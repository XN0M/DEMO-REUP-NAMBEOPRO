"""Bilibili single-video support using the existing yt-dlp download pipeline."""
import asyncio
from pathlib import Path
import re
from typing import Dict, Optional

import yt_dlp

from core.extractor_yt import YouTubeExtractor


class BilibiliExtractor(YouTubeExtractor):
    use_youtube_session = False
    VIDEO_URL = re.compile(
        r'https?://(?:www\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10})(?:[/?#]|$)',
        re.IGNORECASE,
    )

    @classmethod
    def clean_bilibili_url(cls, raw_url: str) -> Optional[str]:
        # Copy/share text may include a title and Markdown escape characters.
        text = re.sub(r'\\([:/._?&=#-])', r'\1', raw_url or '')
        match = cls.VIDEO_URL.search(text)
        return f'https://www.bilibili.com/video/{match.group(1)}/' if match else None

    @classmethod
    def is_bilibili_url(cls, raw_url: str) -> bool:
        return cls.clean_bilibili_url(raw_url) is not None

    @classmethod
    def clean_youtube_url(cls, raw_url: str) -> str:
        """Reuse the shared yt-dlp downloader with a canonical Bilibili URL."""
        clean_url = cls.clean_bilibili_url(raw_url)
        if not clean_url:
            raise ValueError('Link Bilibili không hợp lệ')
        return clean_url

    def parse_single_url_sync(self, raw_url: str) -> Optional[Dict]:
        clean_url = self.clean_youtube_url(raw_url)
        try:
            with yt_dlp.YoutubeDL({**self._build_base_opts(), 'skip_download': True}) as ydl:
                info = ydl.extract_info(clean_url, download=False)
        except yt_dlp.utils.DownloadError as error:
            if '412' in str(error):
                raise RuntimeError('Bilibili đang chặn truy cập (HTTP 412). '
                    'Không thể xác minh chất lượng hoặc tải video từ kết nối hiện tại; '
                    'hãy thử lại khi video mở được trên trình duyệt cùng mạng.') from error
            raise RuntimeError(f'Không đọc được video Bilibili: {error}') from error
        if not info:
            return None
        formats = [item for item in info.get('formats', [])
                   if item.get('vcodec') not in (None, 'none') and item.get('height')]
        best = max(formats, key=lambda item: (item.get('height') or 0,
                    item.get('fps') or 0, item.get('tbr') or 0), default={})
        width = best.get('width') or info.get('width')
        height = best.get('height') or info.get('height')
        return {
            'id': str(info.get('id') or clean_url.rstrip('/').split('/')[-1]),
            'platform': 'bilibili',
            'title': info.get('title') or 'Bilibili Video',
            'author': info.get('uploader') or 'Bilibili Creator',
            'author_avatar': '',
            'cover_url': info.get('thumbnail') or '',
            'play_url': clean_url,
            'duration': info.get('duration') or 0,
            'resolution': f'{width}x{height}' if width and height else 'Chưa rõ',
            'created_time': '',
            'raw_url': raw_url.strip(),
        }

    async def parse_single_url(self, raw_url: str) -> Optional[Dict]:
        return await asyncio.to_thread(self.parse_single_url_sync, raw_url)

    def download_video_sync(self, url, output_mp4_path, on_progress=None):
        try:
            return super().download_video_sync(url, output_mp4_path, on_progress)
        except Exception as error:
            # The destination is a new per-video folder. Keep failed DASH parts out
            # of the library so a retry can start cleanly.
            output = Path(output_mp4_path)
            for piece in output.parent.glob(output.stem + '.*'):
                if piece.is_file() and (piece.name == output.name or
                        piece.suffix in ('.part', '.ytdl') or
                        re.search(r'\.f\d+\.(?:mp4|m4a|webm)$', piece.name)):
                    piece.unlink(missing_ok=True)
            if '412' in str(error):
                raise RuntimeError('Bilibili chặn lượt tải (HTTP 412); file chưa được lưu. '
                    'Hãy thử lại khi video mở được trên trình duyệt cùng mạng.') from error
            raise
