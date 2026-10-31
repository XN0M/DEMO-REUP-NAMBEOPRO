"""Start the loopback UI without touching another process's port."""
import ctypes
import asyncio
import json
import os
import subprocess
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from core.app_paths import DATA_ROOT, FROZEN, RESOURCE_ROOT


INSTANCE_FILE = DATA_ROOT / 'instance.json'


def _notice(message):
    if FROZEN and os.name == 'nt':
        ctypes.windll.user32.MessageBoxW(None, str(message), 'Nambeo', 0x10)
    else:
        print(message, file=sys.stderr)


def _claim_instance():
    if os.name != 'nt':
        return None, False
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, 'Local\\Nambeo-App')
    if not handle:
        raise OSError(ctypes.get_last_error(), 'Không tạo được khóa Nambeo')
    return handle, ctypes.get_last_error() == 183


def _running_url():
    try:
        info = json.loads(INSTANCE_FILE.read_text(encoding='utf-8'))
        port = int(info['port'])
        token = str(info['token'])
        if not (1 <= port <= 65535):
            return None
        url = f'http://127.0.0.1:{port}'
        with urllib.request.urlopen(url + '/api/instance/ping?token=' + token, timeout=1) as response:
            return url if response.status == 204 else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _open_when_ready(url):
    for _ in range(100):
        try:
            with urllib.request.urlopen(url + '/', timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(.1)


def _self_test():
    """Exercise bundled native tools and assets without downloading or processing media."""
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '0'
    checks = {}
    def check(name, action):
        try:
            action()
            checks[name] = 'ok'
        except Exception as error:
            checks[name] = f'{type(error).__name__}: {error}'

    def ffmpeg():
        import imageio_ffmpeg
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-version'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=15)
    def browser():
        from playwright.async_api import async_playwright
        async def run():
            async with async_playwright() as playwright:
                instance = await playwright.chromium.launch(headless=True)
                await instance.close()
        asyncio.run(run())
    def whisper():
        from faster_whisper import WhisperModel
        path = RESOURCE_ROOT / 'models' / 'whisper-base'
        if not (path / 'model.bin').is_file():
            raise FileNotFoundError(path)
        WhisperModel(str(path), device='cpu', compute_type='int8')
    check('ffmpeg', ffmpeg)
    check('chromium', browser)
    check('whisper_base', whisper)
    check('web', lambda: (RESOURCE_ROOT / 'web' / 'index.html').read_bytes())
    result = {'ok': all(value == 'ok' for value in checks.values()), 'checks': checks}
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / 'self-test.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['ok'] else 1


def main():
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    if '--self-test' in sys.argv:
        return _self_test()
    mutex, exists = _claim_instance()
    if exists:
        for _ in range(50):
            url = _running_url()
            if url:
                if os.environ.get('NAMBEO_NO_BROWSER') != '1':
                    webbrowser.open(url)
                return
            time.sleep(.1)
        _notice('Nambeo đang khởi động. Hãy thử mở lại sau ít giây.')
        return

    output = None
    sock = None
    token = secrets.token_urlsafe(24)
    try:
        if FROZEN:
            output = (DATA_ROOT / 'nambeo.log').open('a', encoding='utf-8', buffering=1)
            sys.stdout = sys.stderr = output
        os.environ['NAMBEO_INSTANCE_TOKEN'] = token
        if FROZEN:
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '0'
        os.chdir(RESOURCE_ROOT)
        from server import app

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(('127.0.0.1', 8000))
        except OSError:
            sock.bind(('127.0.0.1', 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        INSTANCE_FILE.write_text(json.dumps({'port': port, 'token': token}), encoding='utf-8')
        url = f'http://127.0.0.1:{port}'
        if os.environ.get('NAMBEO_NO_BROWSER') != '1':
            threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
        service = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='info'))
        service.run(sockets=[sock])
        return 0 if service.started else 1
    except Exception as error:
        import traceback
        traceback.print_exc()
        _notice(f'Không khởi động được Nambeo: {error}\nXem nhật ký: {DATA_ROOT / "nambeo.log"}')
        return 1
    finally:
        if sock:
            sock.close()
        try:
            if INSTANCE_FILE.exists() and json.loads(INSTANCE_FILE.read_text(encoding='utf-8')).get('token') == token:
                INSTANCE_FILE.unlink()
        except (OSError, ValueError):
            pass
        if mutex:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mutex))
        if output:
            output.close()


if __name__ == '__main__':
    sys.exit(main() or 0)
