import os
import re
import asyncio
import time
from urllib.parse import urlsplit, parse_qs
from .youtube_session import (session_store, youtube_queue, youtube_host, cookiejar,
                              classify, QuietLogger, YouTubeError)
from typing import Dict, Optional, Callable
import yt_dlp
import imageio_ffmpeg

class YouTubeExtractor:
    """Extractor and downloader for YouTube videos using yt-dlp."""
    use_youtube_session = True

    def __init__(self):
        self.ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    @staticmethod
    def is_youtube_url(url: str) -> bool:
        """Check if URL belongs to YouTube."""
        if not url:
            return False
        return youtube_host(url)

    @staticmethod
    def clean_youtube_url(url: str) -> str:
        """Strip playlist / radio mix parameters to focus on single video."""
        if not url:
            return ""
        u = url.strip()
        if not youtube_host(u):
            raise YouTubeError('youtube_url', 'Hãy nhập đường dẫn video youtube.com hoặc youtu.be hợp lệ.')
        parsed = urlsplit(u)
        parts = parsed.path.strip('/').split('/')
        identity = (parts[0] if parsed.hostname.lower() == 'youtu.be' else
                    parse_qs(parsed.query).get('v', [''])[0] if parsed.path == '/watch' else
                    parts[1] if len(parts) == 2 and parts[0] in ('shorts','live','embed') else '')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{11}', identity):
            raise YouTubeError('youtube_url', 'Nhập link một video YouTube, Shorts hoặc livestream cụ thể; chưa hỗ trợ link kênh/danh sách.')
        return f'https://www.youtube.com/watch?v={identity}'

    def _build_base_opts(self) -> dict:
        """Shared yt-dlp options: Node.js runtime (chống bóp băng thông n-sig YouTube),
        Android + Web client fallback để tối đa hóa tốc độ tải và độ ổn định stream."""
        return {
            'ffmpeg_location': self.ffmpeg_path,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            # Kích hoạt Node.js runtime để giải mã YouTube n-sig,
            # ngăn YouTube bóp tốc độ xuống ~35 KB/s
            'js_runtimes': {'node': {}},
        }

    def _execute(self, url, options, action, progress=None, check=False, on_snapshot=None):
        if not self.use_youtube_session:
            with yt_dlp.YoutubeDL(options) as ydl:
                return action(ydl)
        if not youtube_host(url):
            raise YouTubeError('youtube_url', 'Đường dẫn video YouTube không hợp lệ.')
        # A fresh request must be able to try public access even after a previous
        # authenticated request paused the queue. Keep FIFO spacing intact.
        with youtube_queue.lease(progress, check=True) as generation:
            snapshot = session_store.snapshot() if check else {'cookies': []}
            if on_snapshot:
                on_snapshot(snapshot['version'])
            if progress:
                progress({'status': 'downloading', 'speed': 'Đang kết nối YouTube'})
            options = {**options, 'noprogress': True,
                'socket_timeout': 20, 'retries': 0, 'extractor_retries': 0,
                'fragment_retries': 0, 'file_access_retries': 0}
            used_fallback = check
            network_attempts = 0
            while True:
                logger = QuietLogger()
                try:
                    with yt_dlp.YoutubeDL({**options, 'logger': logger}) as ydl:
                        ydl.cookiejar = cookiejar(snapshot['cookies'])
                        result = action(ydl)
                    if snapshot['cookies'] and logger.invalid_session:
                        raise YouTubeError('youtube_session_invalid', 'YouTube báo phiên đã hết hiệu lực. Hãy xuất và nhập lại cookie.')
                    if check and not result:
                        raise YouTubeError('youtube_extract_failed', 'Không đọc được metadata video YouTube.')
                    if generation == youtube_queue.generation:
                        youtube_queue.resume()
                    return result
                except Exception as error:
                    safe = classify(error)
                    if not used_fallback and safe.code in ('youtube_auth_required', 'youtube_restricted'):
                        used_fallback = True
                        snapshot = session_store.snapshot()
                        if snapshot['cookies']:
                            continue
                    if safe.code != 'youtube_network' or network_attempts == 2:
                        raise safe from None
                    network_attempts += 1
                    time.sleep(max(youtube_queue.interval, 2 ** network_attempts))

    def parse_single_url_sync(self, raw_url: str, check=False, on_snapshot=None) -> Optional[Dict]:
        """Extract YouTube video metadata synchronously."""
        clean_url = self.clean_youtube_url(raw_url)
        ydl_opts = {
            **self._build_base_opts(),
            'skip_download': True,
        }
        info = self._execute(clean_url, ydl_opts,
            lambda ydl: ydl.extract_info(clean_url, download=False),
            check=check, on_snapshot=on_snapshot)
        if not info:
            return None

        vid_id = info.get("id") or f"yt_{abs(hash(clean_url))}"
        title = info.get("title") or "YouTube Video"
        author = info.get("uploader") or info.get("channel") or "YouTube Creator"
        duration = info.get("duration") or 0

        # Thumbnail - ưu tiên maxresdefault để chất lượng cao nhất
        cover_url = info.get("thumbnail") or f"https://i.ytimg.com/vi/{vid_id}/maxresdefault.jpg"

        # Resolution - lấy từ luồng đã chọn thực tế
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
            "play_url": clean_url,  # Truyền nguyên URL cho downloader yt-dlp
            "duration": duration,
            "resolution": resolution,
            "created_time": "",
            "raw_url": raw_url.strip()
        }

    async def parse_single_url(self, raw_url: str) -> Optional[Dict]:
        """Extract YouTube video metadata asynchronously in a thread."""
        return await asyncio.to_thread(self.parse_single_url_sync, raw_url)

    def download_video_sync(
        self,
        url: str,
        output_mp4_path: str,
        on_progress: Optional[Callable[[Dict], None]] = None
    ):
        """Download best H.264 video + AAC audio and mux into output_mp4_path with yt-dlp.
        Ưu tiên định dạng MP4 (H.264/AVC + AAC) để tương thích tối đa với:
        - Trình phát web HTML5
        - OpenCV (trích xuất thumbnail)
        - Pipeline Whisper STT và FFmpeg Audio Ducking phía sau
        """
        clean_url = self.clean_youtube_url(url)
        outtmpl_no_ext = os.path.splitext(output_mp4_path)[0]

        # Theo dõi tổng bytes ghép đôi (video + audio riêng biệt)
        _stream_totals: Dict[str, int] = {}
        _stream_downloaded: Dict[str, int] = {}

        def _hook(d):
            if not on_progress:
                return
            status = d.get("status")
            if status == "downloading":
                ctx_id = str(d.get("ctx_id", "default"))
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                speed = d.get("speed") or 0

                _stream_totals[ctx_id] = total
                _stream_downloaded[ctx_id] = downloaded

                # Tổng hợp tiến độ từ cả luồng video và audio (nếu tải song song)
                all_total = sum(_stream_totals.values())
                all_downloaded = sum(_stream_downloaded.values())
                percent = round((all_downloaded / all_total) * 100, 1) if all_total > 0 else 0.0

                speed_str = ""
                if speed > 0:
                    if speed > 1024 * 1024:
                        speed_str = f"{speed / (1024 * 1024):.1f} MB/s"
                    elif speed > 1024:
                        speed_str = f"{speed / 1024:.0f} KB/s"
                    else:
                        speed_str = f"{speed:.0f} B/s"

                on_progress({
                    "status": "downloading",
                    "percent": percent,
                    "downloaded_bytes": all_downloaded,
                    "total_bytes": all_total,
                    "speed": speed_str
                })
            elif status == "finished":
                # Luồng đơn đã tải xong, đang chờ ghép âm thanh
                on_progress({
                    "status": "merging",
                    "percent": 98.0,
                    "speed": "Đang ghép âm thanh..."
                })

        ydl_opts = {
            **self._build_base_opts(),
            # Luôn giữ độ phân giải gốc cao nhất có sẵn (4K/2K/1080p/720p - tuyệt đối không ép chất lượng xuống),
            # ưu tiên H.264 (AVC) và AAC audio khi có cùng độ phân giải để tối ưu tương thích
            'format': 'bestvideo+bestaudio/best',
            'format_sort': ['res', 'vcodec:avc', 'acodec:m4a'],
            'merge_output_format': 'mp4',
            'outtmpl': f"{outtmpl_no_ext}.%(ext)s",
            'progress_hooks': [_hook],
            'overwrites': True,
        }

        code = self._execute(clean_url, ydl_opts, lambda ydl: ydl.download([clean_url]), on_progress)
        if code:
            raise YouTubeError('youtube_download_failed', 'Bộ tải chưa hoàn tất video. Hãy kiểm tra phiên và thử lại.')

        # Đảm bảo file đầu ra đúng tên đích
        expected_path = f"{outtmpl_no_ext}.mp4"
        if os.path.exists(expected_path) and os.path.normcase(expected_path) != os.path.normcase(output_mp4_path):
            os.replace(expected_path, output_mp4_path)

        # Dọn dẹp các file trung gian của yt-dlp (*.f123.mp4, *.f140.m4a, ...)
        target_dir = os.path.dirname(output_mp4_path)
        base_name = os.path.basename(outtmpl_no_ext)
        if os.path.exists(target_dir):
            for f in os.listdir(target_dir):
                if f.startswith(base_name) and re.search(r'\.f\d+\.(mp4|m4a|webm)$', f):
                    try:
                        os.remove(os.path.join(target_dir, f))
                    except Exception:
                        pass
