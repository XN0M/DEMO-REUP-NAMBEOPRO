import json
import unittest
from unittest.mock import patch, MagicMock

from core import douyin_monitor as d
from core.monitor_errors import MonitorError

UID = 'MS4w-public-user'
URL = 'https://www.douyin.com/user/' + UID


def post(vid='123', timestamp=100, owner=UID, pinned=False):
    return {'aweme_id': vid, 'desc': 'Public post', 'create_time': timestamp,
            'author': {'sec_uid': owner}, 'video': {'cover': {'url_list': ['https://example.test/cover.jpg']}},
            'is_top': pinned}


class GuestDouyin(unittest.TestCase):
    def test_profile_and_share_text_normalization(self):
        self.assertEqual(d.resolve_profile('分享主页 ' + URL + '?source=share'), (URL, UID))
        self.assertEqual(d.resolve_profile('['+URL+']('+URL+')'), (URL, UID))
        self.assertEqual(d.resolve_profile('https://www.iesdouyin.com/share/user/123?sec_uid='+UID), (URL, UID))
        with patch.object(d.httpx, 'Client') as client:
            response = client.return_value.__enter__.return_value.get.return_value
            response.is_redirect = True
            response.headers = {'location': URL + '?from=share'}
            self.assertEqual(d.resolve_profile('https://v.douyin.com/abc/'), (URL, UID))

    def test_video_tiktok_and_host_spoof_rejected(self):
        for url in ('https://www.douyin.com/video/123', 'https://www.tiktok.com/@someone',
                    'https://douyin.com.evil.test/user/test', 'https://douyin.com@evil.test/user/test'):
            with self.subTest(url=url), self.assertRaises(MonitorError):
                d.resolve_profile(url)

    def test_share_redirect_to_other_host_is_rejected(self):
        with patch.object(d.httpx, 'Client') as client:
            response = client.return_value.__enter__.return_value.get.return_value
            response.is_redirect = True
            response.headers = {'location':'https://evil.test/user/test'}
            with self.assertRaises(MonitorError):
                d.resolve_profile('https://v.douyin.com/test/')
            self.assertEqual(client.return_value.__enter__.return_value.get.call_count, 1)

    def test_only_matching_owner_posts_are_accepted(self):
        result = d.parse_post_list({'aweme_list':[post(), post('456', owner='other')], 'has_more':0}, UID)
        self.assertEqual([v['video_id'] for v in result.videos], ['123'])
        self.assertTrue(result.complete)
        self.assertIsNone(d.parse_post_list({'aweme_list':[post(owner='other')], 'has_more':0}, UID))

    def test_missing_metadata_never_becomes_false_empty_success(self):
        for payload in ({}, {'aweme_list':None}, {'aweme_list':[{}]}, {'aweme_list':[]},
                        {'aweme_list':[], 'status_code':1, 'has_more':0}):
            self.assertIsNone(d.parse_post_list(payload, UID, trusted=True))
        result = d.parse_post_list({'aweme_list':[{'aweme_id':'1'}], 'has_more':0}, UID, trusted=True)
        self.assertEqual(result.videos[0]['title'], 'Douyin Video 1')
        self.assertEqual(result.videos[0]['published'], '')

    def test_empty_requires_trusted_list_and_explicit_end(self):
        self.assertIsNone(d.parse_post_list({'aweme_list':[], 'has_more':0}, UID))
        result = d.parse_post_list({'aweme_list':[], 'has_more':0}, UID, trusted=True)
        self.assertEqual(result.videos, [])
        self.assertTrue(result.complete)

    def test_embedded_json_and_assignment_are_parsed_without_eval(self):
        payload = {'data':{'aweme_list':[post()], 'has_more':0}}
        for html in ('<script id="RENDER_DATA">'+json.dumps(payload)+'</script>',
                     '<script>window._ROUTER_DATA = '+json.dumps(payload)+';</script>'):
            results = d.embedded_lists(html, UID)
            self.assertEqual(results[0].videos[0]['video_id'], '123')

    def run_browser(self, payload, known=None, status=200, text='', timeout=False):
        clock = [0]
        def monotonic():
            clock[0] += 1
            return clock[0]
        with patch('playwright.sync_api.sync_playwright') as factory, patch.object(d.time, 'monotonic', side_effect=monotonic):
            playwright = factory.return_value.__enter__.return_value
            browser = playwright.chromium.launch.return_value
            context = browser.new_context.return_value
            page = context.new_page.return_value
            page.content.return_value = '<html></html>'
            page.locator.return_value.inner_text.return_value = text
            response = MagicMock()
            response.url = 'https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id='+UID
            response.status = status
            response.json.return_value = payload
            callbacks = {}
            page.on.side_effect = lambda event, fn: callbacks.update({event:fn})
            def goto(*args, **kwargs):
                if timeout:
                    from playwright.sync_api import TimeoutError
                    raise TimeoutError('synthetic timeout')
                callbacks['response'](response)
                return MagicMock(status=200)
            page.goto.side_effect = goto
            try:
                return d.scan_channel(URL, known_ids=known)
            finally:
                context.close.assert_called_once()
                browser.close.assert_called_once()
                context_args = browser.new_context.call_args.kwargs
                self.assertNotIn('storage_state', context_args)

    def test_browser_deduplicates_sorts_pinned_and_returns_partial(self):
        result = self.run_browser({'aweme_list':[post('1', 1, pinned=True), post('3', 3), post('3', 3)], 'has_more':1}, known={'1'})
        self.assertEqual([v['video_id'] for v in result.videos], ['3', '1'])
        self.assertFalse(result.complete)

    def test_browser_initial_twenty_and_repeat_boundary(self):
        payload = {'aweme_list':[post(str(i), i) for i in range(1, 26)], 'has_more':1}
        first = self.run_browser(payload)
        self.assertEqual(len(first.videos), 20)
        self.assertEqual(first.videos[0]['video_id'], '25')
        self.assertTrue(first.complete)
        repeat = self.run_browser(payload, known={'25'})
        self.assertTrue(repeat.complete)

    def test_browser_block_and_login_are_explicit_failures(self):
        for status, text, code in ((403, '', 'douyin_verification'), (200, '请先登录', 'douyin_login_required')):
            with self.subTest(code=code), self.assertRaises(MonitorError) as raised:
                self.run_browser({}, status=status, text=text)
            self.assertEqual(raised.exception.code, code)

    def test_timeout_and_browser_slot_are_released(self):
        with self.assertRaises(MonitorError) as raised:
            self.run_browser({}, timeout=True)
        self.assertEqual(raised.exception.code, 'douyin_timeout')
        self.assertTrue(d._browser_lock.acquire(blocking=False))
        d._browser_lock.release()


if __name__ == '__main__':
    unittest.main()
