"""Bilibili URL and quality-selection contracts without network or media costs."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yt_dlp

from core.downloader import DouyinDownloader
from core.extractor import DouyinExtractor
from core.extractor_bilibili import BilibiliExtractor
from core.extractor_yt import YouTubeExtractor


BV = 'BV1Pvp46CEQr'
CANONICAL = f'https://www.bilibili.com/video/{BV}/'


class FakeYoutubeDL:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def extract_info(self, url, download=False):
        assert url == CANONICAL and not download
        return {'id': BV, 'title': '中國的麵', 'uploader': '作者', 'duration': 52,
                'formats': [{'vcodec': 'avc1', 'width': 1280, 'height': 720},
                            {'vcodec': 'avc1', 'width': 1920, 'height': 1080, 'fps': 30}],
                'thumbnail': 'https://i0.hdslb.com/test.jpg'}

    def download(self, urls):
        assert urls == [CANONICAL]
        Path(self.options['outtmpl'].replace('%(ext)s', 'mp4')).write_bytes(b'merged sample')


class BilibiliTests(unittest.TestCase):
    def test_share_and_address_bar_urls_are_same_video(self):
        links = [
            f'【這可能是中國最豪橫的麵】 https://www.bilibili.com/video/{BV}/?share_source=copy_web',
            f'https://www.bilibili.com/video/{BV}/?trackid=web_pegasus&spm_id_from=333',
            f'【標題】 https\\://www\\.bilibili.com/video/{BV}/?share_source=copy_web',
        ]
        for link in links:
            self.assertEqual(BilibiliExtractor.clean_bilibili_url(link), CANONICAL)
            self.assertTrue(BilibiliExtractor.is_bilibili_url(link))
        self.assertFalse(BilibiliExtractor.is_bilibili_url(
            f'https://bilibili.com.evil.example/video/{BV}/'))

    def test_metadata_reports_best_available_resolution(self):
        with patch('core.extractor_bilibili.yt_dlp.YoutubeDL', FakeYoutubeDL):
            info = BilibiliExtractor().parse_single_url_sync(CANONICAL + '?share_source=copy_web')
        self.assertEqual((info['id'], info['platform'], info['resolution']),
                         (BV, 'bilibili', '1920x1080'))
        self.assertEqual(info['play_url'], CANONICAL)

    def test_download_requests_best_available_video_and_audio_without_resolution_cap(self):
        options = []
        def fake_dl(value):
            options.append(value)
            return FakeYoutubeDL(value)
        with tempfile.TemporaryDirectory() as directory, \
                patch('core.extractor_yt.yt_dlp.YoutubeDL', fake_dl):
            output = Path(directory) / 'sample.mp4'
            BilibiliExtractor().download_video_sync(CANONICAL + '?trackid=ignored', str(output))
            self.assertEqual(output.read_bytes(), b'merged sample')
        self.assertEqual(options[0]['format'], 'bestvideo+bestaudio/best')
        self.assertEqual(options[0]['merge_output_format'], 'mp4')
        self.assertFalse(any('height' in item for item in options[0]['format_sort']))

    def test_bilibili_http_412_is_explicit(self):
        with patch.object(FakeYoutubeDL, 'extract_info', side_effect=yt_dlp.utils.DownloadError('HTTP Error 412')):
            with patch('core.extractor_bilibili.yt_dlp.YoutubeDL', FakeYoutubeDL):
                with self.assertRaisesRegex(RuntimeError, 'HTTP 412'):
                    BilibiliExtractor().parse_single_url_sync(CANONICAL)

    def test_download_dispatch_uses_bilibili_extractor(self):
        with tempfile.TemporaryDirectory() as directory:
            downloader = DouyinDownloader(directory)
            info = {'id': BV, 'platform': 'bilibili', 'title': '中國的麵',
                    'author': '作者', 'play_url': CANONICAL}
            def fake_download(_self, url, output, progress):
                self.assertEqual(url, CANONICAL)
                Path(output).write_bytes(b'video sample')
                progress({'status': 'downloading', 'percent': 99})
            with patch.object(downloader, '_get_next_sequence', return_value=1), \
                    patch.object(BilibiliExtractor, 'download_video_sync', fake_download), \
                    patch.object(DouyinDownloader, 'extract_first_frame', return_value=True):
                result = asyncio.run(downloader.download_video(info))
            self.assertEqual(result['status'], 'completed')
            self.assertTrue(Path(result['file_path']).is_file())

    def test_blocked_download_leaves_no_empty_library_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            downloader = DouyinDownloader(directory)
            info = {'id': BV, 'platform': 'bilibili', 'title': '中國的麵',
                    'author': '作者', 'play_url': CANONICAL}
            with patch.object(downloader, '_get_next_sequence', return_value=1), \
                    patch.object(BilibiliExtractor, 'download_video_sync',
                                 side_effect=RuntimeError('HTTP 412')):
                result = asyncio.run(downloader.download_video(info))
            self.assertEqual(result['status'], 'failed')
            self.assertIn('412', result['error'])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_aggregate_routes_to_bilibili(self):
        extractor = DouyinExtractor()
        expected = {'id': BV, 'platform': 'bilibili'}
        with patch.object(extractor.bilibili_extractor, 'parse_single_url',
                          new_callable=AsyncMock, return_value=expected) as parse:
            self.assertEqual(asyncio.run(extractor.parse_single_url(
                f'【標題】 {CANONICAL}?share_source=copy_web')), expected)
            parse.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
