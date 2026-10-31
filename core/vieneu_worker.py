"""Private HTTP worker run only by .venv-vieneu Python."""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

if getattr(sys, 'frozen', False):
    os.environ['PATH'] = str(Path(sys.executable).resolve().parent) + os.pathsep + os.environ.get('PATH', '')

try:
    from core.app_paths import DATA_ROOT
except ModuleNotFoundError:  # Direct source invocation: python core/vieneu_worker.py
    from app_paths import DATA_ROOT


def prepare(device):
    """Populate models in the user's cache; never report success before both engines load."""
    directory = DATA_ROOT / '.runtime' / 'vieneu'
    directory.mkdir(parents=True, exist_ok=True)
    os.environ['HF_HOME'] = str(directory / 'models')
    def stage(message):
        if sys.stdout:
            print(message, flush=True)
        (directory / 'setup.json').write_text(json.dumps({'message': message}, ensure_ascii=False), encoding='utf-8')
    stage('Đang kiểm tra VieNeu và thiết bị…')
    if device == 'cuda':
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError('Máy chưa sẵn sàng cho CUDA. Cài gói CPU hoặc kiểm tra driver NVIDIA.')
    from vieneu import Vieneu
    import soundfile as sf
    engine = Vieneu(mode='v3turbo', device=device, backend='onnx' if device == 'cpu' else 'pytorch',
                    max_batch_size=1, max_streams=1)
    voices = engine.list_preset_voices()
    if not voices:
        raise RuntimeError('Không tìm thấy giọng VieNeu.')
    sample = engine.infer('Đây là câu thử giọng Nambeo.', voice=voices[0][1])
    if len(sample) < 4800:
        raise RuntimeError('VieNeu không tạo được âm thanh mẫu.')
    sample_path = directory / 'setup-sample.wav'
    sf.write(sample_path, sample, 48000)
    catalog = [dict(id='vieneu:preset:'+identity, name=label, provider='vieneu',
                    model='v3turbo', lang='vi-VN', gender='neutral', free=True, kind='preset')
               for label, identity in voices]
    (directory / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
    (directory / 'verified.json').write_text(json.dumps({'device': device, 'model_fingerprint': 'local-voices-1'}), encoding='utf-8')
    stage('Đang tải và thử OmniVoice…')
    import torch
    from omnivoice import OmniVoice
    omni = OmniVoice.from_pretrained('k2-fsa/OmniVoice',
                                     device_map='cuda:0' if device == 'cuda' else 'cpu',
                                     dtype=torch.float16 if device == 'cuda' else torch.float32)
    result = omni.generate(text='Đây là giọng thử OmniVoice.', ref_audio=str(sample_path))
    if not result or len(result[0]) < 2400:
        raise RuntimeError('OmniVoice không tạo được âm thanh mẫu.')
    (directory / 'omni-verified.json').write_text(json.dumps({'device': device, 'model': 'k2-fsa/OmniVoice'}), encoding='utf-8')
    stage('VieNeu và OmniVoice đã sẵn sàng.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ready')
    parser.add_argument('--device', choices=['cuda', 'cpu'], default='cuda')
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        prepare(args.device)
        return
    if not args.ready:
        parser.error('--ready là bắt buộc khi chạy worker')
    token = os.environ['NAMBEO_TTS_TOKEN']
    parent = int(os.environ.get('NAMBEO_TTS_PARENT', os.getppid()))
    def watch_parent():
        # Also release GPU memory when the host is forcibly closed (no atexit).
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            api = ctypes.WinDLL('kernel32', use_last_error=True)
            api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            api.OpenProcess.restype = wintypes.HANDLE
            api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = api.OpenProcess(0x00100000, False, parent)
            if not handle:
                os._exit(0)
            api.WaitForSingleObject(handle, 0xFFFFFFFF)
            api.CloseHandle(handle)
            os._exit(0)
        else:
            import time
            while True:
                try:
                    os.kill(parent, 0)
                except ProcessLookupError:
                    os._exit(0)
                time.sleep(1)
    threading.Thread(target=watch_parent, daemon=True).start()
    state = dict(state='idle', device=args.device, message='Sẵn sàng nạp model khi tạo giọng')
    lock = threading.Lock()
    engine = None
    omni_engine = None

    def catalog():
        from importlib.util import find_spec
        path = Path(find_spec('vieneu').origin).parent / 'assets' / 'voices_v3_turbo.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        return [dict(id='vieneu:preset:' + name, name=name + (' · ' + v['description'] if v.get('description') else ''),
                     provider='vieneu', model='v3turbo', gender=v.get('gender', 'neutral'), lang='vi-VN',
                     free=True, kind='preset') for name, v in data['presets'].items()]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, value, audio=False):
            data = value if audio else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'audio/wav' if audio else 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authorized(self):
            if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.reply(403, {'error': 'Forbidden'})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            if self.path == '/health':
                self.reply(200, dict(state))
            elif self.path == '/voices':
                try:
                    self.reply(200, {'voices': catalog()})
                except Exception:
                    self.reply(500, {'error': 'Không đọc được danh mục giọng; hãy cài / sửa VieNeu'})
            else:
                self.reply(404, {})

        def do_POST(self):
            nonlocal engine, omni_engine
            if not self.authorized():
                return
            if self.path == '/shutdown':
                self.reply(200, {'status': 'stopping'})
                # Exit the SDK's background threads and venv launcher together.
                import time
                def finish():
                    time.sleep(.05)
                    os._exit(0)
                threading.Thread(target=finish, daemon=True).start()
                return
            if self.path not in ('/speech', '/omnivoice'):
                return self.reply(404, {})
            with lock:
                try:
                    length = int(self.headers.get('Content-Length', 0))
                    if not 0 < length <= 28_000_000:
                        raise ValueError('Nội dung quá dài')
                    payload = json.loads(self.rfile.read(length))
                    text = payload['text']
                    if not isinstance(text, str) or not text.strip() or len(text) > 10000:
                        raise ValueError('Lời đọc cần từ 1 đến 10000 ký tự')
                    if self.path == '/omnivoice':
                        state.update(state='loading', message='Đang nạp OmniVoice…')
                        if omni_engine is None:
                            engine = None
                            import gc
                            gc.collect()
                            import torch
                            if args.device == 'cuda':
                                torch.cuda.empty_cache()
                            from omnivoice import OmniVoice
                            omni_engine = OmniVoice.from_pretrained('k2-fsa/OmniVoice',
                                device_map='cuda:0' if args.device == 'cuda' else 'cpu',
                                dtype=torch.float16 if args.device == 'cuda' else torch.float32)
                        reference = base64.b64decode(payload.get('reference_wav', ''), validate=True)
                        if not 0 < len(reference) <= 20 * 1024 * 1024:
                            raise ValueError('Mẫu giọng OmniVoice không hợp lệ')
                        with tempfile.NamedTemporaryFile(suffix='.wav', dir=DATA_ROOT, delete=False) as stream:
                            stream.write(reference)
                            reference_path = stream.name
                        try:
                            audio = omni_engine.generate(text=text, ref_audio=reference_path)
                        finally:
                            Path(reference_path).unlink(missing_ok=True)
                        if not audio or not len(audio[0]):
                            raise ValueError('OmniVoice trả về audio trống')
                        import soundfile as sf
                        buffer = io.BytesIO()
                        sf.write(buffer, audio[0], 24000, format='WAV', subtype='PCM_16')
                        state.update(state='ready', message='Sẵn sàng')
                        return self.reply(200, buffer.getvalue(), audio=True)
                    if engine is None:
                        state.update(state='loading', message='Đang nạp / tải model VieNeu lần đầu…')
                        omni_engine = None
                        import gc
                        gc.collect()
                        if args.device == 'cuda':
                            import torch
                            torch.cuda.empty_cache()
                            if not torch.cuda.is_available():
                                raise ValueError('CUDA chưa sẵn sàng. Cài / sửa VieNeu hoặc chọn CPU.')
                        from vieneu import Vieneu
                        engine = Vieneu(mode='v3turbo', device=args.device,
                                        backend='onnx' if args.device == 'cpu' else 'pytorch',
                                        max_batch_size=1, max_streams=1)
                    state.update(state='generating', message='Đang tạo giọng…')
                    if payload.get('reference'):
                        ref = Path(payload['reference']).resolve()
                        allowed = DATA_ROOT / '.runtime' / 'voices'
                        if not ref.is_relative_to(allowed.resolve()) or ref.name != 'sample.wav':
                            raise ValueError('Mẫu giọng không hợp lệ')
                        audio = engine.infer(text, ref_audio=str(ref))
                    else:
                        audio = engine.infer(text, voice=payload['voice'])
                    import soundfile as sf
                    buffer = io.BytesIO()
                    sf.write(buffer, audio, 48000, format='WAV', subtype='PCM_16')
                    state.update(state='ready', message='Sẵn sàng')
                    self.reply(200, buffer.getvalue(), audio=True)
                except Exception as error:
                    import traceback
                    traceback.print_exc()
                    message = str(error) if isinstance(error, ValueError) else 'Tạo giọng thất bại. Kiểm tra nhật ký, bộ nhớ GPU và model.'
                    if 'out of memory' in str(error).lower():
                        message = 'GPU hết VRAM. Đóng ứng dụng dùng GPU hoặc chuyển CPU rồi thử lại.'
                    state.update(state='error', message=message)
                    self.reply(500, {'error': message})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    ready = Path(args.ready)
    ready.write_text(json.dumps({'port': server.server_port}), encoding='utf-8')
    server.serve_forever()


if __name__ == '__main__':
    main()
