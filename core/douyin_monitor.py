"""Read public Douyin profile posts in an isolated, anonymous Chromium context."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import threading
import time
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx

from core.monitor_errors import MonitorError, ScanResult

_browser_lock = threading.Lock()
_PROFILE = re.compile(r'^/(?:user|share/user)/([A-Za-z0-9_-]+)/?$')
_ALLOWED = ('douyin.com', 'iesdouyin.com')


def _allowed(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
        return (parsed.scheme in ('https', 'http') and not parsed.username and not parsed.password
                and parsed.port in (None, 80, 443)
                and any(host == domain or host.endswith('.' + domain) for domain in _ALLOWED))
    except ValueError:
        return False


def _profile(url):
    if not _allowed(url):
        return None
    parsed = urlsplit(url)
    match = _PROFILE.fullmatch(parsed.path)
    if match:
        uid = (parse_qs(parsed.query).get('sec_uid') or [match.group(1)])[0]
        if not re.fullmatch(r'[A-Za-z0-9_-]+', uid):
            return None
        if parsed.path.startswith('/share/user/') and uid.isdigit():
            return None  # Numeric legacy user IDs must first resolve to a sec_uid.
        return 'https://www.douyin.com/user/' + uid, uid
    return None


def resolve_profile(raw_url):
    match = re.search(r'https?://[^\s<>"\u3000\[\]\(\)]+', raw_url or '')
    if not match:
        raise MonitorError('douyin_profile_url', 'Nhập link hồ sơ kênh Douyin công khai.', 422)
    url = match.group(0).rstrip('.,;!，。；！)）')
    if not _allowed(url):
        raise MonitorError('douyin_profile_url', 'Đây không phải link kênh Douyin; TikTok là nền tảng khác.', 422)
    direct = _profile(url)
    if direct:
        return direct
    host = (urlsplit(url).hostname or '').lower()
    if host != 'v.douyin.com' and not urlsplit(url).path.startswith('/share/user/'):
        raise MonitorError('douyin_profile_url', 'Cần link hồ sơ /user/..., không phải link một video.', 422)
    try:
        expires = time.monotonic() + 10
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            for _ in range(5):
                budget = expires - time.monotonic()
                if budget <= 0:
                    raise MonitorError('douyin_timeout', 'Hết thời gian mở link chia sẻ Douyin.', 504)
                response = client.get(url, timeout=budget)
                response.raise_for_status() if not response.is_redirect else None
                if response.is_redirect:
                    url = urljoin(url, response.headers.get('location', ''))
                    if not _allowed(url):
                        raise MonitorError('douyin_profile_url', 'Link chia sẻ chuyển đến địa chỉ không phải Douyin.', 422)
                    direct = _profile(url)
                    if direct:
                        return direct
                else:
                    break
    except httpx.HTTPError as error:
        raise MonitorError('douyin_network', 'Không mở được link chia sẻ Douyin. Thử link hồ sơ đầy đủ.') from error
    raise MonitorError('douyin_profile_url', 'Link chia sẻ không dẫn đến hồ sơ Douyin công khai.', 422)


def _first_url(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        values = value.get('url_list') or value.get('urlList') or []
        return values[0] if values else ''
    return ''


def parse_post_list(payload, sec_uid, trusted=False):
    """Return None for unrecognized data, never interpret missing data as no posts."""
    if not isinstance(payload, dict):
        return None
    if 'data' in payload and isinstance(payload['data'], dict) and 'aweme_list' not in payload:
        payload = payload['data']
    if payload.get('status_code', 0) not in (0, '0'):
        return None
    posts = payload.get('aweme_list', payload.get('awemeList'))
    if not isinstance(posts, list):
        return None
    more = payload.get('has_more', payload.get('hasMore'))
    complete = more in (False, 0, '0') if more is not None else False
    if not posts:
        return ScanResult([], complete=True) if trusted and complete else None
    videos = []
    for post in posts[:100]:
        if not isinstance(post, dict):
            continue
        author = post.get('author', post.get('authorInfo', {})) or {}
        owner = author.get('sec_uid') or author.get('secUid')
        if owner != sec_uid and not (trusted and not owner):
            continue
        # Image notes are not downloadable videos in this workflow.
        if post.get('images') and not post.get('video'):
            continue
        vid = str(post.get('aweme_id') or post.get('awemeId') or '')
        if not vid.isdigit():
            continue
        timestamp = post.get('create_time', post.get('createTime', 0)) or 0
        try:
            published = datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat() if timestamp else ''
        except (ValueError, OverflowError, OSError, TypeError):
            published = ''
        video = post.get('video', {}) or {}
        videos.append({'video_id': vid, 'title': str(post.get('desc') or 'Douyin Video ' + vid),
                       'url': 'https://www.douyin.com/video/' + vid, 'published': published,
                       'thumbnail': _first_url(video.get('cover') or video.get('origin_cover') or {}),
                       '_pinned': bool(post.get('is_top') or post.get('isTop'))})
    if not videos:
        # An explicitly empty public video list is different from unrelated/broken records.
        return None
    return ScanResult(videos, complete=complete, observed_ids=[v['video_id'] for v in videos])


def embedded_lists(html, sec_uid):
    """Only parse JSON script payloads; do not execute embedded JavaScript."""
    results = []
    def walk(obj):
        if isinstance(obj, dict):
            result = parse_post_list(obj, sec_uid)
            if result:
                results.append(result)
            else:
                for value in obj.values():
                    walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
    for match in re.finditer(r'<script\b([^>]*)>(.*?)</script>', html, re.S | re.I):
        attributes, raw = match.groups()
        sources = []
        if re.search(r'RENDER_DATA|__NEXT_DATA__|application/json', attributes, re.I):
            sources.extend((unquote(raw.strip()), raw.strip()))
        assignment = re.search(r'(?:_ROUTER_DATA|SSR_RENDER_DATA)\s*=\s*', raw)
        if assignment:
            try:
                obj, _ = json.JSONDecoder().raw_decode(raw[assignment.end():])
                walk(obj)
            except (ValueError, RecursionError):
                pass
        for source in sources:
            try:
                walk(json.loads(source))
                break
            except (ValueError, RecursionError):
                continue
    return results


def scan_channel(raw_url, known_ids=None, stop_event=None, timeout=45):
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    started = time.monotonic()
    deadline = started + min(45, timeout)
    url, sec_uid = resolve_profile(raw_url)
    known_ids = set(known_ids or ())
    if not _browser_lock.acquire(blocking=False):
        raise MonitorError('douyin_busy', 'Một kênh Douyin khác đang được kiểm tra. Thử lại sau.', 409)
    collected = {}
    recognized = False
    exhausted = False
    boundary = False
    blocked = False
    navigation_timed_out = False
    packet_count = 0
    browser = context = None
    def remaining():
        return max(1, int((deadline - time.monotonic()) * 1000))
    def ingest(result):
        nonlocal recognized, exhausted, boundary
        recognized = True
        exhausted = exhausted or result.complete
        # A known unpinned item establishes the previous scan boundary.
        boundary = boundary or any(v['video_id'] in known_ids and not v.get('_pinned') for v in result.videos)
        for video in result.videos:
            if len(collected) >= 100:
                break
            collected[video['video_id']] = video
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True, timeout=min(15000, remaining()))
                context = browser.new_context(locale='zh-CN', viewport={'width': 1280, 'height': 900})
                page = context.new_page()
                page.set_default_timeout(3000)
                # Metadata only; never download video/audio during a channel check.
                page.route('**/*', lambda route: route.abort() if route.request.resource_type in ('media', 'image', 'font') else route.continue_())
                def on_response(response):
                    nonlocal blocked, packet_count
                    parsed = urlsplit(response.url)
                    if not _allowed(response.url) or not re.search(r'/aweme/(?:v\d+/web/)?(?:aweme/)?post/?$', parsed.path):
                        return
                    params = parse_qs(parsed.query)
                    requested_uid = (params.get('sec_user_id') or params.get('sec_uid') or [''])[0]
                    if requested_uid and requested_uid != sec_uid:
                        return
                    if response.status in (401, 403, 429):
                        blocked = True
                        return
                    if response.status != 200:
                        return
                    try:
                        result = parse_post_list(response.json(), sec_uid, trusted=requested_uid == sec_uid)
                        if result:
                            packet_count += 1
                            ingest(result)
                    except Exception:
                        pass
                page.on('response', on_response)
                try:
                    response = page.goto(url, wait_until='domcontentloaded', timeout=min(25000, remaining()))
                    if response and response.status >= 400:
                        raise MonitorError('douyin_access', 'Douyin không cho truy cập hồ sơ này từ kết nối hiện tại.')
                except BrowserTimeout:
                    navigation_timed_out = True
                    if navigation_timed_out or time.monotonic() >= deadline:
                        raise MonitorError('douyin_timeout', 'Hết thời gian đọc kênh Douyin. Thử lại sau.', 504)
                scrolls = 0
                last_scroll = time.monotonic()
                while time.monotonic() < deadline:
                    if stop_event and stop_event.is_set():
                        raise MonitorError('monitor_stopping', 'Ứng dụng đang dừng lượt theo dõi.', 503)
                    page.wait_for_timeout(min(250, remaining()))
                    if not recognized:
                        for result in embedded_lists(page.content(), sec_uid):
                            ingest(result)
                    if recognized and (exhausted or boundary or (not known_ids and len(collected) >= 20)):
                        break
                    if len(collected) >= 100 or packet_count >= 4:
                        break
                    elapsed = time.monotonic() - last_scroll
                    if elapsed >= 4 and scrolls < 3:
                        page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                        scrolls += 1
                        last_scroll = time.monotonic()
                    elif elapsed >= 5 and scrolls >= 3:
                        break
                text = page.locator('body').inner_text(timeout=min(2000, remaining()))[:12000] if remaining() > 1 else ''
                if not recognized:
                    if blocked or re.search(r'验证码|安全验证|访问频繁|verifycenter|captcha', text, re.I):
                        raise MonitorError('douyin_verification', 'Douyin đang giới hạn truy cập hoặc yêu cầu xác minh; chưa đọc được danh sách bằng phiên khách.')
                    if re.search(r'登录后(?:查看|观看)|请先登录|登录后即可', text):
                        raise MonitorError('douyin_login_required', 'Douyin yêu cầu đăng nhập để xem kênh này; Nambeo đang dùng phiên khách.')
                    if re.search(r'用户不存在|账号已注销|帐号已注销|私密账号', text):
                        raise MonitorError('douyin_unavailable', 'Kênh Douyin không tồn tại hoặc không công khai.', 404)
                    if time.monotonic() >= deadline:
                        raise MonitorError('douyin_timeout', 'Hết thời gian đọc kênh Douyin. Thử lại sau.', 504)
                    raise MonitorError('douyin_data_unavailable', 'Douyin chưa cung cấp danh sách video công khai cho phiên khách; không thể xác nhận kênh trống.')
            finally:
                if context:
                    context.close()
                if browser:
                    browser.close()
    except MonitorError:
        raise
    except BrowserTimeout as error:
        if not collected:
            raise MonitorError('douyin_timeout', 'Hết thời gian đọc kênh Douyin. Thử lại sau.', 504) from error
    except Exception as error:
        raise MonitorError('douyin_browser', 'Không đọc được Douyin bằng Chromium. Kiểm tra mạng và cài đặt Playwright.') from error
    finally:
        _browser_lock.release()
    videos = sorted(collected.values(), key=lambda v: v['published'], reverse=True)
    complete = exhausted or boundary or (not known_ids and len(videos) >= 20)
    observed = [v['video_id'] for v in videos]
    for video in videos:
        video.pop('_pinned', None)
    if not known_ids:
        videos = videos[:20]
    return ScanResult(videos, complete, '' if complete else 'Kiểm tra chưa đầy đủ; đã giữ các video đọc được.', observed)
