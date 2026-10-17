"""Local voice library and isolated VieNeu process. No torch in the host app."""
import atexit
import io
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
import wave
from contextlib import contextmanager
import httpx
import imageio_ffmpeg

BASE = Path(__file__).resolve().parent.parent
ENGINE = 'vieneu-3.8.3-v3turbo-1'
GPU_LOCK = threading.RLock()


class TTSError(ValueError):
    """Actionable, safe to display to the user."""


class ProviderUnavailable(TTSError):
    """A provider-wide failure: skip its remaining network requests in this job."""
    def __init__(self, provider, message):
        super().__init__(message)
        self.provider = provider


@contextmanager
def gpu_slot(cancelled=lambda: False):
    while not GPU_LOCK.acquire(timeout=.1):
        if cancelled():
            raise TTSError('Đã hủy tạo giọng')
    try:
        if cancelled():
            raise TTSError('Đã hủy tạo giọng')
        yield
    finally:
        GPU_LOCK.release()


class VoiceLibrary:
    def __init__(self, root=None):
        self.root = Path(root or BASE / '.runtime' / 'voices')
        self.lock = threading.RLock()
        self.uses = {}

    def path(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise TTSError('ID giọng không hợp lệ')
        target = self.root / identity
        if not target.resolve().is_relative_to(self.root.resolve()):
            raise TTSError('Đường dẫn giọng không hợp lệ')
        return target

    def get(self, identity):
        with self.lock:
            try:
                return json.loads((self.path(identity) / 'voice.json').read_text(encoding='utf-8'))
            except FileNotFoundError:
                raise TTSError('Thiếu giọng cá nhân. Vui lòng chọn lại giọng.') from None

    def list(self):
        with self.lock:
            return [self.get(p.name) for p in sorted(self.root.glob('*')) if (p / 'voice.json').is_file()]

    def sample(self, identity):
        self.get(identity)
        sample = (self.path(identity) / 'sample.wav').resolve()
        if not sample.is_relative_to(self.root.resolve()) or not sample.is_file():
            raise TTSError('Mẫu giọng không tồn tại hoặc đường dẫn không hợp lệ')
        return sample

    @staticmethod
    def name(value):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
            raise TTSError('Tên giọng cần từ 1 đến 80 ký tự')
        return value.strip()

    def create(self, name, filename, content, consent):
        name = self.name(name)
        if consent is not True:
            raise TTSError('Cần xác nhận quyền sử dụng giọng mẫu')
        suffix = Path(filename).suffix.lower()
        if suffix not in ('.wav', '.mp3', '.m4a', '.flac'):
            raise TTSError('Chọn file WAV, MP3, M4A hoặc FLAC')
        if not content or len(content) > 20 * 1024 * 1024:
            raise TTSError('File mẫu phải có dữ liệu và không quá 20 MB')
        identity = uuid.uuid4().hex
        directory = self.path(identity)
        directory.mkdir(parents=True)
        try:
            source = directory / ('source' + suffix)
            source.write_bytes(content)
            result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe',
                '-i', str(source), '-vn', '-t', '8.1', '-ac', '1', '-ar', '48000',
                '-c:a', 'pcm_s16le', str(directory / 'sample.wav')], capture_output=True, timeout=30)
            if result.returncode:
                raise TTSError('Không đọc được âm thanh trong file mẫu')
            with wave.open(str(directory / 'sample.wav'), 'rb') as audio:
                duration = audio.getnframes() / audio.getframerate()
                frames = audio.readframes(audio.getnframes())
            if not 3 <= duration <= 8.001:
                raise TTSError('Mẫu giọng phải dài từ 3 đến 8 giây; hãy cắt mẫu rồi tải lại')
            import numpy as np
            if float(np.max(np.abs(np.frombuffer(frames, dtype='<i2').astype(float)))) < 100:
                raise TTSError('Mẫu giọng quá nhỏ hoặc không có tiếng')
            source.unlink()
            data = dict(id=identity, name=name, sha256=hashlib.sha256((directory / 'sample.wav').read_bytes()).hexdigest(),
                        duration=round(duration, 3), consent=True, created=time.time())
            self._save(directory, data)
            return data
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def _save(self, directory, data):
        temporary = directory / 'voice.tmp'
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, directory / 'voice.json')

    def rename(self, identity, name):
        with self.lock:
            data = self.get(identity)
            data['name'] = self.name(name)
            self._save(self.path(identity), data)
            return data

    def delete(self, identity):
        with self.lock:
            self.get(identity)
            if self.uses.get(identity):
                raise TTSError('Giọng đang được job sử dụng; hãy chờ job kết thúc')
            shutil.rmtree(self.path(identity))

    @contextmanager
    def pin(self, voices):
        ids = {v.split(':', 2)[2] for v in voices if v.startswith('vieneu:custom:')}
        with self.lock:
            for identity in ids:
                self.get(identity)
            for identity in ids:
                self.uses[identity] = self.uses.get(identity, 0) + 1
        try:
            yield
        finally:
            with self.lock:
                for identity in ids:
                    self.uses[identity] -= 1

    def fingerprint(self, voice):
        engine = ENGINE
        verified = BASE / '.runtime' / 'vieneu' / 'verified.json'
        if verified.exists():
            try:
                engine += ':' + json.loads(verified.read_text(encoding='utf-8')).get('model_fingerprint', '')
            except (OSError, ValueError):
                pass
        if voice.startswith('vieneu:custom:'):
            try:
                return engine + ':' + self.get(voice.split(':', 2)[2])['sha256']
            except TTSError:
                return engine + ':missing'
        return engine


class VieneuRuntime:
    def __init__(self, root=BASE):
        self.root = Path(root)
        self.directory = self.root / '.runtime' / 'vieneu'
        self.process = None
        self.connection = None
        self.device = 'cuda'
        settings = self.directory / 'settings.json'
        if settings.exists():
            try:
                self.device = json.loads(settings.read_text())['device']
            except (OSError, ValueError, KeyError):
                pass
        if self.device not in ('cpu', 'cuda'):
            self.device = 'cuda'
        self.state = 'stopped'
        self.error = ''
        self.last_used = time.monotonic()
        self.install_process = None
        self.gpu_release = None
        self.control = threading.RLock()
        self._log = None
        atexit.register(self.stop)
        threading.Thread(target=self._idle, daemon=True).start()

    @property
    def python(self):
        return self.root / '.venv-vieneu' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

    def _idle(self):
        while True:
            time.sleep(15)
            if time.monotonic() - self.last_used > 300 and GPU_LOCK.acquire(blocking=False):
                try:
                    self.stop()
                finally:
                    GPU_LOCK.release()

    def stop(self):
        with self.control:
            # A Windows venv launcher can outlive/be outlived by its Python child.
            # Ask the worker itself to exit before terminating the launcher.
            if self.connection:
                try:
                    self.request('POST', '/shutdown', {}, timeout=2)
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        try:
                            self.request('GET', '/health', timeout=.2)
                            time.sleep(.05)
                        except Exception:
                            break
                except Exception:
                    pass
            if self.process:
                if self.process.poll() is None:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    else:
                        self.process.terminate()
                    try:
                        self.process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
                self.process = None
            self.connection = None
            if self._log:
                self._log.close()
                self._log = None
            if self.state != 'error':
                self.state = 'stopped'

    def status(self):
        if self.install_process:
            code = self.install_process.poll()
            if code is None:
                setup = self.directory / 'setup.json'
                try:
                    message = json.loads(setup.read_text(encoding='utf-8'))['message']
                except (OSError, ValueError, KeyError):
                    message = 'Đang cài VieNeu và tải model…'
                return dict(state='installing', device=self.device, message=message)
            self.install_process = None
            if code:
                self.state, self.error = 'error', 'Cài VieNeu thất bại. Xem nhật ký rồi thử lại.'
        if not self.python.exists():
            return dict(state='not_installed', device=self.device, message='Chưa cài VieNeu. Chọn Cài / sửa VieNeu.')
        if self.process and self.process.poll() is not None:
            self.state, self.error = 'error', 'Worker VieNeu đã dừng. Kiểm tra nhật ký và thử lại.'
        result = dict(state=self.state, device=self.device, message=self.error)
        if self.connection and self.process and self.process.poll() is None:
            try:
                result.update(self.request('GET', '/health', timeout=2))
            except Exception:
                pass
        return result

    def install(self):
        with GPU_LOCK, self.control:
            if self.install_process and self.install_process.poll() is None:
                return self.status()
            self.stop()
            if self.gpu_release:
                self.gpu_release()
            self.directory.mkdir(parents=True, exist_ok=True)
            import sys
            with open(self.directory / 'install.log', 'w', encoding='utf-8') as log:
                self.install_process = subprocess.Popen([sys.executable, str(self.root / 'scripts' / 'setup_vieneu.py'), '--device', self.device],
                    cwd=self.root, stdout=log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.error = ''
            return self.status()

    def set_device(self, device):
        if device not in ('cuda', 'cpu'):
            raise TTSError('Thiết bị phải là cuda hoặc cpu')
        with GPU_LOCK:
            self.stop()
            self.device, self.state = device, 'stopped'
            self.error = 'Nếu dùng thiết bị này lần đầu, chọn Cài / sửa VieNeu để tải model phù hợp.'
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / 'settings.json').write_text(json.dumps({'device': device}))

    def start(self):
        with self.control:
            if self.process and self.process.poll() is None and self.connection:
                return
            if self.install_process and self.install_process.poll() is None:
                raise TTSError('Đang cài VieNeu; hãy chờ hoàn tất')
            if not self.python.exists():
                raise TTSError('Chưa cài VieNeu. Mở Giọng cá nhân → Cài / sửa VieNeu.')
            self.stop()
            self.state, self.error = 'starting', ''
            self.directory.mkdir(parents=True, exist_ok=True)
            ready = self.directory / (uuid.uuid4().hex + '.json')
            token = secrets.token_urlsafe(32)
            env = dict(os.environ, NAMBEO_TTS_TOKEN=token, NAMBEO_TTS_PARENT=str(os.getpid()), HF_HOME=str(self.directory / 'models'),
                       PYTHONIOENCODING='utf-8')
            if (self.directory / 'verified.json').exists():
                env['HF_HUB_OFFLINE'] = '1'
            self._log = open(self.directory / 'worker.log', 'a', encoding='utf-8')
            self.process = subprocess.Popen([str(self.python), str(self.root / 'core' / 'vieneu_worker.py'),
                '--ready', str(ready), '--device', self.device], cwd=self.root, env=env,
                stdout=self._log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and self.process.poll() is None:
                if ready.exists():
                    self.connection = dict(json.loads(ready.read_text()), token=token)
                    ready.unlink(missing_ok=True)
                    self.last_used = time.monotonic()
                    return
                time.sleep(.1)
            self.stop()
            self.state, self.error = 'error', 'Không khởi động được VieNeu. Xem nhật ký worker.'
            raise TTSError(self.error)

    def request(self, method, path, payload=None, timeout=600):
        if not self.connection:
            raise TTSError('Worker chưa sẵn sàng')
        try:
            with httpx.Client(trust_env=False, timeout=timeout) as client:
                response = client.request(method, f'http://127.0.0.1:{self.connection["port"]}{path}',
                    headers={'Authorization': 'Bearer ' + self.connection['token']}, json=payload)
            if response.status_code != 200:
                raise TTSError(response.json().get('error', 'Lỗi tạo giọng VieNeu'))
            return response.content if path == '/speech' else response.json()
        except httpx.HTTPError as error:
            raise TTSError('Mất kết nối hoặc quá thời gian tạo giọng VieNeu. Kiểm tra nhật ký rồi thử lại.') from error

    def catalog(self, load=False):
        # Catalog is read from packaged assets by the worker, without loading a model.
        cache = self.directory / 'catalog.json'
        if cache.exists():
            try:
                return json.loads(cache.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
        if load:
            with GPU_LOCK:
                self.start()
        if self.connection:
            return self.request('GET', '/voices', timeout=10)['voices']
        return []

    def synthesize(self, text, voice, cancelled=lambda: False, refresh=False):
        text = text.strip()
        if not text or len(text) > 10000:
            raise TTSError('Lời đọc cần từ 1 đến 10000 ký tự')
        with library.pin([voice]):
            if cancelled():
                raise TTSError('Đã hủy tạo giọng')
            self.last_used = time.monotonic()
            digest = hashlib.sha256(json.dumps([text, voice, library.fingerprint(voice)], ensure_ascii=False).encode()).hexdigest()
            cache = self.directory / 'audio' / (digest + '.wav')
            def cached():
                if cache.exists():
                    content = cache.read_bytes()
                    try:
                        with wave.open(io.BytesIO(content), 'rb') as audio:
                            if audio.getnframes() > 0 and audio.getframerate() == 48000:
                                return content
                    except (wave.Error, EOFError):
                        pass
                return None
            result = None if refresh else cached()
            if result:
                return result
            with gpu_slot(cancelled):
                result = None if refresh else cached()
                if result:
                    return result
                if self.gpu_release:
                    self.gpu_release()
                self.start()
                reference = None
                if voice.startswith('vieneu:custom:'):
                    reference = str(library.sample(voice.split(':', 2)[2]))
                    preset = None
                elif voice.startswith('vieneu:preset:'):
                    preset = voice.split(':', 2)[2]
                    if preset not in {v['id'].split(':', 2)[2] for v in self.catalog()}:
                        raise TTSError('Giọng VieNeu không tồn tại. Hãy chọn lại giọng.')
                else:
                    raise TTSError('ID giọng VieNeu không hợp lệ')
                try:
                    result = self.request('POST', '/speech', dict(text=text, voice=preset, reference=reference))
                    if cancelled():
                        raise TTSError('Đã hủy tạo giọng')
                    with wave.open(io.BytesIO(result), 'rb') as audio:
                        if audio.getnframes() == 0 or audio.getframerate() != 48000:
                            raise TTSError('VieNeu trả về audio trống hoặc sai định dạng')
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    temporary = cache.with_suffix('.tmp')
                    temporary.write_bytes(result)
                    os.replace(temporary, cache)
                    return result
                finally:
                    self.last_used = time.monotonic()


library = VoiceLibrary()
runtime = VieneuRuntime()
