"""Channel monitoring with atomic persistence and a lifecycle-owned scheduler."""
from __future__ import annotations

import copy
from functools import wraps
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
import uuid

import httpx

from core.app_paths import DATA_ROOT
from core.monitor_errors import MonitorError, ScanResult

logger = logging.getLogger("nambeo.monitor")
DATA_DIR = DATA_ROOT / "data"
CHANNELS_FILE = DATA_DIR / "channels.json"
VIDEOS_FILE = DATA_DIR / "channel_videos.json"
FB_CONFIG_FILE = DATA_DIR / "fb_config.json"
_file_lock = threading.RLock()
_checks_lock = threading.Lock()
_active_checks: set[str] = set()
_scheduler_lock = threading.Lock()
_scheduler = None
_stop_event = threading.Event()
_new_video_callbacks = []


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def transactional(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _file_lock:
            return fn(*args, **kwargs)
    return wrapped


@transactional
def _read_json(path, default):
    if not path.exists():
        return copy.deepcopy(default)
    # Never silently replace corrupt user data with an empty database.
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as error:
        raise MonitorError("monitor_storage", "Không đọc được dữ liệu theo dõi; hãy khôi phục bản sao lưu.", 500) from error


@transactional
def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name, suffix=".tmp", delete=False) as stream:
            name = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def validate_interval(platform, value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise MonitorError("channel_interval", "Chu kỳ kiểm tra phải là số phút nguyên dương.", 422)
    if platform == "douyin" and value < 5:
        raise MonitorError("channel_interval", "Chu kỳ theo dõi Douyin tối thiểu là 5 phút.", 422)
    return value


def load_channels():
    channels = _read_json(CHANNELS_FILE, {"channels": []}).get("channels", [])
    with _checks_lock:
        active = set(_active_checks)
    return [{**ch, "checking": ch["id"] in active} for ch in channels]


def save_channels(channels):
    _write_json(CHANNELS_FILE, {"channels": [{k: v for k, v in ch.items() if k != "checking"} for ch in channels]})


@transactional
def add_channel(channel_data):
    channels = load_channels()
    ch = dict(channel_data)
    ch.setdefault("check_interval_minutes", 15)
    validate_interval(ch["platform"], ch["check_interval_minutes"])
    ch.update(id=str(uuid.uuid4()), added_at=now_iso(), last_checked=None,
              last_attempt=None, last_success=None, last_error=None, consecutive_failures=0)
    ch.setdefault("active", True)
    ch.setdefault("collection", "Chung")
    channels.append(ch)
    save_channels(channels)
    return ch


@transactional
def update_channel(channel_id, updates):
    channels = load_channels()
    for ch in channels:
        if ch["id"] == channel_id:
            if "check_interval_minutes" in updates:
                validate_interval(ch["platform"], updates["check_interval_minutes"])
            ch.update(updates)
            save_channels(channels)
            return ch
    return None


@transactional
def delete_channel(channel_id):
    channels = load_channels()
    remaining = [ch for ch in channels if ch["id"] != channel_id]
    if len(remaining) == len(channels):
        return False
    save_channels(remaining)
    return True


def load_videos_data():
    data = _read_json(VIDEOS_FILE, {"known_video_ids": {}, "pending_videos": [], "videos": []})
    data.setdefault("known_video_ids", {})
    if "videos" not in data:
        data["videos"] = list(data.get("pending_videos", []))
    return data


def save_videos_data(data):
    data["pending_videos"] = [v for v in data["videos"] if v.get("status") == "pending"]
    _write_json(VIDEOS_FILE, data)


def get_pending_videos():
    return [v for v in load_videos_data()["videos"] if v.get("status") == "pending"]


def get_channel_videos(channel_uuid=None, status=None):
    # Old clients marked rows as dismissed but still showed them in "All".
    videos = [v for v in load_videos_data()["videos"] if v.get("status") != "dismissed"]
    if channel_uuid and channel_uuid != "all":
        videos = [v for v in videos if v.get("channel_uuid") == channel_uuid or v.get("channel_id") == channel_uuid]
    if status and status != "all":
        videos = [v for v in videos if (v.get("status") or "pending") == status]
    return videos


@transactional
def mark_video_downloaded(video_id, file_path=""):
    data = load_videos_data()
    found = False
    for video in data["videos"]:
        if video.get("video_id") == video_id or video.get("uuid") == video_id:
            found = True
            video.update(status="downloaded", downloaded_at=now_iso())
            if file_path:
                video["file_path"] = file_path
                normalized = file_path.replace("\\", "/")
                video["rel_path"] = normalized.split("/downloads/", 1)[-1] if "/downloads/" in normalized else os.path.basename(file_path)
    if found:
        save_videos_data(data)


@transactional
def update_video_collection(video_uuid, collection):
    data = load_videos_data()
    for video in data["videos"]:
        if video.get("uuid") == video_uuid or video.get("video_id") == video_uuid:
            video["collection"] = collection
            save_videos_data(data)
            return True
    return False


@transactional
def dismiss_pending(video_uuid):
    return bool(delete_channel_videos([video_uuid])["removed_ids"])


def _removed_keys(video):
    platform = str(video.get("platform") or "unknown")
    keys = []
    if video.get("channel_uuid"):
        keys.append(platform + ":channel:" + str(video["channel_uuid"]))
    if video.get("channel_id"):
        keys.append(platform + ":source:" + str(video["channel_id"]))
    return keys or [platform + ":legacy"]


@transactional
def delete_channel_videos(video_uuids, blocked_video_ids=()):
    """Remove feed metadata only; never delete downloaded files or channels."""
    requested = set(video_uuids)
    blocked = set(map(str, blocked_video_ids))
    data = load_videos_data()
    removed, skipped, found, remaining = [], [], set(), []
    suppressions = data.setdefault("removed_video_ids", {})
    for video in data["videos"]:
        identity = str(video.get("uuid") or video.get("video_id") or "")
        if identity not in requested:
            remaining.append(video)
            continue
        found.add(identity)
        if video.get("status") == "downloading" or str(video.get("video_id")) in blocked:
            skipped.append(identity)
            remaining.append(video)
            continue
        # Suppression survives the rolling 500-ID scan cache.
        key = _removed_keys(video)[0]
        known = suppressions.setdefault(key, [])
        vid = str(video.get("video_id") or "")
        if vid and vid not in known:
            known.append(vid)
        removed.append(identity)
    if removed:
        data["videos"] = remaining
        save_videos_data(data)
    return {"removed_count": len(removed), "removed_ids": removed,
            "skipped_ids": skipped, "missing_ids": sorted(requested - found)}


def channel_key(ch):
    prefix = {"youtube": "yt", "douyin": "dy", "facebook": "fb"}[ch["platform"]]
    identity = ch.get("channel_id") if ch["platform"] == "youtube" else ch.get("sec_uid") if ch["platform"] == "douyin" else ch.get("page_id")
    return prefix + "_" + str(identity or ch["url"])


def _is_known(key, video_id):
    return video_id in load_videos_data()["known_video_ids"].get(key, [])


def _video(ch, video_id, **details):
    return dict(platform=ch["platform"], video_id=str(video_id), channel_id=ch.get("channel_id") or ch.get("sec_uid") or ch.get("page_id"),
                channel_uuid=ch["id"], channel_name=ch.get("name", ""), collection=ch.get("collection", "Chung"), **details)


@transactional
def _commit_scan(ch, scan):
    data = load_videos_data()
    key = channel_key(ch)
    known = data["known_video_ids"].get(key, [])
    prior = set(known)
    removed = data.get("removed_video_ids", {})
    suppressed = {vid for key in _removed_keys(_video(ch, "")) for vid in removed.get(key, [])}
    existing = {(v.get("platform"), v.get("video_id"), v.get("channel_uuid")) for v in data["videos"]}
    candidates = sorted(scan.videos, key=lambda v: str(v.get("published", "")), reverse=True)
    if ch["platform"] == "douyin" and not prior:
        candidates = candidates[:20]
    added = []
    for candidate in candidates:
        vid = str(candidate["video_id"])
        identity = (ch["platform"], vid, ch["id"])
        if vid in prior or vid in suppressed or identity in existing:
            continue
        item = dict(candidate, uuid=str(uuid.uuid4()), detected_at=now_iso(), status="pending")
        added.append(item)
        existing.add(identity)
    observed = scan.observed_ids or [str(v["video_id"]) for v in scan.videos]
    # Preserve observation order and at least the previously kept 500 IDs.
    data["known_video_ids"][key] = list(dict.fromkeys(known + observed))[-500:]
    data["videos"] = added + data["videos"]
    save_videos_data(data)
    return added


@transactional
def _add_pending(video):
    # Compatibility for older integrations; ingestion normally uses _commit_scan.
    data = load_videos_data()
    if not any(v.get("platform") == video.get("platform") and v.get("video_id") == video.get("video_id")
               and v.get("channel_uuid") == video.get("channel_uuid") for v in data["videos"]):
        item = dict(video)
        item.setdefault("uuid", str(uuid.uuid4()))
        item.setdefault("detected_at", now_iso())
        item.setdefault("status", "pending")
        data["videos"].insert(0, item)
        save_videos_data(data)


def load_fb_config():
    return _read_json(FB_CONFIG_FILE, {"page_id": "", "page_access_token": "", "page_name": ""})


@transactional
def save_fb_config(cfg):
    old = load_fb_config()
    page_id = str(cfg.get("page_id", "")).strip()
    token = str(cfg.get("page_access_token") or "").strip()
    if not page_id:
        raise MonitorError("facebook_config", "Vui lòng nhập Facebook Page ID.", 422)
    if not token:
        if page_id != old.get("page_id") or not old.get("page_access_token"):
            raise MonitorError("facebook_config", "Nhập token mới khi cấu hình hoặc đổi Facebook Page.", 422)
        token = old["page_access_token"]
    _write_json(FB_CONFIG_FILE, dict(page_id=page_id, page_name=cfg.get("page_name", ""), page_access_token=token))


def detect_platform(url):
    host = (urlsplit(url).hostname or "").lower()
    for platform, domains in (("youtube", ("youtube.com", "youtu.be")), ("douyin", ("douyin.com",)),
                              ("facebook", ("facebook.com", "fb.com", "fb.watch"))):
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return platform
    return None


def resolve_youtube_channel_id(url):
    match = re.search(r"/channel/(UC[\w-]{22})", url)
    if match:
        return match.group(1), url
    try:
        response = httpx.get(url, timeout=10, follow_redirects=True)
        response.raise_for_status()
        match = re.search(r'"(?:externalId|browseId|channelId)"\s*:\s*"(UC[\w-]{22})"', response.text)
        title = re.search(r"<title>([^<]+)</title>", response.text)
        if match:
            return match.group(1), title.group(1).replace(" - YouTube", "") if title else url
    except httpx.HTTPError:
        pass
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"extract_flat": True, "quiet": True, "no_warnings": True,
                               "playlistend": 1, "socket_timeout": 10, "retries": 0}) as ydl:
            info = ydl.extract_info(url, download=False)
        channel_id = (info or {}).get("channel_id") or (info or {}).get("id", "")
        if channel_id.startswith("UC"):
            return channel_id, info.get("channel") or info.get("uploader") or url
    except Exception:
        pass
    raise MonitorError("youtube_channel", "Không đọc được ID kênh YouTube. Kiểm tra link và kết nối.", 422)


def check_youtube(ch):
    channel_id = ch.get("channel_id")
    if channel_id:
        try:
            import feedparser
            response = httpx.get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": channel_id},
                                 timeout=10, follow_redirects=True)
            response.raise_for_status()
            feed = feedparser.parse(response.text)
            if feed.bozo or feed.get("version") != "atom10" or not feed.get("feed"):
                raise ValueError("Invalid RSS")
            if any(not entry.get("yt_videoid") for entry in feed.entries):
                raise ValueError("RSS entries do not contain video IDs")
            videos = [_video(ch, entry.yt_videoid, title=entry.get("title", ""),
                       url=entry.get("link", "https://www.youtube.com/watch?v=" + entry.yt_videoid),
                       published=entry.get("published", ""),
                       thumbnail="https://i.ytimg.com/vi/" + entry.yt_videoid + "/hqdefault.jpg")
                      for entry in feed.entries if entry.get("yt_videoid")]
            return ScanResult(videos)
        except (httpx.HTTPError, ValueError, ImportError):
            pass
    try:
        import yt_dlp
        target = ch["url"].rstrip("/")
        if not target.endswith(("/videos", "/shorts", "/streams")):
            target += "/videos"
        with yt_dlp.YoutubeDL({"extract_flat": True, "playlistend": 20, "quiet": True,
                              "no_warnings": True, "socket_timeout": 10, "retries": 0}) as ydl:
            info = ydl.extract_info(target, download=False)
        if not isinstance(info, dict) or "entries" not in info:
            raise ValueError()
        return ScanResult([_video(ch, e["id"], title=e.get("title", ""), url="https://www.youtube.com/watch?v=" + e["id"],
                          published=e.get("upload_date", ""), thumbnail=e.get("thumbnail") or
                          "https://i.ytimg.com/vi/" + e["id"] + "/hqdefault.jpg")
                           for e in info["entries"] if e and e.get("id")])
    except Exception as error:
        raise MonitorError("youtube_scan", "Không đọc được video YouTube. Kiểm tra kết nối hoặc thử lại sau.") from error


def check_douyin(ch):
    from core.douyin_monitor import scan_channel
    known = set(load_videos_data()["known_video_ids"].get(channel_key(ch), []))
    scan = scan_channel(ch["url"], known_ids=known, stop_event=_stop_event if _scheduler else None)
    return ScanResult([_video(ch, v["video_id"], **{k: value for k, value in v.items() if k != "video_id"})
                       for v in scan.videos], scan.complete, scan.message, scan.observed_ids)


def check_facebook(ch):
    from core.fb_poster import graph_response
    token = load_fb_config().get("page_access_token", "")
    if not token or not ch.get("page_id"):
        raise MonitorError("facebook_config", "Cần cấu hình Page ID và token Facebook.", 422)
    try:
        response = httpx.get("https://graph.facebook.com/v21.0/" + ch["page_id"] + "/videos",
                            params={"access_token": token, "fields": "id,title,description,created_time,permalink_url,thumbnails",
                                    "limit": 20}, timeout=15)
        data = graph_response(response, token)
        if not isinstance(data.get("data"), list):
            raise MonitorError("facebook_response", "Facebook trả danh sách video không hợp lệ.")
        videos = []
        for v in data["data"]:
            if not v.get("id"):
                continue
            thumbnails = v.get("thumbnails", {}).get("data", [])
            link = v.get("permalink_url") or "/video/" + v["id"]
            videos.append(_video(ch, v["id"], title=v.get("title") or v.get("description", "")[:80],
                          url=link if link.startswith("https://") else "https://www.facebook.com" + link,
                          published=v.get("created_time", ""), thumbnail=thumbnails[0].get("uri") if thumbnails else None))
        return ScanResult(videos)
    except httpx.HTTPError as error:
        raise MonitorError("facebook_network", "Không kết nối được Facebook. Hãy thử lại sau.") from error


_CHECKERS = {"youtube": check_youtube, "douyin": check_douyin, "facebook": check_facebook}


def _notify(videos):
    for callback in list(_new_video_callbacks):
        try:
            callback(videos)
        except Exception:
            logger.warning("Không phát được sự kiện cập nhật kênh.")


def check_channel(channel):
    channel_id = channel["id"]
    added = []
    with _checks_lock:
        if channel_id in _active_checks:
            raise MonitorError("channel_busy", "Kênh đang được kiểm tra.", 409)
        _active_checks.add(channel_id)
    try:
        ch = next((c for c in load_channels() if c["id"] == channel_id), None)
        if ch is None:
            raise MonitorError("channel_missing", "Kênh không còn tồn tại.", 404)
        update_channel(channel_id, {"last_attempt": now_iso()})
        checker = _CHECKERS.get(ch["platform"])
        if not checker:
            raise MonitorError("channel_platform", "Nền tảng chưa được hỗ trợ.", 422)
        scan = checker(ch)
        if isinstance(scan, list):
            scan = ScanResult(scan)
        with _file_lock:
            if not any(c["id"] == channel_id for c in load_channels()):
                raise MonitorError("channel_missing", "Kênh đã bị xóa trong khi kiểm tra.", 404)
            added = _commit_scan(ch, scan)
            stamp = now_iso()
            update_channel(channel_id, {"last_checked": stamp, "last_success": stamp, "last_error": None,
                           "last_status": "complete" if scan.complete else "partial", "last_message": scan.message,
                           "consecutive_failures": 0, "next_retry_at": None})
        return {"channel_id": channel_id, "status": "complete" if scan.complete else "partial",
                "complete": scan.complete, "message": scan.message, "new_videos_found": len(added), "videos": added}
    except Exception as error:
        public = error if isinstance(error, MonitorError) else MonitorError("channel_failed", "Kiểm tra kênh thất bại. Hãy thử lại.")
        if public.status_code == 409:
            raise public
        current = next((c for c in load_channels() if c["id"] == channel_id), None)
        if current:
            failures = current.get("consecutive_failures", 0) + 1
            delay = (15, 30, 60)[min(failures - 1, 2)]
            update_channel(channel_id, {"last_status": "error", "last_error": {"code": public.code, "message": str(public)},
                           "consecutive_failures": failures,
                           "next_retry_at": (datetime.now(timezone.utc) + timedelta(minutes=delay)).isoformat()})
        if public is error:
            raise
        raise public from error
    finally:
        with _checks_lock:
            _active_checks.discard(channel_id)
        _notify(added)


def register_new_video_callback(callback):
    if callback not in _new_video_callbacks:
        _new_video_callbacks.append(callback)


def _run_monitor_cycle():
    for ch in load_channels():
        if _stop_event.is_set():
            break
        if not ch.get("active", True):
            continue
        retry = ch.get("next_retry_at")
        stamp = retry or ch.get("last_attempt") or ch.get("last_checked")
        if stamp:
            try:
                due = datetime.fromisoformat(stamp).timestamp()
                if not retry:
                    due += max(5 if ch["platform"] == "douyin" else 1, ch.get("check_interval_minutes", 15)) * 60
                if time.time() < due:
                    continue
            except (ValueError, TypeError):
                pass
        try:
            check_channel(ch)
        except MonitorError:
            pass


def start_scheduler():
    global _scheduler
    with _scheduler_lock:
        if _scheduler and _scheduler.is_alive():
            return
        _stop_event.clear()
        def loop():
            while not _stop_event.is_set():
                try:
                    _run_monitor_cycle()
                except Exception:
                    logger.error("Không hoàn tất được vòng theo dõi kênh.")
                _stop_event.wait(60)
        _scheduler = threading.Thread(target=loop, daemon=True, name="nambeo-monitor")
        _scheduler.start()


def stop_scheduler():
    global _scheduler
    with _scheduler_lock:
        _stop_event.set()
        if _scheduler:
            _scheduler.join(timeout=50)
            if not _scheduler.is_alive():
                _scheduler = None
