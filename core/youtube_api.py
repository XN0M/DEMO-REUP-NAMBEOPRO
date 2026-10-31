"""Same-origin, loopback-only controls for sensitive YouTube session data."""
import asyncio
import ipaddress
import secrets
import threading
import time
import uuid
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, StrictBool
from .youtube_session import session_store, youtube_queue, YouTubeError, MAX_BYTES, youtube_host, classify
from .extractor_yt import YouTubeExtractor


class SessionChange(BaseModel):
    enabled: StrictBool | None = None
    resume: StrictBool = False


class SessionCheck(BaseModel):
    url: str


def require_local_origin(request):
    try:
        local = ipaddress.ip_address(request.client.host).is_loopback
        origin = request.headers.get('origin')
        referer = request.headers.get('referer')
        parsed = urlsplit(referer) if referer else None
        referer_origin = f'{parsed.scheme}://{parsed.netloc}' if parsed else None
    except (ValueError, AttributeError):
        raise HTTPException(403, 'Yêu cầu phải đến từ giao diện Nambeo trên máy này.') from None
    if not local or request.url.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise HTTPException(403, 'Phiên YouTube chỉ được dùng trên máy chạy Nambeo.')
    expected = f'{request.url.scheme}://{request.url.netloc}'
    if ((origin and origin != expected) or (referer and referer_origin != expected)
            or request.headers.get('sec-fetch-site') in ('cross-site', 'same-site')
            or not (origin == expected or referer_origin == expected or request.headers.get('sec-fetch-site') == 'same-origin')):
        raise HTTPException(403, 'Hãy dùng chức năng này từ giao diện Nambeo trên cùng địa chỉ.')


def guard_session_use(request):
    # Guard even guest requests: a queued request may execute after a session is imported.
    require_local_origin(request)


def attach(host):
    router = APIRouter(prefix='/api/youtube/session')
    csrf = secrets.token_urlsafe(32)
    jobs = {}
    lock = threading.RLock()

    def guard(request, mutate=False):
        require_local_origin(request)
        if mutate and not secrets.compare_digest(request.headers.get('x-nambeo-csrf', ''), csrf):
            raise HTTPException(403, 'Phiên giao diện đã đổi. Tải lại cài đặt YouTube rồi thử lại.')

    def status():
        return {**session_store.status(), 'csrf_token': csrf, 'paused': youtube_queue.paused,
                'queued': len(youtube_queue.tickets), 'active': youtube_queue.active}

    def public_error(error):
        raise HTTPException(400, dict(code=error.code, message=str(error)))

    @router.get('')
    def get_session(request: Request, response: Response):
        guard(request)
        response.headers['Cache-Control'] = 'no-store'
        return status()

    @router.post('')
    async def import_session(request: Request):
        guard(request, True)
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_BYTES:
                raise HTTPException(413, dict(code='cookie_too_large', message='Tệp cookie tối đa 2 MB.'))
        try:
            await asyncio.to_thread(session_store.import_file, bytes(content))
            # Pending requests have already stopped; only explicit retries start again.
            youtube_queue.resume()
            return status()
        except YouTubeError as error:
            public_error(error)
        except OSError:
            raise HTTPException(400, 'Không lưu được phiên. Kiểm tra dung lượng và quyền ghi.') from None
        finally:
            content.clear()

    @router.put('')
    def change_session(req: SessionChange, request: Request):
        guard(request, True)
        try:
            if req.enabled is not None:
                session_store.enable(req.enabled)
            if req.resume:
                youtube_queue.resume()
            return status()
        except YouTubeError as error:
            public_error(error)
        except OSError:
            raise HTTPException(400, 'Không cập nhật được phiên đã lưu.') from None

    @router.delete('')
    def delete_session(request: Request):
        guard(request, True)
        try:
            session_store.delete()
        except OSError:
            raise HTTPException(400, 'Không xóa được tệp phiên. Kiểm tra quyền truy cập.') from None
        return status()

    def check_worker(identity, url):
        version = None
        def captured(value):
            nonlocal version
            version = value
            with lock:
                jobs[identity]['status'] = 'running'
        try:
            info = YouTubeExtractor().parse_single_url_sync(url, check=True, on_snapshot=captured)
            if not info:
                raise YouTubeError('youtube_extract_failed', 'Không đọc được thông tin video.')
            outcome = dict(state='accessible', code=None,
                message='Truy cập được metadata của video vừa kiểm tra; chưa xác nhận tải media.')
        except Exception as error:
            safe = classify(error)
            outcome = dict(state='network_error' if safe.code == 'youtube_network' else
                'verification_required' if safe.code in ('youtube_auth_required', 'youtube_session_invalid') else 'error',
                code=safe.code, message=str(safe))
        try:
            current = session_store.record_check(version, outcome)
        except (YouTubeError, OSError):
            # A deleted/replaced session or full disk must not change the result.
            current = False
        outcome['current_session'] = current
        if not current:
            outcome['message'] = 'Kết quả chưa được lưu cho phiên hiện tại (phiên đã đổi hoặc không ghi được dữ liệu). ' + outcome['message']
        with lock:
            jobs[identity].update(status='completed', result=outcome, finished_at=time.time())

    @router.post('/check')
    def check_session(req: SessionCheck, request: Request):
        guard(request, True)
        if len(req.url) > 4096 or not youtube_host(req.url):
            raise HTTPException(400, 'Nhập một đường dẫn video YouTube hợp lệ.')
        try:
            YouTubeExtractor.clean_youtube_url(req.url)
        except YouTubeError as error:
            public_error(error)
        with lock:
            if any(job['status'] in ('waiting', 'running') for job in jobs.values()):
                raise HTTPException(409, 'Một lượt kiểm tra đang chạy. Hãy chờ kết quả.')
            while len(jobs) >= 100:
                jobs.pop(next(iter(jobs)))
            identity = uuid.uuid4().hex
            jobs[identity] = dict(id=identity, status='waiting', created_at=time.time())
            threading.Thread(target=check_worker, args=(identity, req.url), daemon=True).start()
            return dict(jobs[identity])

    @router.get('/check/{identity}')
    def check_result(identity: str, request: Request, response: Response):
        guard(request)
        response.headers['Cache-Control'] = 'no-store'
        with lock:
            if identity not in jobs:
                raise HTTPException(404, 'Không tìm thấy lượt kiểm tra; máy chủ có thể vừa khởi động lại.')
            return dict(jobs[identity])

    host.app.include_router(router)
