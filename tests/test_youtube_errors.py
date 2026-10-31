"""YouTube failures must reach the UI instead of becoming empty results."""
import unittest
import tempfile
from unittest.mock import patch
from fastapi.testclient import TestClient
import server
from core.extractor_yt import YouTubeExtractor
from core.youtube_session import SessionStore, YouTubeQueue


class YouTubeErrors(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target, value in [('core.extractor_yt.session_store', SessionStore(self.temp.name)),
                              ('core.youtube_api.session_store', SessionStore(self.temp.name)),
                              ('core.extractor_yt.youtube_queue', YouTubeQueue(interval=0))]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
    def test_bot_challenge_is_actionable_in_parse_api(self):
        with patch('core.extractor_yt.yt_dlp.YoutubeDL') as factory:
            factory.return_value.__enter__.return_value.extract_info.side_effect = RuntimeError(
                '\x1b[0;31mERROR:\x1b[0m Sign in to confirm you’re not a bot')
            response = TestClient(server.app, base_url='http://127.0.0.1:8000',
                client=('127.0.0.1',45000), headers={'Origin':'http://127.0.0.1:8000'}).post('/api/parse', json={
                'urls': ['https://www.youtube.com/watch?v=uv_QG8c4MCY']})
        self.assertEqual(response.status_code, 400)
        self.assertIn('YouTube yêu cầu đăng nhập', response.json()['detail'])
        self.assertNotIn('\x1b', response.json()['detail'])

    def test_network_failure_is_not_swallowed(self):
        with patch('core.extractor_yt.yt_dlp.YoutubeDL') as factory, patch('core.extractor_yt.time.sleep'):
            factory.return_value.__enter__.return_value.extract_info.side_effect = OSError('Network unavailable')
            with self.assertRaisesRegex(RuntimeError, 'Không kết nối được tới YouTube'):
                YouTubeExtractor().parse_single_url_sync('https://www.youtube.com/watch?v=uv_QG8c4MCY')


if __name__ == '__main__':
    unittest.main()
