"""Explicit Page selection, preserving the legacy single-Page configuration."""
import asyncio
import copy
import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import monitor, fb_poster
from .monitor_errors import MonitorError


def _path():
    return monitor.FB_CONFIG_FILE.with_name('fb_pages.json')


def _load():
    if _path().exists():
        data = monitor._read_json(_path(), {})
        if not isinstance(data, dict) or not isinstance(data.get('pages'), list):
            raise MonitorError('facebook_storage', 'Danh sách Page không hợp lệ; dữ liệu được giữ nguyên.', 500)
        return data
    old = monitor.load_fb_config()
    return {'pages': [old] if old.get('page_id') and old.get('page_access_token') else []}


def public(page):
    return {'page_id': page['page_id'], 'page_name': page.get('page_name', ''),
            'token_set': bool(page.get('page_access_token'))}


@monitor.transactional
def list_pages():
    return [public(p) for p in _load()['pages']]


@monitor.transactional
def get_page(page_id):
    for page in _load()['pages']:
        if page['page_id'] == page_id:
            return copy.deepcopy(page)
    raise MonitorError('facebook_page_missing', 'Page đã bị xóa hoặc chưa được lưu. Hãy chọn lại Page.', 404)


@monitor.transactional
def save_page(page_id, name, token):
    page_id = page_id.strip()
    if not re.fullmatch(r'[0-9]+', page_id):
        raise MonitorError('facebook_page_id', 'Page ID phải là dãy số.', 422)
    data = _load()
    old = next((p for p in data['pages'] if p['page_id'] == page_id), None)
    token = (token or '').strip() or (old or {}).get('page_access_token', '')
    if not token:
        raise MonitorError('facebook_token', 'Page mới cần Page Access Token riêng.', 422)
    page = {'page_id': page_id, 'page_name': name.strip(), 'page_access_token': token}
    data['pages'] = [page if p['page_id'] == page_id else p for p in data['pages']]
    if old is None:
        data['pages'].append(page)
    monitor._write_json(_path(), data)
    return public(page)


@monitor.transactional
def delete_page(page_id):
    data = _load()
    data['pages'] = [p for p in data['pages'] if p['page_id'] != page_id]
    monitor._write_json(_path(), data)


def check_page(page):
    try:
        response = httpx.get(fb_poster.GRAPH_URL + '/' + page['page_id'],
                             params={'fields': 'id,name', 'access_token': page['page_access_token']}, timeout=15)
        result = fb_poster.graph_response(response)
        if str(result.get('id')) != page['page_id'] or not result.get('name'):
            raise MonitorError('facebook_page', 'Token không xác nhận đúng Page đã chọn.', 422)
        return {'page_id': page['page_id'], 'page_name': result['name']}
    except httpx.HTTPError:
        raise MonitorError('facebook_network', 'Không kết nối được Facebook. Hãy thử lại sau.', 502) from None


def upload(page, path, caption):
    # Snapshot credentials belong to this request, never to a mutable global default.
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise MonitorError('facebook_file', 'File video không tồn tại hoặc rỗng.', 404)
    key = (str(path).casefold(), page['page_id'])
    with fb_poster._upload_lock:
        if key in fb_poster._active_uploads:
            raise MonitorError('facebook_busy', 'Video này đang được đăng lên Page đã chọn.', 409)
        fb_poster._active_uploads.add(key)
    try:
        fn = fb_poster._upload_small if path.stat().st_size < fb_poster.SMALL_FILE_LIMIT else fb_poster._upload_resumable
        video_id = fn(str(path), caption, page['page_id'], page['page_access_token'])
        return {'success': True, 'video_id': video_id, 'page_id': page['page_id']}
    except OSError:
        raise MonitorError('facebook_file', 'Không đọc được video để đăng.', 409) from None
    finally:
        with fb_poster._upload_lock:
            fb_poster._active_uploads.discard(key)


class PageInput(BaseModel):
    page_id: str = Field(max_length=80)
    page_name: str = Field(default='', max_length=300)
    page_access_token: str | None = Field(default=None, max_length=16384)


class PostInput(BaseModel):
    mp4_path: str
    caption: str = ''


def local_ui(request: Request):
    try:
        local = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        local = False
    expected = f'{request.url.scheme}://{request.url.netloc}'
    ref = urlsplit(request.headers.get('referer', ''))
    origin = request.headers.get('origin')
    ref_origin = f'{ref.scheme}://{ref.netloc}' if ref.netloc else None
    if (not local or request.url.hostname not in ('localhost', '127.0.0.1', '::1')
            or (origin and origin != expected) or (ref_origin and ref_origin != expected)
            or request.headers.get('sec-fetch-site') in ('cross-site', 'same-site')
            or not (origin == expected or ref_origin == expected or request.headers.get('sec-fetch-site') == 'same-origin')):
        raise HTTPException(403, 'Hãy dùng chức năng Facebook từ giao diện Nambeo trên máy này.')


def attach(host):
    router = APIRouter(prefix='/api/fb/pages', dependencies=[Depends(local_ui)])

    @router.get('')
    def listing():
        return {'pages': list_pages()}

    @router.post('')
    def save(req: PageInput):
        return {'page': save_page(req.page_id, req.page_name, req.page_access_token)}

    @router.delete('/{page_id}')
    def delete(page_id: str):
        delete_page(page_id)
        return {'deleted': True}

    @router.post('/{page_id}/test')
    async def test(page_id: str):
        page = get_page(page_id)
        return {'ok': True, **await asyncio.to_thread(check_page, page)}

    @router.post('/{page_id}/post')
    async def post(page_id: str, req: PostInput):
        page = get_page(page_id)
        path = host._resolve_safe_path(req.mp4_path)
        return await asyncio.to_thread(upload, page, path, req.caption)

    host.app.include_router(router)
