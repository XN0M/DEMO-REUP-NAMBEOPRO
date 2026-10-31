"""Guest downloads must not depend on the state of a stored login."""
import unittest
from unittest.mock import Mock, patch

from core import extractor_yt as yt
from core.youtube_session import YouTubeError, YouTubeQueue

URL = 'https://www.youtube.com/watch?v=ylw1aPULfpU'


class GuestAccess(unittest.TestCase):
    def setUp(self):
        self.queue = YouTubeQueue(interval=0)
        self.store = Mock()
        self.store.snapshot.return_value = {'version': 'test', 'cookies': []}
        for name, value in [('youtube_queue', self.queue), ('session_store', self.store)]:
            patcher = patch.object(yt, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_guest_success_ignores_broken_cookie_and_resumes_queue(self):
        self.queue.paused = True
        self.store.snapshot.side_effect = YouTubeError('youtube_session_invalid', 'Expired')
        with patch.object(yt.yt_dlp, 'YoutubeDL') as factory:
            action = Mock(return_value={'height': 2160})
            result = yt.YouTubeExtractor()._execute(URL, {}, action)
            self.assertEqual(list(factory.return_value.__enter__.return_value.cookiejar), [])
        self.assertEqual(result['height'], 2160)
        self.store.snapshot.assert_not_called()
        self.assertFalse(self.queue.paused)

    def test_auth_challenge_uses_saved_session_once(self):
        self.store.snapshot.return_value['cookies'] = ['synthetic']
        action = Mock(side_effect=[RuntimeError('Sign in to confirm you are not a bot'), 'ok'])
        with patch.object(yt.yt_dlp, 'YoutubeDL'), patch.object(yt, 'cookiejar') as jar:
            self.assertEqual(yt.YouTubeExtractor()._execute(URL, {}, action), 'ok')
        self.assertEqual(action.call_count, 2)
        self.assertEqual(jar.call_args_list[0].args, ([],))
        self.assertEqual(jar.call_args_list[1].args, (['synthetic'],))

    def test_network_failure_does_not_request_cookies(self):
        action = Mock(side_effect=OSError('Unable to download API page: getaddrinfo failed'))
        with patch.object(yt.yt_dlp, 'YoutubeDL'), patch.object(yt.time, 'sleep'):
            with self.assertRaises(YouTubeError) as error:
                yt.YouTubeExtractor()._execute(URL, {}, action)
        self.assertEqual(error.exception.code, 'youtube_network')
        self.assertEqual(action.call_count, 3)
        self.store.snapshot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
