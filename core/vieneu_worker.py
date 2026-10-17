"""Private HTTP worker run only by .venv-vieneu Python."""
import argparse
import io
import json
import os
from pathlib import Path
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ready', required=True)
    parser.add_argument('--device', choices=['cuda', 'cpu'], default='cuda')
    args = parser.parse_args()
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
            nonlocal engine
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
            if self.path != '/speech':
                return self.reply(404, {})
            with lock:
                try:
                    length = int(self.headers.get('Content-Length', 0))
                    if not 0 < length <= 100_000:
                        raise ValueError('Nội dung quá dài')
                    payload = json.loads(self.rfile.read(length))
                    text = payload['text']
                    if not isinstance(text, str) or not text.strip() or len(text) > 10000:
                        raise ValueError('Lời đọc cần từ 1 đến 10000 ký tự')
                    if engine is None:
                        state.update(state='loading', message='Đang nạp / tải model VieNeu lần đầu…')
                        if args.device == 'cuda':
                            import torch
                            if not torch.cuda.is_available():
                                raise ValueError('CUDA chưa sẵn sàng. Cài / sửa VieNeu hoặc chọn CPU.')
                        from vieneu import Vieneu
                        engine = Vieneu(mode='v3turbo', device=args.device,
                                        backend='onnx' if args.device == 'cpu' else 'pytorch',
                                        max_batch_size=1, max_streams=1)
                    state.update(state='generating', message='Đang tạo giọng…')
                    if payload.get('reference'):
                        ref = Path(payload['reference']).resolve()
                        allowed = Path(__file__).resolve().parent.parent / '.runtime' / 'voices'
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
