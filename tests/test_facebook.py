"""Facebook contracts use synthetic credentials and mocked HTTP only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import server
from core import fb_poster as fb, monitor
from core.monitor_errors import MonitorError


def response(data=None, status=200, text=None):
    kwargs = {'text':text} if text is not None else {'json':data}
    return httpx.Response(status, request=httpx.Request('POST','https://graph.facebook.com/test'), **kwargs)


class Facebook(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nambeo-facebook-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root/'sample.mp4'
        self.video.write_bytes(b'1234567890')
        for module, name, value in ((monitor,'FB_CONFIG_FILE',self.root/'fb.json'),
                                    (server,'current_download_dir',str(self.root)),
                                    (fb,'_active_uploads',set())):
            p = patch.object(module,name,value);p.start();self.addCleanup(p.stop)
        monitor.save_fb_config({'page_id':'123','page_access_token':'synthetic-secret','page_name':'Test'})
        self.client = TestClient(server.app)

    def test_blank_token_preserves_saved_value_and_page_change_requires_new(self):
        result = self.client.post('/api/fb/config',json={'page_id':'123','page_access_token':'','page_name':'Renamed'})
        self.assertEqual(result.status_code,200)
        self.assertEqual(monitor.load_fb_config()['page_access_token'],'synthetic-secret')
        result = self.client.post('/api/fb/config',json={'page_id':'456','page_name':'Other'})
        self.assertEqual(result.status_code,422)
        self.assertEqual(monitor.load_fb_config()['page_id'],'123')
        public = self.client.get('/api/fb/config').text
        self.assertNotIn('synthetic-secret',public)
        self.assertNotIn('c-secret',public)

    def test_small_upload_requires_confirmed_id(self):
        for payload in ({}, {'id':''}, {'id':None}):
            with self.subTest(payload=payload), patch.object(fb.httpx,'post',return_value=response(payload)) as post:
                result = self.client.post('/api/fb/post',json={'mp4_path':'sample.mp4'})
                self.assertEqual(result.status_code,502)
                self.assertEqual(post.call_count,1)
        with patch.object(fb.httpx,'post',return_value=response({'id':'987'})):
            result = self.client.post('/api/fb/post',json={'mp4_path':'sample.mp4'})
        self.assertEqual(result.json(),{'success':True,'video_id':'987','page_id':'123'})

    def test_http_json_and_graph_errors_are_sanitized(self):
        cases = (response({'error':{'message':'synthetic-secret','code':190}},400),
                 response({},500), response(text='synthetic-secret not json'), response([]))
        for payload in cases:
            with self.subTest(payload=payload.status_code), patch.object(fb.httpx,'post',return_value=payload):
                result = self.client.post('/api/fb/post',json={'mp4_path':'sample.mp4'})
                self.assertEqual(result.status_code,502)
                self.assertNotIn('synthetic-secret',result.text)

    def test_timeout_is_unknown_and_never_retried(self):
        with patch.object(fb.httpx,'post',side_effect=httpx.ReadTimeout('synthetic-secret')) as post:
            result = self.client.post('/api/fb/post',json={'mp4_path':'sample.mp4'})
        self.assertEqual(result.status_code,504)
        self.assertEqual(result.json()['code'],'facebook_outcome_unknown')
        self.assertEqual(post.call_count,1)
        self.assertNotIn('synthetic-secret',result.text)

    def test_resumable_progress_and_finish_confirmation(self):
        replies = [response({'upload_session_id':'s','video_id':'987','start_offset':'0','end_offset':'4'}),
                   response({'start_offset':'4','end_offset':'10'}), response({'start_offset':'10','end_offset':'10'}),
                   response({'success':True})]
        progress = []
        with patch.object(fb,'SMALL_FILE_LIMIT',1), patch.object(fb.httpx,'post',side_effect=replies):
            result = fb.post_video_to_facebook(self.video,on_progress=lambda a,b:progress.append((a,b)))
        self.assertEqual(result['video_id'],'987')
        self.assertEqual(progress,[(4,10),(10,10)])

    def test_resumable_rejects_stuck_out_of_bounds_and_missing_finish(self):
        for transfer in ({'start_offset':0,'end_offset':10}, {'start_offset':11,'end_offset':12},
                         {'start_offset':'bad','end_offset':10}):
            replies = [response({'upload_session_id':'s','video_id':'987','start_offset':0,'end_offset':10}), response(transfer)]
            with self.subTest(transfer=transfer), patch.object(fb,'SMALL_FILE_LIMIT',1), \
                 patch.object(fb.httpx,'post',side_effect=replies) as post:
                with self.assertRaises(MonitorError):
                    fb.post_video_to_facebook(self.video)
                self.assertEqual(post.call_count,2)
        replies = [response({'upload_session_id':'s','video_id':'987','start_offset':0,'end_offset':10}),
                   response({'start_offset':10,'end_offset':10}),response({'success':False})]
        with patch.object(fb,'SMALL_FILE_LIMIT',1),patch.object(fb.httpx,'post',side_effect=replies):
            with self.assertRaises(MonitorError) as raised:
                fb.post_video_to_facebook(self.video)
        self.assertEqual(raised.exception.code,'facebook_outcome_unknown')

    def test_duplicate_upload_is_rejected_and_lock_released(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set();release.wait(3)
            return '987'
        with patch.object(fb,'_upload_small',side_effect=slow),ThreadPoolExecutor(1) as pool:
            future = pool.submit(fb.post_video_to_facebook,self.video)
            try:
                self.assertTrue(entered.wait(1))
                result = self.client.post('/api/fb/post',json={'mp4_path':'sample.mp4'})
                self.assertEqual(result.status_code,409)
            finally:
                release.set()
            future.result(3)
        self.assertEqual(fb._active_uploads,set())

    def test_connection_validates_page_id_and_reports_network_failure(self):
        with patch.object(fb.httpx,'get',return_value=response({'id':'123','name':'Test','fan_count':10})):
            self.assertTrue(self.client.post('/api/fb/test').json()['ok'])
        with patch.object(fb.httpx,'get',return_value=response({'id':'456','name':'Other'})):
            self.assertEqual(self.client.post('/api/fb/test').status_code,502)
        with patch.object(fb.httpx,'get',side_effect=httpx.ConnectError('synthetic-secret')):
            result = self.client.post('/api/fb/test')
        self.assertEqual(result.status_code,502)
        self.assertNotIn('synthetic-secret',result.text)

    def test_slow_upload_leaves_api_loop_responsive(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set();release.wait(3)
            return {'success':True,'video_id':'987'}
        async def scenario():
            with patch.object(fb,'post_video_to_facebook',side_effect=slow):
                task=asyncio.create_task(server.api_fb_post(server.FbPostRequest(mp4_path='sample.mp4')))
                try:
                    for _ in range(100):
                        if entered.is_set():break
                        await asyncio.sleep(.005)
                    self.assertTrue(entered.is_set())
                    self.assertFalse(task.done())
                    self.assertTrue((await server.api_fb_get_config())['token_set'])
                finally:
                    release.set();await task
        asyncio.run(scenario())


if __name__=='__main__':
    unittest.main()
