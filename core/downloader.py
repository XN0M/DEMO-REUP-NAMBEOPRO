import asyncio
import json
import os
import re
import time
import urllib.request
from typing import Callable, Dict, Optional

class DouyinDownloader:
    def __init__(self, default_download_dir: str):
        self.default_download_dir = default_download_dir
        os.makedirs(self.default_download_dir, exist_ok=True)
        self.tasks: Dict[str, Dict] = {}

    def sanitize_filename(self, text: str, max_len: int = 60) -> str:
        """Remove invalid Windows characters and limit length."""
        # Replace invalid Windows filename characters
        safe = re.sub(r'[\\/*?:"<>|]', "", text)
        # Collapse spaces and underscores
        safe = re.sub(r'\s+', " ", safe).strip()
        safe = safe[:max_len].strip()
        return safe or "douyin_video"

    @staticmethod
    def extract_first_frame(video_path: str, thumb_path: str) -> bool:
        """Extract first frame of an mp4 video using OpenCV with Unicode path support."""
        if not os.path.exists(video_path):
            return False
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            ret, frame = cap.read()
            cap.release()
            if ret and frame is not None:
                ok, buf = cv2.imencode(".jpg", frame)
                if ok:
                    with open(thumb_path, "wb") as f:
                        f.write(buf)
                    return True
        except Exception as e:
            print(f"Error extracting first frame from {video_path}: {e}")
        return False


    @staticmethod
    def get_video_duration(video_path: str) -> float:
        """Get accurate duration of an mp4 video using OpenCV."""
        if not os.path.exists(video_path):
            return 0.0
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            cap.release()
            if fps and fps > 0 and frame_count > 0:
                return round(frame_count / fps, 1)
        except Exception as e:
            print(f"Error calculating duration for {video_path}: {e}")
        return 0.0

    def get_task(self, video_id: str) -> Optional[Dict]:
        return self.tasks.get(video_id)

    def get_all_tasks(self) -> Dict[str, Dict]:
        return self.tasks

    async def download_video(
        self,
        video_info: Dict,
        target_dir: Optional[str] = None,
        collection: Optional[str] = None,
        on_progress: Optional[Callable[[Dict], None]] = None
    ) -> Dict:
        """Download a video by video_info dictionary asynchronously into specified collection folder."""
        video_id = video_info["id"]
        base_dir = target_dir or self.default_download_dir
        
        if collection and collection.strip() and collection.strip() != "Chung":
            clean_col = self.sanitize_filename(collection.strip(), max_len=30)
            save_dir = os.path.join(base_dir, clean_col)
        else:
            save_dir = base_dir
        os.makedirs(save_dir, exist_ok=True)

        os.makedirs(save_dir, exist_ok=True)

        author = self.sanitize_filename(video_info.get("author", "user"), max_len=20)
        title = self.sanitize_filename(video_info.get("title", f"video_{video_id}"), max_len=50)
        base_name = f"[{author}]_{title}_{video_id}"
        
        mp4_filename = f"{base_name}.mp4"
        json_filename = f"{base_name}.json"
        
        mp4_path = os.path.join(save_dir, mp4_filename)
        json_path = os.path.join(save_dir, json_filename)

        task_data = {
            "id": video_id,
            "title": video_info.get("title", ""),
            "author": video_info.get("author", ""),
            "status": "downloading",
            "percent": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "file_path": mp4_path,
            "json_path": json_path,
            "speed": "0 KB/s",
            "error": None
        }
        self.tasks[video_id] = task_data

        # Save metadata JSON first
        try:
            data_to_save = dict(video_info)
            if "raw_detail" in video_info and isinstance(video_info["raw_detail"], dict):
                data_to_save.update(video_info["raw_detail"])
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Failed to save metadata json: {e}")

        # Download stream in thread pool to avoid blocking asyncio event loop
        thumb_path = os.path.join(save_dir, f"{base_name}.thumb.jpg")
        loop = asyncio.get_running_loop()
        try:
            if video_info.get("platform") == "youtube":
                from core.extractor_yt import YouTubeExtractor
                yt_extractor = YouTubeExtractor()
                def _yt_prog(d):
                    for k, v in d.items():
                        task_data[k] = v
                    if on_progress:
                        on_progress(task_data)
                await loop.run_in_executor(
                    None,
                    yt_extractor.download_video_sync,
                    video_info["play_url"],
                    mp4_path,
                    _yt_prog
                )
            else:
                await loop.run_in_executor(
                    None,
                    self._sync_download,
                    video_info["play_url"],
                    mp4_path,
                    task_data,
                    on_progress
                )
            # Extract first frame
            await loop.run_in_executor(None, self.extract_first_frame, mp4_path, thumb_path)
            task_data["status"] = "completed"
            task_data["percent"] = 100.0
            task_data["speed"] = "Done"
            task_data["thumb_path"] = thumb_path
        except Exception as e:
            task_data["status"] = "failed"
            task_data["error"] = str(e)
            print(f"Error downloading {video_id}: {e}")

        if on_progress:
            on_progress(task_data)

        return task_data

    def _sync_download(
        self,
        url: str,
        dest_path: str,
        task_data: Dict,
        on_progress: Optional[Callable[[Dict], None]]
    ):
        referer = "https://www.facebook.com/" if ("fbcdn.net" in url or "facebook.com" in url) else "https://www.douyin.com/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Referer": referer
        }
        req = urllib.request.Request(url, headers=headers)
        
        with urllib.request.urlopen(req, timeout=30) as resp, open(dest_path, "wb") as out_f:
            total_length = resp.headers.get("content-length")
            total_bytes = int(total_length) if total_length else 0
            task_data["total_bytes"] = total_bytes

            downloaded = 0
            start_time = time.time()
            last_report = start_time

            chunk_size = 65536
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out_f.write(chunk)
                downloaded += len(chunk)
                task_data["downloaded_bytes"] = downloaded

                now = time.time()
                if total_bytes > 0:
                    task_data["percent"] = round((downloaded / total_bytes) * 100, 1)

                if now - last_report >= 0.5:
                    speed = downloaded / (now - start_time + 0.001)
                    if speed > 1024 * 1024:
                        task_data["speed"] = f"{speed / (1024 * 1024):.1f} MB/s"
                    else:
                        task_data["speed"] = f"{speed / 1024:.1f} KB/s"
                    last_report = now
                    if on_progress:
                        on_progress(task_data)
