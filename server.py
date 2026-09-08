import asyncio
import json
import os
import sys
import subprocess
import shutil
import urllib.parse
from typing import Dict, List, Optional

# Force UTF-8 on Windows
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uuid

from core.downloader import DouyinDownloader
from core.extractor import DouyinExtractor
from core.transcriber import DouyinTranscriber
from core.translator import DouyinTranslator
from core.dubber import DouyinDubber
from core.subtitler import DouyinSubtitler

app = FastAPI(title="Douyin Video Downloader MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DEFAULT_DOWNLOAD_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def load_app_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    example_file = os.path.join(BASE_DIR, "config.example.json")
    if os.path.exists(example_file):
        try:
            with open(example_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_app_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}")

# State
current_download_dir = DEFAULT_DOWNLOAD_DIR
extractor = DouyinExtractor()
downloader = DouyinDownloader(default_download_dir=current_download_dir)
transcriber = DouyinTranscriber()
translator = DouyinTranslator()
dubber = DouyinDubber()
subtitler = DouyinSubtitler()
parsed_cache: Dict[str, Dict] = {}

class ParseRequest(BaseModel):
    urls: List[str]

class ConfigRequest(BaseModel):
    download_dir: str

class CollectionCreateRequest(BaseModel):
    name: str

class DownloadRequest(BaseModel):
    video_ids: List[str]
    target_dir: Optional[str] = None
    collection: Optional[str] = "Chung"
    videos: Optional[List[Dict]] = None

class OpenFolderRequest(BaseModel):
    folder_path: Optional[str] = None

class OpenFileRequest(BaseModel):
    filepath: str

class ReplaceOriginalRequest(BaseModel):
    filepath: Optional[str] = None
    target_rel: Optional[str] = None
    replacement_rel: str

class RestoreOriginalRequest(BaseModel):
    filepath: Optional[str] = None
    target_rel: Optional[str] = None

class BatchDeleteRequest(BaseModel):
    filepaths: List[str]

class TranscribeRequest(BaseModel):
    filepath: str
    model_size: Optional[str] = "base"
    language: Optional[str] = "auto"

class CharacterMapRequest(BaseModel):
    filepath: str
    video_title: Optional[str] = ""
    provider: Optional[str] = None
    model: Optional[str] = None

class TranslateRequest(BaseModel):
    filepath: str
    character_map: Dict
    provider: Optional[str] = None
    model: Optional[str] = None

class ApiKeyRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None

class AiConfigRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_model: Optional[str] = None
    default_ai_provider: Optional[str] = None
    default_ai_model: Optional[str] = None

class AiTestRequest(BaseModel):
    provider: Optional[str] = "gemini"
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None

class TranslateChunkRequest(BaseModel):
    segments: List[Dict]
    character_map: Optional[Dict] = None
    provider: Optional[str] = None
    model: Optional[str] = None

class SaveTranscriptRequest(BaseModel):
    filepath: str
    segments: List[Dict]
    character_map: Optional[Dict] = None

class DubPreviewRequest(BaseModel):
    text: Optional[str] = "Xin chào, đây là giọng đọc thử nghiệm lồng tiếng video."
    voice: Optional[str] = "vi-VN-HoaiMyNeural"
    provider: Optional[str] = "edge"

class DubExecuteRequest(BaseModel):
    filepath: str
    mode: Optional[str] = "single"
    single_voice: Optional[str] = "vi-VN-HoaiMyNeural"
    character_voices: Optional[Dict[str, str]] = None
    bg_volume: Optional[float] = 0.15
    voice_volume: Optional[float] = 1.2
    burn_sub: Optional[bool] = False
    sub_type: Optional[str] = "hardsub"
    sub_lang: Optional[str] = "vi"
    font_size: Optional[int] = 22
    font_color: Optional[str] = "white"
    position: Optional[str] = "overlay_original"
    mask_original: Optional[bool] = True

class SubtitleExportRequest(BaseModel):
    filepath: str
    sub_type: Optional[str] = "hardsub"
    language: Optional[str] = "vi"
    audio_source: Optional[str] = "auto"
    font_size: Optional[int] = 22
    font_color: Optional[str] = "white"
    position: Optional[str] = "overlay_original"
    mask_original: Optional[bool] = True

@app.get("/api/config")
async def get_config():
    cfg = load_app_config()
    gemini_key = cfg.get("gemini_api_key", os.environ.get("GEMINI_API_KEY", ""))
    gemini_masked = f"{gemini_key[:6]}...{gemini_key[-4:]}" if len(gemini_key) > 10 else ("***" if gemini_key else "")

    openai_key = cfg.get("openai_api_key", os.environ.get("OPENAI_API_KEY", ""))
    openai_masked = f"{openai_key[:6]}...{openai_key[-4:]}" if len(openai_key) > 10 else ("***" if openai_key else "")
    openai_base_url = cfg.get("openai_base_url", "")

    default_provider = cfg.get("default_ai_provider", "gemini" if gemini_key else ("openai" if openai_key else "gemini"))
    gemini_model = cfg.get("gemini_model") or "gemini-2.5-flash"
    openai_model = cfg.get("openai_model") or "gpt-4o-mini"

    if default_provider == "openai":
        default_model = cfg.get("default_ai_model") or openai_model
    else:
        default_model = cfg.get("default_ai_model") or gemini_model

    return {
        "download_dir": current_download_dir,
        "default_dir": DEFAULT_DOWNLOAD_DIR,
        "has_gemini_key": bool(gemini_key),
        "gemini_api_key_masked": gemini_masked,
        "gemini_model": gemini_model,
        "has_openai_key": bool(openai_key),
        "openai_api_key_masked": openai_masked,
        "openai_base_url": openai_base_url,
        "openai_model": openai_model,
        "default_ai_provider": default_provider,
        "default_ai_model": default_model
    }

@app.post("/api/config/ai")
async def save_ai_config(req: AiConfigRequest):
    cfg = load_app_config()
    if req.gemini_api_key is not None:
        cfg["gemini_api_key"] = req.gemini_api_key.strip()
    if req.gemini_model is not None:
        cfg["gemini_model"] = req.gemini_model.strip()
    if req.openai_api_key is not None:
        cfg["openai_api_key"] = req.openai_api_key.strip()
    if req.openai_base_url is not None:
        cfg["openai_base_url"] = req.openai_base_url.strip()
    if req.openai_model is not None:
        cfg["openai_model"] = req.openai_model.strip()
    if req.default_ai_provider is not None:
        cfg["default_ai_provider"] = req.default_ai_provider.strip()
    if req.default_ai_model is not None:
        cfg["default_ai_model"] = req.default_ai_model.strip()

    save_app_config(cfg)
    return {"status": "success", "message": "Đã lưu cấu hình AI thành công"}

@app.post("/api/config/ai/test")
async def test_ai_connection(req: AiTestRequest):
    cfg = load_app_config()
    provider = (req.provider or cfg.get("default_ai_provider", "gemini")).lower().strip()
    base_url = req.base_url or cfg.get("openai_base_url", "")

    if provider in ["openai", "chatgpt"]:
        api_key = req.api_key or cfg.get("openai_api_key", os.environ.get("OPENAI_API_KEY", ""))
        model = req.model or cfg.get("openai_model") or "gpt-4o-mini"
        if not api_key:
            raise HTTPException(status_code=400, detail="Chưa nhập API Key cho OpenAI/ChatGPT.")
    else:
        provider = "gemini"
        api_key = req.api_key or cfg.get("gemini_api_key", os.environ.get("GEMINI_API_KEY", ""))
        model = req.model or cfg.get("gemini_model") or "gemini-2.5-flash"
        if not api_key:
            raise HTTPException(status_code=400, detail="Chưa nhập API Key cho Google Gemini.")

    try:
        res = await translator.test_connection(
            api_key=api_key,
            provider=provider,
            model=model,
            base_url=base_url
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/api-key")
async def save_api_key(req: ApiKeyRequest):
    cfg = load_app_config()
    if req.gemini_api_key is not None:
        cfg["gemini_api_key"] = req.gemini_api_key.strip()
    if req.openai_api_key is not None:
        cfg["openai_api_key"] = req.openai_api_key.strip()
    save_app_config(cfg)
    return {"status": "success", "message": "Đã lưu API Key thành công"}

@app.post("/api/config")
async def update_config(req: ConfigRequest):
    global current_download_dir
    new_dir = req.download_dir.strip()
    if not new_dir:
        raise HTTPException(status_code=400, detail="Đường dẫn thư mục không được để trống")
    try:
        os.makedirs(new_dir, exist_ok=True)
        current_download_dir = new_dir
        downloader.default_download_dir = new_dir
        return {"status": "success", "download_dir": current_download_dir}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Không thể tạo/truy cập thư mục: {str(e)}")

@app.post("/api/open-folder")
async def open_folder(req: OpenFolderRequest):
    folder = req.folder_path or current_download_dir
    if not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)
    try:
        if os.name == "nt":
            os.startfile(folder)
        else:
            subprocess.run(["xdg-open", folder])
        return {"status": "success", "opened": folder}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi mở thư mục: {str(e)}")

@app.get("/api/collections")
async def get_collections():
    cols = ["Chung"]
    if os.path.exists(current_download_dir):
        for item in sorted(os.listdir(current_download_dir)):
            item_path = os.path.join(current_download_dir, item)
            if os.path.isdir(item_path) and not item.startswith("."):
                if item not in cols:
                    cols.append(item)
    return {"collections": cols}

@app.post("/api/collections")
async def create_collection(req: CollectionCreateRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tên bộ sưu tập không được để trống")
    safe_name = downloader.sanitize_filename(name, max_len=30)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Tên bộ sưu tập không hợp lệ")
    col_dir = os.path.join(current_download_dir, safe_name)
    os.makedirs(col_dir, exist_ok=True)
    return {"status": "success", "collection": safe_name}

@app.post("/api/parse")
async def parse_videos(req: ParseRequest):
    valid_urls = [u.strip() for u in req.urls if u.strip()]
    if not valid_urls:
        raise HTTPException(status_code=400, detail="Vui lòng nhập ít nhất một link Douyin")

    results = []
    for raw_url in valid_urls:
        info = await extractor.parse_single_url(raw_url)
        if info:
            parsed_cache[info["id"]] = info
            # Return without heavy raw_detail
            item = {k: v for k, v in info.items() if k != "raw_detail"}
            results.append(item)

    if not results:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy video từ link đã nhập. Vui lòng kiểm tra lại link Douyin hoặc Facebook."
        )

    return {"status": "success", "count": len(results), "videos": results}


@app.get("/api/proxy-media")
async def proxy_media(request: Request, url: str = Query(...)):
    """Proxy video and image streams to bypass CORS and Referer restrictions, supporting Range requests for seeking."""
    decoded_url = urllib.parse.unquote(url)
    if "fbcdn.net" in decoded_url or "facebook.com" in decoded_url:
        referer = "https://www.facebook.com/"
    elif "ytimg.com" in decoded_url or "youtube.com" in decoded_url or "googlevideo.com" in decoded_url:
        referer = "https://www.youtube.com/"
    else:
        referer = "https://www.douyin.com/"

    headers = {
        "User-Agent": extractor.user_agent,
        "Referer": referer,
        "Accept-Encoding": "identity",
    }

    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    client = httpx.AsyncClient(follow_redirects=True, timeout=60.0)
    req = client.build_request("GET", decoded_url, headers=headers)
    resp = await client.send(req, stream=True)

    response_headers = {
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": "*",
    }
    if "content-range" in resp.headers:
        response_headers["Content-Range"] = resp.headers["content-range"]
    if "content-length" in resp.headers:
        response_headers["Content-Length"] = resp.headers["content-length"]

    media_type = resp.headers.get(
        "content-type",
        "video/mp4" if "video" in decoded_url or "mp4" in decoded_url else "image/jpeg"
    )

    async def stream_content():
        try:
            # Use raw un-decoded socket bytes to avoid brotli/gzip chunk decoding errors on range slices
            async for chunk in resp.aiter_raw(chunk_size=65536):
                yield chunk
        except Exception:
            pass
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_content(),
        status_code=resp.status_code,
        headers=response_headers,
        media_type=media_type
    )


def _resolve_safe_path(rel_path: str) -> str:
    decoded = urllib.parse.unquote(rel_path).replace("\\", "/")
    full_path = os.path.normpath(os.path.join(current_download_dir, decoded))
    base_abs = os.path.abspath(current_download_dir)
    if not os.path.abspath(full_path).startswith(base_abs):
        raise HTTPException(status_code=403, detail="Truy cập bị từ chối")
    return full_path

async def _bg_download_task(v_info: Dict, target_dir: str, collection: Optional[str] = None):
    await downloader.download_video(v_info, target_dir=target_dir, collection=collection)

@app.post("/api/download")
async def trigger_download(req: DownloadRequest, background_tasks: BackgroundTasks):
    target_dir = req.target_dir or current_download_dir
    os.makedirs(target_dir, exist_ok=True)
    col = req.collection.strip() if req.collection else "Chung"

    # Hydrate cache from client request if available (for persistence across server/page restarts)
    if req.videos:
        for v in req.videos:
            if isinstance(v, dict) and "id" in v:
                if v["id"] not in parsed_cache or not parsed_cache[v["id"]].get("play_url"):
                    parsed_cache[v["id"]] = v

    started = []
    for v_id in req.video_ids:
        if v_id in parsed_cache:
            v_info = parsed_cache[v_id]
            background_tasks.add_task(_bg_download_task, v_info, target_dir, col)
            started.append(v_id)

    if not started:
        raise HTTPException(status_code=404, detail="Không tìm thấy thông tin video để tải. Vui lòng phân tích lại.")

    return {"status": "success", "started_ids": started, "target_dir": target_dir, "collection": col}

@app.get("/api/tasks")
async def get_tasks():
    return downloader.get_all_tasks()

@app.get("/api/history")
async def get_history():
    """Scan current download directory and subfolders for all downloaded mp4 videos and their metadata."""
    if not os.path.exists(current_download_dir):
        return {"videos": []}

    targets = [("", "Chung")]  # (subfolder_rel, collection_name)
    try:
        for item in sorted(os.listdir(current_download_dir)):
            item_path = os.path.join(current_download_dir, item)
            if os.path.isdir(item_path) and not item.startswith("."):
                targets.append((item, item))
    except Exception as e:
        print(f"Error scanning directories: {e}")

    history = []
    from datetime import datetime

    for subfolder, col_name in targets:
        folder = os.path.join(current_download_dir, subfolder) if subfolder else current_download_dir
        if not os.path.exists(folder):
            continue
        try:
            derivative_suffixes = (
                ".dubbed.mp4",
                ".hardsub.vi.mp4",
                ".hardsub.bilingual.mp4",
                ".dubbed.hardsub.vi.mp4",
                ".dubbed.hardsub.bilingual.mp4",
                ".backup.mp4"
            )
            files = [f for f in os.listdir(folder) if f.endswith(".mp4") and not any(f.endswith(sfx) for sfx in derivative_suffixes)]
        except Exception:
            continue

        for f in files:
            mp4_path = os.path.join(folder, f)
            base_name = f[:-4]
            thumb_name = f"{base_name}.thumb.jpg"
            thumb_path = os.path.join(folder, thumb_name)
            json_name = f"{base_name}.json"
            json_path = os.path.join(folder, json_name)

            # Ensure first-frame thumbnail exists
            if not os.path.exists(thumb_path):
                DouyinDownloader.extract_first_frame(mp4_path, thumb_path)

            try:
                stat = os.stat(mp4_path)
                size_mb = round(stat.st_size / (1024 * 1024), 2)
                created_time = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                size_mb = 0
                created_time = "N/A"

            # Try reading json metadata
            title = base_name
            author = "User"
            platform = "facebook" if ("facebook" in f.lower() or "fb_" in f.lower()) else ("youtube" if ("youtube" in f.lower() or "yt_" in f.lower()) else "douyin")
            duration = 0
            resolution = "HD"
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as jf:
                        meta = json.load(jf)
                        platform = meta.get("platform", platform)
                        if platform in ["facebook", "youtube"]:
                            title = meta.get("title", base_name)
                            author = meta.get("author", "YouTube Creator" if platform == "youtube" else "Facebook User")
                            resolution = meta.get("resolution", "HD")
                            duration = meta.get("duration", 0)
                        else:
                            title = meta.get("desc", meta.get("title", base_name))
                            author = meta.get("author", {}).get("nickname", "Douyin User") if isinstance(meta.get("author"), dict) else meta.get("author", "Douyin User")
                            v_meta = meta.get("video", {})
                            d_ms = v_meta.get("duration", 0)
                            duration = round(d_ms / 1000, 1) if d_ms else meta.get("duration", 0)
                            w, h = v_meta.get("width", 0), v_meta.get("height", 0)
                            if w and h:
                                resolution = f"{w}x{h}"
                except Exception:
                    pass

            # Ensure accurate duration using OpenCV
            if duration <= 0:
                duration = DouyinDownloader.get_video_duration(mp4_path)

            rel_mp4 = os.path.relpath(mp4_path, current_download_dir).replace("\\", "/")
            rel_thumb = os.path.relpath(thumb_path, current_download_dir).replace("\\", "/")
            transcript_path = os.path.join(folder, f"{base_name}.transcript.json")
            dubbed_mp4_path = os.path.join(folder, f"{base_name}.dubbed.mp4")
            dubbed_mp3_path = os.path.join(folder, f"{base_name}.dubbed.mp3")
            has_dubbed = os.path.exists(dubbed_mp4_path)
            hardsub_vi_path = os.path.join(folder, f"{base_name}.hardsub.vi.mp4")
            hardsub_bi_path = os.path.join(folder, f"{base_name}.hardsub.bilingual.mp4")
            dubbed_hardsub_vi_path = os.path.join(folder, f"{base_name}.dubbed.hardsub.vi.mp4")
            dubbed_hardsub_bi_path = os.path.join(folder, f"{base_name}.dubbed.hardsub.bilingual.mp4")
            has_hardsub = os.path.exists(hardsub_vi_path) or os.path.exists(hardsub_bi_path)
            has_dubbed_sub = os.path.exists(dubbed_hardsub_vi_path) or os.path.exists(dubbed_hardsub_bi_path)
            has_sub_video = has_hardsub or has_dubbed_sub
            backup_path = os.path.join(folder, f"{base_name}.backup.mp4")
            has_backup = os.path.exists(backup_path)

            # Determine best video to play (dubbed_sub > dubbed > hardsub > original)
            if has_dubbed_sub and os.path.exists(dubbed_hardsub_vi_path):
                best_video_rel = os.path.relpath(dubbed_hardsub_vi_path, current_download_dir).replace("\\", "/")
            elif has_dubbed_sub and os.path.exists(dubbed_hardsub_bi_path):
                best_video_rel = os.path.relpath(dubbed_hardsub_bi_path, current_download_dir).replace("\\", "/")
            elif has_dubbed:
                best_video_rel = os.path.relpath(dubbed_mp4_path, current_download_dir).replace("\\", "/")
            elif has_hardsub and os.path.exists(hardsub_vi_path):
                best_video_rel = os.path.relpath(hardsub_vi_path, current_download_dir).replace("\\", "/")
            elif has_hardsub and os.path.exists(hardsub_bi_path):
                best_video_rel = os.path.relpath(hardsub_bi_path, current_download_dir).replace("\\", "/")
            else:
                best_video_rel = rel_mp4

            history.append({
                "filename": f,
                "rel_path": rel_mp4,
                "collection": col_name,
                "platform": platform,
                "title": title,
                "author": author,
                "size_mb": size_mb,
                "created_time": created_time,
                "duration": duration,
                "resolution": resolution,
                "has_thumb": os.path.exists(thumb_path),
                "has_transcript": os.path.exists(transcript_path),
                "has_dubbed": has_dubbed,
                "has_hardsub": has_hardsub,
                "has_sub_video": has_sub_video,
                "has_backup": has_backup,
                "backup_video_rel": os.path.relpath(backup_path, current_download_dir).replace("\\", "/") if has_backup else None,
                "best_video_rel": best_video_rel,
                "hardsub_video_rel": os.path.relpath(hardsub_vi_path if os.path.exists(hardsub_vi_path) else hardsub_bi_path, current_download_dir).replace("\\", "/") if has_hardsub else None,
                "has_dubbed_sub": has_dubbed_sub,
                "dubbed_sub_video_rel": os.path.relpath(dubbed_hardsub_vi_path if os.path.exists(dubbed_hardsub_vi_path) else dubbed_hardsub_bi_path, current_download_dir).replace("\\", "/") if has_dubbed_sub else None,
                "dubbed_video_rel": os.path.relpath(dubbed_mp4_path, current_download_dir).replace("\\", "/") if has_dubbed else None,
                "dubbed_audio_rel": os.path.relpath(dubbed_mp3_path, current_download_dir).replace("\\", "/") if os.path.exists(dubbed_mp3_path) else None,
                "thumb_url": f"/api/history/thumbnail/{urllib.parse.quote(rel_thumb)}",
                "stream_url": f"/api/history/stream/{urllib.parse.quote(rel_mp4)}"
            })

    # Sort newest first
    history.sort(key=lambda x: x["created_time"], reverse=True)
    return {"videos": history}

@app.get("/api/history/thumbnail/{filepath:path}")
async def get_history_thumbnail(filepath: str):
    full_path = _resolve_safe_path(filepath)
    if os.path.exists(full_path):
        return FileResponse(full_path, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="Thumbnail not found")

@app.get("/api/history/stream/{filepath:path}")
async def stream_history_video(request: Request, filepath: str):
    full_path = _resolve_safe_path(filepath)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Video file not found")
    # FileResponse handles HTTP Range requests (status 206) for timeline seeking
    return FileResponse(full_path, media_type="video/mp4")

def _delete_video_and_associated_files(filepath: str) -> List[str]:
    full_path = _resolve_safe_path(filepath)
    if not full_path.endswith(".mp4"):
        mp4_path = f"{full_path}.mp4"
    else:
        mp4_path = full_path

    dir_name = os.path.dirname(mp4_path)
    base_name = os.path.basename(mp4_path)[:-4]

    target_files = [
        mp4_path,
        os.path.join(dir_name, f"{base_name}.json"),
        os.path.join(dir_name, f"{base_name}.thumb.jpg"),
        os.path.join(dir_name, f"{base_name}.transcript.json"),
        os.path.join(dir_name, f"{base_name}.srt"),
        os.path.join(dir_name, f"{base_name}.vi.srt"),
        os.path.join(dir_name, f"{base_name}.bilingual.srt"),
        os.path.join(dir_name, f"{base_name}.transcript.txt"),
        os.path.join(dir_name, f"{base_name}.dubbed.mp4"),
        os.path.join(dir_name, f"{base_name}.dubbed.mp3"),
        os.path.join(dir_name, f"{base_name}.hardsub.vi.mp4"),
        os.path.join(dir_name, f"{base_name}.hardsub.bilingual.mp4"),
        os.path.join(dir_name, f"{base_name}.softsub.vi.mp4"),
        os.path.join(dir_name, f"{base_name}.softsub.bilingual.mp4"),
        os.path.join(dir_name, f"{base_name}.dubbed.hardsub.vi.mp4"),
        os.path.join(dir_name, f"{base_name}.dubbed.hardsub.bilingual.mp4"),
        os.path.join(dir_name, f"{base_name}.dubbed.softsub.vi.mp4"),
        os.path.join(dir_name, f"{base_name}.dubbed.softsub.bilingual.mp4"),
        os.path.join(dir_name, f"{base_name}.backup.mp4")
    ]

    deleted = []
    for p in target_files:
        if os.path.exists(p):
            try:
                os.remove(p)
                deleted.append(os.path.basename(p))
            except Exception as e:
                print(f"Error removing {p}: {e}")
    return deleted

@app.delete("/api/history/{filepath:path}")
async def delete_history_video(filepath: str):
    deleted = _delete_video_and_associated_files(filepath)
    if not deleted:
        raise HTTPException(status_code=404, detail="Không tìm thấy file để xóa")
    return {"status": "success", "deleted_files": deleted}

@app.post("/api/history/delete-batch")
async def delete_batch_history_videos(req: BatchDeleteRequest):
    all_deleted = []
    failed = []
    for fp in req.filepaths:
        try:
            d = _delete_video_and_associated_files(fp)
            if d:
                all_deleted.extend(d)
            else:
                failed.append(fp)
        except Exception as e:
            failed.append(f"{fp} ({str(e)})")
    return {
        "status": "success",
        "deleted_count": len(req.filepaths) - len(failed),
        "deleted_files": all_deleted,
        "failed": failed
    }

@app.post("/api/history/open-file")
async def open_history_file(req: OpenFileRequest):
    full_path = _resolve_safe_path(req.filepath)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File không tồn tại")
    try:
        if os.name == "nt":
            os.startfile(full_path)
        else:
            subprocess.run(["xdg-open", full_path])
        return {"status": "success", "opened": full_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi mở file: {str(e)}")

@app.post("/api/history/replace-original")
async def replace_original_video(req: ReplaceOriginalRequest):
    """Replace original .mp4 video with an edited version, saving a .backup.mp4 first."""
    target = req.filepath or req.target_rel
    if not target:
        raise HTTPException(status_code=400, detail="Vui lòng chỉ định video cần thay thế")

    orig_path = _resolve_safe_path(target)
    if not os.path.exists(orig_path):
        raise HTTPException(status_code=404, detail="File video gốc không tồn tại")
    
    rep_path = _resolve_safe_path(req.replacement_rel)
    if not os.path.exists(rep_path):
        raise HTTPException(status_code=404, detail="File video thay thế không tồn tại")

    dir_name = os.path.dirname(orig_path)
    base_name = os.path.basename(orig_path)[:-4]
    backup_path = os.path.join(dir_name, f"{base_name}.backup.mp4")
    thumb_path = os.path.join(dir_name, f"{base_name}.thumb.jpg")

    # 1. Create backup if it doesn't already exist
    backup_created = False
    if not os.path.exists(backup_path):
        shutil.copy2(orig_path, backup_path)
        backup_created = True

    # 2. Copy replacement video over original video
    shutil.copy2(rep_path, orig_path)

    # 3. Refresh first-frame thumbnail for original video
    try:
        DouyinDownloader.extract_first_frame(orig_path, thumb_path)
    except Exception as e:
        print(f"Error updating thumbnail: {e}")

    return {
        "status": "success",
        "message": "Đã thay thế video gốc thành công",
        "backup_created": backup_created,
        "backup_rel": os.path.relpath(backup_path, current_download_dir).replace("\\", "/"),
        "video_rel": os.path.relpath(orig_path, current_download_dir).replace("\\", "/")
    }

@app.post("/api/history/restore-original")
async def restore_original_video(req: RestoreOriginalRequest):
    """Restore original video from .backup.mp4."""
    target = req.filepath or req.target_rel
    if not target:
        raise HTTPException(status_code=400, detail="Vui lòng chỉ định video cần khôi phục")

    orig_path = _resolve_safe_path(target)
    dir_name = os.path.dirname(orig_path)
    base_name = os.path.basename(orig_path)[:-4]
    backup_path = os.path.join(dir_name, f"{base_name}.backup.mp4")
    thumb_path = os.path.join(dir_name, f"{base_name}.thumb.jpg")

    if not os.path.exists(backup_path):
        raise HTTPException(status_code=404, detail="Không tìm thấy file sao lưu (.backup.mp4)")

    # Restore backup over original video
    shutil.copy2(backup_path, orig_path)

    # Delete backup file now that original is restored
    try:
        os.remove(backup_path)
    except Exception as e:
        print(f"Error removing backup file: {e}")

    # Refresh thumbnail
    try:
        DouyinDownloader.extract_first_frame(orig_path, thumb_path)
    except Exception as e:
        print(f"Error updating thumbnail: {e}")

    return {
        "status": "success",
        "message": "Đã khôi phục video gốc từ bản sao lưu thành công",
        "video_rel": os.path.relpath(orig_path, current_download_dir).replace("\\", "/")
    }


# ----------------------------------------------------
# Transcribe & Translate APIs
# ----------------------------------------------------
@app.get("/api/transcribe/models")
async def get_transcribe_models():
    return {
        "models": [
            {"id": "tiny", "name": "Tiny (~75MB)", "desc": "Siêu nhanh, tốn rất ít RAM"},
            {"id": "base", "name": "Base (~145MB - Khuyên dùng)", "desc": "Rất nhanh trên CPU, chính xác tốt"},
            {"id": "small", "name": "Small (~460MB)", "desc": "Chính xác cao cho anime / phim lồng tiếng"}
        ]
    }

async def _bg_transcribe_task(video_path: str, task_id: str, model_size: str, language: str):
    await transcriber.transcribe_video(video_path, task_id=task_id, model_size=model_size, language=language)

@app.post("/api/transcribe")
async def trigger_transcribe(req: TranscribeRequest, background_tasks: BackgroundTasks):
    full_path = _resolve_safe_path(req.filepath)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File video không tồn tại")

    task_id = f"task_{uuid.uuid4().hex[:8]}"
    model_size = req.model_size or "base"
    language = req.language or "auto"

    background_tasks.add_task(_bg_transcribe_task, full_path, task_id, model_size, language)
    return {"status": "started", "task_id": task_id, "model_size": model_size}

@app.get("/api/transcribe/tasks")
async def get_transcribe_tasks():
    return transcriber.tasks

@app.get("/api/transcript/download/{sub_type}/{filepath:path}")
async def download_transcript_file(sub_type: str, filepath: str):
    full_path = _resolve_safe_path(filepath)
    base_name = os.path.splitext(full_path)[0]

    if sub_type == "vi":
        target = f"{base_name}.vi.srt"
        media = "application/x-subrip"
    elif sub_type == "bilingual":
        target = f"{base_name}.bilingual.srt"
        media = "application/x-subrip"
    elif sub_type == "txt":
        target = f"{base_name}.transcript.txt"
        media = "text/plain"
    else:
        target = f"{base_name}.srt"
        media = "application/x-subrip"

    if not os.path.exists(target):
        json_path = f"{base_name}.transcript.json"
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                segments = data.get("segments", [])
                if sub_type == "txt":
                    with open(target, "w", encoding="utf-8") as f:
                        f.write(DouyinTranscriber.generate_txt(segments, include_timestamps=True))
                elif sub_type == "vi":
                    with open(target, "w", encoding="utf-8") as f:
                        f.write(DouyinTranscriber.generate_srt(segments, "vietnamese"))
                elif sub_type == "bilingual":
                    bi_segments = []
                    for s in segments:
                        zh = s.get("chinese", "")
                        vi = s.get("vietnamese", "")
                        combined = f"{vi}\n{zh}" if vi else zh
                        bi_segments.append({**s, "text": combined})
                    with open(target, "w", encoding="utf-8") as f:
                        f.write(DouyinTranscriber.generate_srt(bi_segments, "text"))
                else:
                    with open(target, "w", encoding="utf-8") as f:
                        f.write(DouyinTranscriber.generate_srt(segments, "chinese"))
            except Exception as e:
                print(f"Error auto-generating {target}: {e}")

    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="File phụ đề/kịch bản chưa được tạo")

    return FileResponse(target, media_type=media, filename=os.path.basename(target))

@app.get("/api/transcript/{filepath:path}")
async def get_transcript(filepath: str):
    full_path = _resolve_safe_path(filepath)
    base_name = os.path.splitext(full_path)[0]
    json_path = f"{base_name}.transcript.json"
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="Chưa có bản thoại cho video này")
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi đọc bản thoại: {str(e)}")

@app.post("/api/translate/character-map")
async def generate_character_map(req: CharacterMapRequest):
    full_path = _resolve_safe_path(req.filepath)
    base_name = os.path.splitext(full_path)[0]
    json_path = f"{base_name}.transcript.json"
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="Vui lòng trích xuất giọng nói trước khi lập bảng phân vai")

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi mở file kịch bản: {str(e)}")

    segments = data.get("segments", [])
    if not segments:
        raise HTTPException(status_code=400, detail="Không có câu thoại nào để phân tích")

    cfg = load_app_config()
    provider = req.provider or cfg.get("default_ai_provider", "gemini")
    provider = provider.lower().strip()

    if provider in ["openai", "chatgpt"]:
        api_key = cfg.get("openai_api_key", os.environ.get("OPENAI_API_KEY", ""))
        model = req.model or cfg.get("openai_model") or (cfg.get("default_ai_model") if cfg.get("default_ai_provider") == "openai" else None) or "gpt-4o-mini"
        base_url = cfg.get("openai_base_url", "")
        if not api_key:
            raise HTTPException(status_code=400, detail="Chưa cấu hình ChatGPT/OpenAI API Key. Vui lòng bấm vào nút 'Cài đặt AI' trên thanh tiêu đề.")
    else:
        provider = "gemini"
        api_key = cfg.get("gemini_api_key", os.environ.get("GEMINI_API_KEY", ""))
        model = req.model or cfg.get("gemini_model") or (cfg.get("default_ai_model") if cfg.get("default_ai_provider") == "gemini" else None) or "gemini-2.5-flash"
        base_url = None
        if not api_key:
            raise HTTPException(status_code=400, detail="Chưa cấu hình Google Gemini API Key. Vui lòng bấm vào nút 'Cài đặt AI' trên thanh tiêu đề.")

    try:
        char_map = await translator.generate_character_map(
            segments,
            api_key=api_key,
            video_title=req.video_title or "",
            provider=provider,
            model=model,
            base_url=base_url
        )
        data["character_map"] = char_map
        data["ai_provider"] = provider
        data["ai_model"] = model
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"status": "success", "character_map": char_map, "provider": provider, "model": model}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/translate/execute")
async def execute_translation(req: TranslateRequest):
    full_path = _resolve_safe_path(req.filepath)
    base_name = os.path.splitext(full_path)[0]
    json_path = f"{base_name}.transcript.json"
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="Không tìm thấy kịch bản gốc để dịch")

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi mở file kịch bản: {str(e)}")

    segments = data.get("segments", [])
    cfg = load_app_config()
    provider = req.provider or cfg.get("default_ai_provider", "gemini")
    provider = provider.lower().strip()

    if provider in ["openai", "chatgpt"]:
        api_key = cfg.get("openai_api_key", os.environ.get("OPENAI_API_KEY", ""))
        model = req.model or cfg.get("openai_model") or (cfg.get("default_ai_model") if cfg.get("default_ai_provider") == "openai" else None) or "gpt-4o-mini"
        base_url = cfg.get("openai_base_url", "")
        if not api_key:
            raise HTTPException(status_code=400, detail="Chưa cấu hình ChatGPT/OpenAI API Key.")
    else:
        provider = "gemini"
        api_key = cfg.get("gemini_api_key", os.environ.get("GEMINI_API_KEY", ""))
        model = req.model or cfg.get("gemini_model") or (cfg.get("default_ai_model") if cfg.get("default_ai_provider") == "gemini" else None) or "gemini-2.5-flash"
        base_url = None
        if not api_key:
            raise HTTPException(status_code=400, detail="Chưa cấu hình Google Gemini API Key.")

    try:
        translated_segments = await translator.translate_with_character_map(
            segments,
            character_map=req.character_map,
            api_key=api_key,
            provider=provider,
            model=model,
            base_url=base_url
        )
        data["segments"] = translated_segments
        data["character_map"] = req.character_map
        data["ai_provider"] = provider
        data["ai_model"] = model

        # Save updated json
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Save Vietnamese SRT
        vi_srt_path = f"{base_name}.vi.srt"
        with open(vi_srt_path, "w", encoding="utf-8") as f:
            f.write(DouyinTranscriber.generate_srt(translated_segments, "vietnamese"))

        # Save Bilingual SRT
        bilingual_srt_path = f"{base_name}.bilingual.srt"
        bi_segments = []
        for s in translated_segments:
            zh = s.get("chinese", "")
            vi = s.get("vietnamese", "")
            combined = f"{vi}\n{zh}" if vi else zh
            bi_segments.append({**s, "text": combined})
        with open(bilingual_srt_path, "w", encoding="utf-8") as f:
            f.write(DouyinTranscriber.generate_srt(bi_segments, "text"))

        # Save Plain Text script
        txt_path = f"{base_name}.transcript.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(DouyinTranscriber.generate_txt(translated_segments, include_timestamps=True))

        return {"status": "success", "segments": translated_segments}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/translate/chunk")
async def translate_chunk_endpoint(req: TranslateChunkRequest):
    if not req.segments:
        return {"status": "success", "segments": []}

    cfg = load_app_config()
    provider = req.provider or cfg.get("default_ai_provider", "gemini")
    provider = provider.lower().strip()

    if provider in ["openai", "chatgpt"]:
        api_key = cfg.get("openai_api_key", os.environ.get("OPENAI_API_KEY", ""))
        model = req.model or cfg.get("openai_model") or (cfg.get("default_ai_model") if cfg.get("default_ai_provider") == "openai" else None) or "gpt-4o-mini"
        base_url = cfg.get("openai_base_url", "")
        if not api_key:
            raise HTTPException(status_code=400, detail="Chưa cấu hình ChatGPT/OpenAI API Key.")
    else:
        provider = "gemini"
        api_key = cfg.get("gemini_api_key", os.environ.get("GEMINI_API_KEY", ""))
        model = req.model or cfg.get("gemini_model") or (cfg.get("default_ai_model") if cfg.get("default_ai_provider") == "gemini" else None) or "gemini-2.5-flash"
        base_url = None
        if not api_key:
            raise HTTPException(status_code=400, detail="Chưa cấu hình Google Gemini API Key.")

    try:
        translated = await translator.translate_chunk(
            chunk_segments=req.segments,
            character_map=req.character_map or {},
            api_key=api_key,
            provider=provider,
            model=model,
            base_url=base_url
        )
        return {"status": "success", "segments": translated, "model": model, "provider": provider}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/transcript/save")
async def save_transcript(req: SaveTranscriptRequest):
    full_path = _resolve_safe_path(req.filepath)
    base_name = os.path.splitext(full_path)[0]
    json_path = f"{base_name}.transcript.json"

    try:
        data = {}
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        
        data["segments"] = req.segments
        if req.character_map:
            data["character_map"] = req.character_map

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Update SRT & TXT if translations exist
        has_vi = any(s.get("vietnamese") for s in req.segments)
        if has_vi:
            vi_srt_path = f"{base_name}.vi.srt"
            with open(vi_srt_path, "w", encoding="utf-8") as f:
                f.write(DouyinTranscriber.generate_srt(req.segments, "vietnamese"))

            bilingual_srt_path = f"{base_name}.bilingual.srt"
            bi_segments = []
            for s in req.segments:
                zh = s.get("chinese", "")
                vi = s.get("vietnamese", "")
                combined = f"{vi}\n{zh}" if vi else zh
                bi_segments.append({**s, "text": combined})
            with open(bilingual_srt_path, "w", encoding="utf-8") as f:
                f.write(DouyinTranscriber.generate_srt(bi_segments, "text"))

            txt_path = f"{base_name}.transcript.txt"
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(DouyinTranscriber.generate_txt(req.segments, include_timestamps=True))

        return {"status": "success", "message": "Đã lưu bản thoại"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi lưu bản thoại: {str(e)}")


# ----------------------------------------------------
# AI Voice Dubbing APIs
# ----------------------------------------------------
@app.get("/api/dub/voices")
async def get_dub_voices():
    """Return available TTS voices for dubbing."""
    return {"voices": dubber.get_available_voices()}

@app.post("/api/dub/preview")
async def preview_dub_voice(req: DubPreviewRequest):
    """Generate audio sample for voice preview."""
    try:
        cfg = load_app_config()
        openai_key = cfg.get("openai_api_key", os.environ.get("OPENAI_API_KEY", ""))
        openai_base = cfg.get("openai_base_url", "")
        
        audio_bytes = await dubber.synthesize_sentence(
            text=req.text or "Xin chào, đây là giọng đọc thử nghiệm lồng tiếng video.",
            voice=req.voice or "vi-VN-HoaiMyNeural",
            provider=req.provider or "edge",
            api_key=openai_key,
            base_url=openai_base
        )
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/dub/execute")
async def execute_dubbing(req: DubExecuteRequest):
    """Start asynchronous dubbing task."""
    full_path = _resolve_safe_path(req.filepath)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File video không tồn tại")

    base_name = os.path.splitext(full_path)[0]
    json_path = f"{base_name}.transcript.json"
    if not os.path.exists(json_path):
        raise HTTPException(status_code=400, detail="Chưa có bản thoại/phụ đề cho video này. Vui lòng trích xuất và dịch trước khi lồng tiếng.")

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            transcript_data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi đọc file phụ đề: {e}")

    cfg = load_app_config()
    openai_key = cfg.get("openai_api_key", os.environ.get("OPENAI_API_KEY", ""))
    openai_base = cfg.get("openai_base_url", "")

    task_id = str(uuid.uuid4())
    dubber.tasks[task_id] = {
        "status": "pending",
        "progress": 0,
        "message": "Đang xếp hàng tác vụ lồng tiếng...",
        "dubbed_video": None,
        "dubbed_audio": None,
        "error": None
    }

    async def _run():
        try:
            await dubber.execute_dubbing(
                video_path=full_path,
                transcript_data=transcript_data,
                mode=req.mode or "single",
                single_voice=req.single_voice or "vi-VN-HoaiMyNeural",
                character_voices=req.character_voices,
                bg_volume=req.bg_volume if req.bg_volume is not None else 0.15,
                voice_volume=req.voice_volume if req.voice_volume is not None else 1.2,
                openai_api_key=openai_key,
                openai_base_url=openai_base,
                task_id=task_id
            )

            # If user requested to burn subtitles onto dubbed video
            if req.burn_sub and dubber.tasks[task_id].get("dubbed_video"):
                dub_video = dubber.tasks[task_id]["dubbed_video"]
                if os.path.exists(dub_video):
                    lang = req.sub_lang or "vi"
                    srt_file = f"{base_name}.bilingual.srt" if lang == "bilingual" else f"{base_name}.vi.srt"
                    if not os.path.exists(srt_file):
                        srt_file = f"{base_name}.srt"
                    if os.path.exists(srt_file):
                        sub_mode = req.sub_type or "hardsub"
                        out_dub_sub = dub_video.replace(".dubbed.mp4", f".dubbed.{sub_mode}.{lang}.mp4")
                        dubber.tasks[task_id]["message"] = "Đang chèn phụ đề vào video lồng tiếng..."
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(
                            None,
                            subtitler.burn_subtitles_sync,
                            dub_video,
                            srt_file,
                            out_dub_sub,
                            sub_mode,
                            lang,
                            None,
                            req.font_size or 22,
                            req.font_color or "white",
                            req.position or "overlay_original",
                            req.mask_original if req.mask_original is not None else True
                        )
                        dubber.tasks[task_id]["dubbed_sub_video"] = out_dub_sub
                        dubber.tasks[task_id]["message"] = "Hoàn tất lồng tiếng và chèn phụ đề!"
        except Exception as e:
            print(f"Dubbing error for task {task_id}: {e}")

    asyncio.create_task(_run())
    return {"status": "success", "task_id": task_id}

@app.get("/api/dub/tasks/{task_id}")
async def get_dub_task(task_id: str):
    """Check status of a dubbing background task."""
    task = dubber.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tác vụ lồng tiếng không tồn tại")

    res = dict(task)
    if task.get("dubbed_video") and os.path.exists(task["dubbed_video"]):
        rel_video = os.path.relpath(task["dubbed_video"], current_download_dir).replace("\\", "/")
        res["dubbed_video_rel"] = rel_video
        res["dubbed_video_url"] = f"/api/history/stream/{urllib.parse.quote(rel_video)}"
    if task.get("dubbed_sub_video") and os.path.exists(task["dubbed_sub_video"]):
        rel_sub = os.path.relpath(task["dubbed_sub_video"], current_download_dir).replace("\\", "/")
        res["dubbed_sub_video_rel"] = rel_sub
        res["dubbed_sub_video_url"] = f"/api/history/stream/{urllib.parse.quote(rel_sub)}"
    if task.get("dubbed_audio") and os.path.exists(task["dubbed_audio"]):
        rel_audio = os.path.relpath(task["dubbed_audio"], current_download_dir).replace("\\", "/")
        res["dubbed_audio_rel"] = rel_audio
        res["dubbed_audio_url"] = f"/api/dub/download/audio/{urllib.parse.quote(rel_audio)}"
    return res

# ----------------------------------------------------
# Subtitle Export & Video Burning APIs (Hardsub / Softsub)
# ----------------------------------------------------
@app.post("/api/subtitles/export")
async def export_subtitle_video(req: SubtitleExportRequest):
    """Export video with burned hardsub or softsub stream."""
    full_path = _resolve_safe_path(req.filepath)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File video không tồn tại")

    base_name = os.path.splitext(full_path)[0]
    lang = req.language or "vi"
    sub_type = req.sub_type or "hardsub"

    if lang == "bilingual":
        srt_path = f"{base_name}.bilingual.srt"
    else:
        srt_path = f"{base_name}.vi.srt"

    if not os.path.exists(srt_path):
        srt_path = f"{base_name}.srt"
    if not os.path.exists(srt_path):
        raise HTTPException(status_code=400, detail="Chưa có file phụ đề cho video này. Vui lòng trích xuất và dịch trước.")

    # Determine audio source (Smart cascade: preserve dubbed audio if available)
    dubbed_video_path = f"{base_name}.dubbed.mp4"
    audio_source = (req.audio_source or "auto").lower()

    use_dubbed = False
    if audio_source in ("dubbed", "auto"):
        if os.path.exists(dubbed_video_path):
            use_dubbed = True
    elif audio_source in ("original", "orig"):
        # If dubbed video exists, smart cascade automatically keeps dubbed audio
        # unless user explicitly asked for 'force_original'
        if os.path.exists(dubbed_video_path):
            use_dubbed = True
    elif audio_source == "force_original":
        use_dubbed = False

    if use_dubbed:
        input_video = dubbed_video_path
        out_path = f"{base_name}.dubbed.{sub_type}.{lang}.mp4"
    else:
        input_video = full_path
        out_path = f"{base_name}.{sub_type}.{lang}.mp4"

    task_id = subtitler.start_subtitle_task(
        video_path=input_video,
        srt_path=srt_path,
        output_path=out_path,
        mode=sub_type,
        language=lang,
        font_size=req.font_size or 22,
        font_color=req.font_color or "white",
        position=req.position or "overlay_original",
        mask_original=req.mask_original if req.mask_original is not None else True
    )

    return {
        "status": "success",
        "task_id": task_id,
        "output_filename": os.path.basename(out_path)
    }

@app.get("/api/subtitles/tasks/{task_id}")
async def get_subtitle_task(task_id: str):
    """Get status of subtitle burning task."""
    task = subtitler.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tác vụ phụ đề không tồn tại")
    res = dict(task)
    if task.get("output_video") and os.path.exists(task["output_video"]):
        rel_video = os.path.relpath(task["output_video"], current_download_dir).replace("\\", "/")
        res["output_video_rel"] = rel_video
        res["output_video_url"] = f"/api/history/stream/{urllib.parse.quote(rel_video)}"
    return res

@app.get("/api/dub/download/{file_type}/{filepath:path}")
async def download_dub_file(file_type: str, filepath: str):
    """Download dubbed MP4 or MP3 file."""
    full_path = _resolve_safe_path(filepath)
    base_name = os.path.splitext(full_path)[0]
    if base_name.endswith(".dubbed"):
        base_name = base_name[:-7]
    if file_type == "video":
        target = f"{base_name}.dubbed.mp4"
        media = "video/mp4"
    elif file_type == "audio":
        target = f"{base_name}.dubbed.mp3"
        media = "audio/mpeg"
    elif file_type == "sub_video":
        # Look for dubbed subtitled video outputs
        candidates = [
            f"{base_name}.dubbed.hardsub.vi.mp4",
            f"{base_name}.dubbed.hardsub.bilingual.mp4",
            f"{base_name}.dubbed.softsub.vi.mp4",
            f"{base_name}.dubbed.softsub.bilingual.mp4"
        ]
        target = None
        for c in candidates:
            if os.path.exists(c):
                target = c
                break
        if not target:
            target = f"{base_name}.dubbed.mp4"
        media = "video/mp4"
    else:
        raise HTTPException(status_code=400, detail="Loại file không hợp lệ")

    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="File lồng tiếng chưa được tạo hoặc không tồn tại")
    return FileResponse(target, media_type=media, filename=os.path.basename(target))

@app.get("/api/subtitles/download/{filepath:path}")
async def download_subtitle_file(filepath: str):
    """Download subtitled video or subtitle file."""
    full_path = _resolve_safe_path(filepath)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File không tồn tại")
    media_type = "video/mp4" if full_path.endswith(".mp4") else "text/plain"
    return FileResponse(full_path, media_type=media_type, filename=os.path.basename(full_path))


# Serve Frontend
web_dir = os.path.join(BASE_DIR, "web")
os.makedirs(web_dir, exist_ok=True)

@app.get("/")
async def serve_index():
    index_file = os.path.join(web_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return JSONResponse({"message": "Douyin Video Downloader API is running."})

app.mount("/static", StaticFiles(directory=web_dir), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
