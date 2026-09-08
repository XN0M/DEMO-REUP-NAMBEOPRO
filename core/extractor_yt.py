import os
import re
import asyncio
from typing import Dict, Optional, Callable
import yt_dlp
import imageio_ffmpeg

class YouTubeExtractor:
    """Extractor and downloader for YouTube videos using yt-dlp."""

    def __init__(self):
        self.ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    @staticmethod
    def is_youtube_url(url: str) -> bool:
        """Check if URL belongs to YouTube."""
        if not url:
            return False
        u = url.lower().strip()
        return "youtube.com" in u or "youtu.be" in u

    @staticmethod
    def clean_youtube_url(url: str) -> str:
        """Strip playlist / radio mix parameters to focus on single video."""
        if not url:
            return ""
        u = url.strip()
        # Check standard watch?v=...
        m = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", u)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"
        # Check youtu.be/...
        m_short = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", u)
        if m_short:
            return f"https://www.youtube.com/watch?v={m_short.group(1)}"
        # Check shorts/...
        m_shorts = re.search(r"/shorts/([a-zA-Z0-9_-]{11})", u)
        if m_shorts:
            return f"https://www.youtube.com/watch?v={m_shorts.group(1)}"
        return u

    def parse_single_url_sync(self, raw_url: str) -> Optional[Dict]:
        """Extract YouTube video metadata synchronously."""
        clean_url = self.clean_youtube_url(raw_url)
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'noplaylist': True,
            'ffmpeg_location': self.ffmpeg_path
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(clean_url, download=False)
                if not info:
                    return None
                
                vid_id = info.get("id") or f"yt_{abs(hash(clean_url))}"
                title = info.get("title") or "YouTube Video"
                author = info.get("uploader") or info.get("channel") or "YouTube Creator"
                duration = info.get("duration") or 0
                
                # Thumbnail
                cover_url = info.get("thumbnail") or f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
                
                # Resolution
                width = info.get("width")
                height = info.get("height")
                resolution = f"{width}x{height}" if width and height else (f"{height}p" if height else "HD")
                
                return {
                    "id": str(vid_id),
                    "platform": "youtube",
                    "title": title,
                    "author": author,
                    "author_avatar": "",
                    "cover_url": cover_url,
                    "play_url": clean_url, # Pass to yt-dlp downloader
                    "duration": duration,
                    "resolution": resolution,
                    "created_time": "",
                    "raw_url": raw_url.strip()
                }
            except Exception as e:
                print(f"[YouTubeExtractor] Parse error: {e}")
                return None

    async def parse_single_url(self, raw_url: str) -> Optional[Dict]:
        """Extract YouTube video metadata asynchronously in a thread."""
        return await asyncio.to_thread(self.parse_single_url_sync, raw_url)

    def download_video_sync(
        self,
        url: str,
        output_mp4_path: str,
        on_progress: Optional[Callable[[Dict], None]] = None
    ):
        """Download best video + audio and mux into output_mp4_path with yt-dlp."""
        clean_url = self.clean_youtube_url(url)
        outtmpl_no_ext = os.path.splitext(output_mp4_path)[0]

        def _hook(d):
            if on_progress:
                status = d.get("status")
                if status == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    downloaded = d.get("downloaded_bytes", 0)
                    speed = d.get("speed", 0)
                    percent = round((downloaded / total) * 100, 1) if total > 0 else 0.0
                    
                    speed_str = ""
                    if speed and speed > 0:
                        if speed > 1024 * 1024:
                            speed_str = f"{speed / (1024*1024):.1f} MB/s"
                        else:
                            speed_str = f"{speed / 1024:.0f} KB/s"
                    
                    on_progress({
                        "status": "downloading",
                        "percent": percent,
                        "downloaded_bytes": downloaded,
                        "total_bytes": total,
                        "speed": speed_str
                    })
                elif status == "finished":
                    on_progress({
                        "status": "merging",
                        "percent": 98.0,
                        "speed": "Đang ghép âm thanh..."
                    })

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            'outtmpl': f"{outtmpl_no_ext}.%(ext)s",
            'ffmpeg_location': self.ffmpeg_path,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [_hook],
            'overwrites': True
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([clean_url])

        expected_path = f"{outtmpl_no_ext}.mp4"
        if os.path.exists(expected_path) and expected_path != output_mp4_path:
            os.replace(expected_path, output_mp4_path)
