"""Validated Facebook Page uploads. Never retry an ambiguous publication."""
from __future__ import annotations

from pathlib import Path
import threading

import httpx

from core.monitor import load_fb_config
from core.monitor_errors import MonitorError

GRAPH_VIDEO_URL = "https://graph-video.facebook.com/v21.0"
GRAPH_URL = "https://graph.facebook.com/v21.0"
CHUNK_SIZE = 10 * 1024 * 1024
SMALL_FILE_LIMIT = 100 * 1024 * 1024
_upload_lock = threading.Lock()
_active_uploads = set()


def _get_config():
    cfg = load_fb_config()
    page_id = cfg.get("page_id", "").strip()
    token = cfg.get("page_access_token", "").strip()
    if not page_id or not token:
        raise MonitorError("facebook_config", "Cần cấu hình Facebook Page ID và Access Token.", 422)
    return page_id, token


def graph_response(response, token=""):
    # Never forward Graph messages/URLs: they can echo request credentials.
    try:
        data = response.json()
    except (ValueError, TypeError) as error:
        raise MonitorError("facebook_response", "Facebook trả phản hồi không phải JSON hợp lệ.") from error
    if not isinstance(data, dict):
        raise MonitorError("facebook_response", "Facebook trả phản hồi không hợp lệ.")
    if response.status_code >= 400 or "error" in data:
        code = data.get("error", {}).get("code") if isinstance(data.get("error"), dict) else None
        message = ("Facebook từ chối token hoặc quyền truy cập Page. Kiểm tra kết nối và quyền đăng."
                   if code in (10, 102, 190, 200) or response.status_code in (401, 403)
                   else "Facebook từ chối yêu cầu. Kiểm tra kết nối Page hoặc thử lại sau.")
        raise MonitorError("facebook_api", message)
    return data


def _request(url, token, **kwargs):
    try:
        return graph_response(httpx.post(url, **kwargs), token)
    except (httpx.TimeoutException, httpx.TransportError) as error:
        raise MonitorError("facebook_outcome_unknown",
                           "Chưa xác định kết quả đăng. Kiểm tra Page trước khi đăng lại để tránh trùng video.", 504) from error


def _id(data, key):
    value = data.get(key)
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value).strip():
        raise MonitorError("facebook_response", "Facebook chưa xác nhận ID video hoặc phiên upload.")
    return str(value)


def _upload_small(mp4_path, caption, page_id, token):
    with open(mp4_path, "rb") as stream:
        data = _request(GRAPH_VIDEO_URL + "/" + page_id + "/videos", token,
                        data={"description": caption, "access_token": token},
                        files={"source": stream}, timeout=300)
    return _id(data, "id")


def _offsets(data, file_size, previous=None):
    try:
        start, end = int(data["start_offset"]), int(data["end_offset"])
    except (KeyError, ValueError, TypeError) as error:
        raise MonitorError("facebook_offset", "Facebook trả vị trí upload không hợp lệ.") from error
    if not 0 <= start <= end <= file_size or (start < file_size and end <= start):
        raise MonitorError("facebook_offset", "Facebook trả vị trí upload ngoài giới hạn.")
    if previous is not None and start <= previous:
        raise MonitorError("facebook_offset", "Upload không tiến triển; đã dừng để tránh lặp vô hạn.")
    return start, end


def _upload_resumable(mp4_path, caption, page_id, token, on_progress=None):
    file_size = Path(mp4_path).stat().st_size
    url = GRAPH_VIDEO_URL + "/" + page_id + "/videos"
    data = _request(url, token, data={"upload_phase": "start", "file_size": file_size, "access_token": token}, timeout=30)
    session = _id(data, "upload_session_id")
    video_id = _id(data, "video_id")
    start, end = _offsets(data, file_size)
    if start != 0:
        raise MonitorError("facebook_offset", "Phiên upload mới không bắt đầu từ đầu file.")
    with open(mp4_path, "rb") as stream:
        while start < file_size:
            stream.seek(start)
            # Graph chooses chunk boundaries; bound allocation and abort unreasonable sizes.
            if end - start > 64 * 1024 * 1024:
                raise MonitorError("facebook_offset", "Facebook yêu cầu phần upload quá lớn.")
            chunk = stream.read(end - start)
            if len(chunk) != end - start:
                raise MonitorError("facebook_file_changed", "File video đã thay đổi trong lúc upload.", 409)
            transfer = _request(url, token,
                                data={"upload_phase": "transfer", "upload_session_id": session,
                                      "start_offset": start, "access_token": token},
                                files={"video_file_chunk": ("chunk", chunk, "application/octet-stream")}, timeout=120)
            new_start, new_end = _offsets(transfer, file_size, previous=start)
            if new_start != end:
                raise MonitorError("facebook_offset", "Facebook chưa xác nhận đủ phần video vừa gửi.")
            start, end = new_start, new_end
            if on_progress:
                on_progress(start, file_size)
    finish = _request(url, token, data={"upload_phase": "finish", "upload_session_id": session,
                                      "description": caption, "access_token": token}, timeout=60)
    if finish.get("success") is not True:
        raise MonitorError("facebook_outcome_unknown",
                           "Facebook chưa xác nhận hoàn tất. Kiểm tra Page trước khi đăng lại.", 502)
    return video_id


def post_video_to_facebook(mp4_path, caption="", on_progress=None):
    path = Path(mp4_path).resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise MonitorError("facebook_file", "File video không tồn tại hoặc rỗng.", 404)
    page_id, token = _get_config()
    key = (str(path).casefold(), page_id)
    with _upload_lock:
        if key in _active_uploads:
            raise MonitorError("facebook_busy", "Video này đang được đăng lên Page.", 409)
        _active_uploads.add(key)
    try:
        if path.stat().st_size < SMALL_FILE_LIMIT:
            video_id = _upload_small(str(path), caption, page_id, token)
        else:
            video_id = _upload_resumable(str(path), caption, page_id, token, on_progress)
        return {"success": True, "video_id": video_id, "page_id": page_id}
    except OSError as error:
        raise MonitorError("facebook_file", "Không đọc được file video trong lúc upload. Kiểm tra file trước khi thử lại.", 409) from error
    finally:
        with _upload_lock:
            _active_uploads.discard(key)


def test_fb_connection():
    page_id, token = _get_config()
    try:
        response = httpx.get(GRAPH_URL + "/" + page_id,
                             params={"fields": "id,name,fan_count", "access_token": token}, timeout=10)
        data = graph_response(response, token)
        actual_id = _id(data, "id")
        if actual_id != page_id or not data.get("name"):
            raise MonitorError("facebook_page", "Token không xác nhận đúng Page đã cấu hình.")
        return {"page_id": actual_id, "page_name": data["name"], "fans": data.get("fan_count")}
    except httpx.HTTPError as error:
        raise MonitorError("facebook_network", "Không kết nối được Facebook. Hãy thử lại sau.") from error
