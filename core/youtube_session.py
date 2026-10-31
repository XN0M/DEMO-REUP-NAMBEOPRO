"""Local-only YouTube sessions; plaintext cookies never leave memory."""
import copy
import csv
import ctypes
import io
import json
import os
import re
import subprocess
import stat
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from http.cookiejar import Cookie
from pathlib import Path
from urllib.parse import urlsplit

from yt_dlp.cookies import YoutubeDLCookieJar
from .app_paths import DATA_ROOT

MAX_BYTES = 2 * 1024 * 1024
AUTH_CODES = {'youtube_auth_required', 'youtube_session_invalid'}


class YouTubeError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def youtube_host(url):
    try:
        parsed = urlsplit(url.strip())
        host = (parsed.hostname or '').lower()
        return parsed.scheme in ('http', 'https') and not parsed.username and (
            host == 'youtu.be' or host == 'youtube.com' or host.endswith('.youtube.com'))
    except (ValueError, AttributeError):
        return False


def classify(error):
    if isinstance(error, YouTubeError):
        return error
    # Inspect privately, but never return an upstream payload, header or URL.
    text = str(error).lower()
    if ('sign in to confirm' in text and 'bot' in text) or 'captcha' in text:
        return YouTubeError('youtube_auth_required', 'YouTube yêu cầu đăng nhập/xác minh lượt truy cập. '
            'Mở video trong trình duyệt, hoàn tất xác minh và nhập lại cookie trong Cài đặt → Kết nối YouTube.')
    if any(s in text for s in ('cookies are no longer valid', 'cookies have expired', 'cookie load', 'failed to decrypt')):
        return YouTubeError('youtube_session_invalid', 'Phiên YouTube không còn dùng được. Hãy xuất và nhập lại cookie.')
    if any(s in text for s in ('private video', 'members-only', 'not available in your country',
                              'age-restricted', 'confirm your age', 'video unavailable', 'has been removed', 'copyright')):
        return YouTubeError('youtube_restricted', 'Video YouTube không khả dụng hoặc phiên hiện tại chưa có quyền xem video này.')
    if any(s in text for s in ('timed out', 'timeout', 'getaddrinfo', 'network', 'connection',
                              'temporary failure', 'http error 5', 'remote end closed')):
        return YouTubeError('youtube_network', 'Không kết nối được tới YouTube. Kiểm tra mạng rồi thử lại.')
    return YouTubeError('youtube_extract_failed', 'YouTube chưa cung cấp dữ liệu/định dạng video hợp lệ. '
        'Kiểm tra video trong trình duyệt và phiên YouTube; không có dữ liệu đăng nhập nào được ghi vào thông báo lỗi.')


class QuietLogger:
    def __init__(self):
        self.invalid_session = False
    def debug(self, *_args, **_kwargs): pass
    def warning(self, message, *_args, **_kwargs):
        if any(part in str(message).lower() for part in ('cookies are no longer valid', 'cookies have expired')):
            self.invalid_session = True
    def error(self, *_args, **_kwargs): pass


def parse_cookies(content):
    if len(content) > MAX_BYTES:
        raise YouTubeError('cookie_too_large', 'Tệp cookie tối đa 2 MB.')
    try:
        text = content.decode('utf-8-sig')
        lines = text.splitlines()
        if not lines or lines[0].strip() not in ('# Netscape HTTP Cookie File', '# HTTP Cookie File'):
            raise ValueError()
        cookies = []
        for line in lines[1:]:
            http_only = line.startswith('#HttpOnly_')
            if http_only:
                line = line[len('#HttpOnly_'):]
            elif not line or line.startswith('#'):
                continue
            fields = line.split('\t')
            if len(fields) != 7:
                raise ValueError()
            domain, subdomains, path, secure, expiry, name, value = fields
            host = domain.lstrip('.').lower()
            if host != 'youtube.com' and not host.endswith('.youtube.com'):
                continue
            if (not re.fullmatch(r'(?:[a-z0-9-]+\.)*youtube\.com', host)
                    or subdomains not in ('TRUE', 'FALSE') or secure not in ('TRUE', 'FALSE')
                    or not expiry.isdigit() or not path.startswith('/')
                    or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                    or any(ord(c) < 32 or ord(c) == 127 for c in value)):
                raise ValueError()
            expires = int(expiry)
            if expires and expires <= time.time():
                continue
            cookies.append(dict(domain=('.' if subdomains == 'TRUE' else '') + host,
                path=path, secure=secure == 'TRUE', expires=expires or None,
                name=name, value=value, http_only=http_only))
        if not cookies:
            raise YouTubeError('youtube_session_invalid', 'Tệp không có cookie YouTube còn hiệu lực. Hãy xuất lại riêng cookie youtube.com.')
        return cookies
    except (ValueError, UnicodeError, OverflowError):
        raise YouTubeError('cookie_format', 'Tệp cookie không đúng định dạng Netscape. Hãy xuất lại cookie youtube.com.') from None


def cookiejar(cookies):
    jar = YoutubeDLCookieJar()
    for item in cookies:
        jar.set_cookie(Cookie(0, item['name'], item['value'], None, False,
            item['domain'], item['domain'].startswith('.'), item['domain'].startswith('.'),
            item['path'], True, item['secure'], item['expires'], item['expires'] is None,
            None, None, {'HttpOnly': None} if item['http_only'] else {}, False))
    return jar


def dpapi(data, decrypt=False):
    if os.name != 'nt':
        raise YouTubeError('session_storage', 'Lưu phiên được mã hóa hiện chỉ hỗ trợ Windows.')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypto = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    if decrypt:
        action = crypto.CryptUnprotectData
        action.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = action(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        action = crypto.CryptProtectData
        action.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = action(ctypes.byref(source), 'Nambeo YouTube', None, None, None, 1, ctypes.byref(output))
    if not ok:
        raise YouTubeError('youtube_session_invalid', 'Không mở/lưu được phiên YouTube cho tài khoản Windows hiện tại. Hãy nhập lại phiên.')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def private_acl(path):
    if os.name != 'nt':
        raise YouTubeError('session_storage', 'Lưu phiên được mã hóa hiện chỉ hỗ trợ Windows.')
    try:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        result = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True,
                                check=True, timeout=10, creationflags=flags)
        sid = next(csv.reader(io.StringIO(result.stdout.decode(errors='replace'))))[-1].strip()
        if not re.fullmatch(r'S-1-\d+(?:-\d+)+', sid):
            raise ValueError()
        subprocess.run(['icacls', str(path), '/reset'], capture_output=True,
                       check=True, timeout=10, creationflags=flags)
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                        f'*{sid}:(F)', '*S-1-5-18:(F)'], capture_output=True,
                       check=True, timeout=10, creationflags=flags)
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
        raise YouTubeError('session_storage', 'Không thiết lập được quyền riêng tư cho tệp phiên YouTube. Phiên cũ được giữ nguyên.') from None


class SessionStore:
    def __init__(self, root=None):
        self.root = Path(root or DATA_ROOT / '.runtime' / 'youtube')
        self.lock = threading.RLock()
        self.guest_version = 'guest:' + uuid.uuid4().hex

    def safe_paths(self):
        for path in (self.root / 'session.bin', self.root / 'guest-check.json', self.root, *self.root.parents):
            try:
                if path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise YouTubeError('session_storage', 'Đường dẫn lưu phiên chứa liên kết thư mục; hãy dùng thư mục dữ liệu thực.')
            except FileNotFoundError:
                pass
            except AttributeError:
                if path.is_symlink():
                    raise YouTubeError('session_storage', 'Đường dẫn lưu phiên không hợp lệ.')

    def read(self):
        try:
            self.safe_paths()
            record = json.loads(dpapi((self.root / 'session.bin').read_bytes(), decrypt=True))
            if (not isinstance(record, dict) or not isinstance(record.get('cookies'), list)
                    or not isinstance(record.get('version'), str) or type(record.get('enabled')) is not bool
                    or not isinstance(record.get('imported_at'), (float, int))):
                raise ValueError()
            return record
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError):
            raise YouTubeError('youtube_session_invalid', 'Không đọc được phiên YouTube. Hãy nhập lại hoặc xóa phiên đã lưu.') from None

    def write(self, record):
        payload = dpapi(json.dumps(record, ensure_ascii=False).encode())
        self.root.mkdir(parents=True, exist_ok=True)
        self.safe_paths()
        private_acl(self.root)
        temporary = self.root / (uuid.uuid4().hex + '.bin')
        try:
            with temporary.open('xb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            private_acl(temporary)
            os.replace(temporary, self.root / 'session.bin')
        finally:
            temporary.unlink(missing_ok=True)

    def import_file(self, content):
        cookies = parse_cookies(content)
        with self.lock:
            self.write(dict(version=uuid.uuid4().hex, cookies=cookies, enabled=True,
                            imported_at=time.time(), last_check=None))
        return self.status()

    def snapshot(self):
        with self.lock:
            record = self.read()
            if not record or not record['enabled']:
                return {'version': record['version'] if record else self.guest_version, 'cookies': []}
            cookies = [c for c in record['cookies'] if not c['expires'] or c['expires'] > time.time()]
            if not cookies:
                raise YouTubeError('youtube_session_invalid', 'Phiên YouTube đã hết hạn. Hãy nhập lại cookie.')
            return {'version': record['version'], 'cookies': copy.deepcopy(cookies)}

    def status(self):
        with self.lock:
            try:
                record = self.read()
            except YouTubeError:
                return dict(stored=True, enabled=False, version=None, last_check=None,
                            storage_error='Không mở được phiên. Hãy nhập lại hoặc xóa phiên.')
            last = record.get('last_check') if record else None
            if not record:
                try:
                    last = json.loads((self.root / 'guest-check.json').read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    pass
            return dict(stored=bool(record), enabled=bool(record and record['enabled']),
                        version=record['version'] if record else None,
                        imported_at=record['imported_at'] if record else None,
                        last_check=last)

    def enable(self, enabled):
        with self.lock:
            record = self.read()
            if not record:
                raise YouTubeError('youtube_session_invalid', 'Chưa có phiên YouTube. Hãy nhập tệp cookie trước.')
            record['enabled'] = enabled
            record['version'] = uuid.uuid4().hex
            record['last_check'] = None
            self.write(record)
        return self.status()

    def record_check(self, version, outcome):
        with self.lock:
            record = self.read()
            if record and record['version'] == version:
                record['last_check'] = dict(outcome, checked_at=time.time())
                self.write(record)
                return True
            elif record is None and version == self.guest_version:
                self.root.mkdir(parents=True, exist_ok=True)
                private_acl(self.root)
                temporary = self.root / (uuid.uuid4().hex + '.json')
                try:
                    temporary.write_text(json.dumps(dict(outcome, checked_at=time.time()), ensure_ascii=False), encoding='utf-8')
                    private_acl(temporary)
                    os.replace(temporary, self.root / 'guest-check.json')
                finally:
                    temporary.unlink(missing_ok=True)
                return True
            return False

    def delete(self):
        with self.lock:
            self.safe_paths()
            (self.root / 'session.bin').unlink(missing_ok=True)
            (self.root / 'guest-check.json').unlink(missing_ok=True)
            self.guest_version = 'guest:' + uuid.uuid4().hex


class YouTubeQueue:
    """FIFO lease shared by metadata checks and downloads, outside the event loop."""
    def __init__(self, interval=5):
        self.condition = threading.Condition()
        self.tickets = deque()
        self.active = False
        self.last_end = 0
        self.interval = interval
        self.paused = False
        self.generation = 0
        self.halt_epoch = 0

    def resume(self):
        with self.condition:
            self.paused = False
            self.generation += 1
            self.condition.notify_all()

    @contextmanager
    def lease(self, progress=None, check=False):
        ticket = object()
        with self.condition:
            epoch = self.halt_epoch
            self.tickets.append(ticket)
            try:
                while True:
                    if (self.paused or epoch != self.halt_epoch) and not check:
                        raise YouTubeError('youtube_paused', 'Hàng đợi YouTube đang tạm dừng. Kiểm tra/nhập lại phiên rồi bấm Tiếp tục YouTube.')
                    delay = self.interval - (time.monotonic() - self.last_end)
                    if not self.active and self.tickets[0] is ticket and delay <= 0:
                        break
                    if progress:
                        progress({'status': 'waiting', 'speed': 'Đang chờ lượt YouTube'})
                    self.condition.wait(timeout=max(.1, min(delay, 1)) if delay > 0 else 1)
                self.tickets.popleft()
                self.active = True
                generation = self.generation
            except BaseException:
                self.tickets.remove(ticket)
                self.condition.notify_all()
                raise
        try:
            yield generation
        except YouTubeError as error:
            if error.code in AUTH_CODES:
                with self.condition:
                    if generation == self.generation:
                        self.paused = True
                        self.halt_epoch += 1
            raise
        finally:
            with self.condition:
                self.active = False
                self.last_end = time.monotonic()
                self.condition.notify_all()


session_store = SessionStore()
youtube_queue = YouTubeQueue()
