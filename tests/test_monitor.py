"""Offline monitoring regressions; no real accounts, channels, or credentials."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import server
from core import monitor as m
from core.monitor_errors import MonitorError, ScanResult


class Monitor(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nambeo-monitor-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in [('CHANNELS_FILE', self.root/'channels.json'), ('VIDEOS_FILE', self.root/'videos.json'),
                            ('FB_CONFIG_FILE', self.root/'fb.json'), ('_new_video_callbacks', []), ('_active_checks', set())]:
            p = patch.object(m, name, value)
            p.start(); self.addCleanup(p.stop)
        self.client = TestClient(server.app)

    def channel(self, platform='youtube', **extra):
        return m.add_channel(dict(platform=platform, url='https://www.youtube.com/channel/UCtest',
                                 name='Test', channel_id='UCtest', **extra))

    def video(self, ch, vid='new'):
        return m._video(ch, vid, title='Test', url='https://example.test/video', published='2026-10-26')

    def test_import_does_not_start_monitor_or_touch_network(self):
        code = ('from unittest.mock import patch; '
                'p=patch("httpx.get", side_effect=AssertionError("unexpected network")); p.start(); '
                'import server; from core import monitor; '
                'assert monitor._scheduler is None; '
                'assert any(getattr(r,"path","")=="/api/channels" for r in server.app.routes)')
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(server.__file__).parent,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lifecycle_starts_once_and_stops_thread(self):
        with patch.object(m, '_run_monitor_cycle') as cycle:
            try:
                with TestClient(server.app):
                    first = m._scheduler
                    m.start_scheduler()
                    self.assertIs(m._scheduler, first)
                    self.assertTrue(first.is_alive())
                self.assertFalse(first.is_alive())
                self.assertIsNone(m._scheduler)
                self.assertGreaterEqual(cycle.call_count, 1)
            finally:
                m.stop_scheduler()

    def test_startup_failure_returns_nonzero(self):
        import start_app
        original = __import__
        def fail(name, *args, **kwargs):
            if name == 'server':
                raise RuntimeError('synthetic startup failure')
            return original(name, *args, **kwargs)
        with patch.object(start_app, 'DATA_ROOT', self.root), patch.object(start_app, 'INSTANCE_FILE', self.root/'instance.json'), \
             patch.object(start_app, '_claim_instance', return_value=(None, False)), patch.object(start_app, '_notice'), \
             patch('builtins.__import__', side_effect=fail), patch('sys.argv', ['start_app.py']), \
             patch('traceback.print_exc'):
            self.assertEqual(start_app.main(), 1)

    def test_server_start_failure_returns_nonzero_and_closes_socket(self):
        import start_app
        with patch.object(start_app, 'DATA_ROOT', self.root), patch.object(start_app, 'INSTANCE_FILE', self.root/'instance.json'), \
             patch.object(start_app, '_claim_instance', return_value=(None, False)), \
             patch.object(start_app.socket, 'socket') as socket_factory, \
             patch.object(start_app.uvicorn, 'Server') as service_factory, \
             patch.dict(start_app.os.environ, {'NAMBEO_NO_BROWSER':'1'}), patch('sys.argv', ['start_app.py']):
            socket_factory.return_value.getsockname.return_value = ('127.0.0.1', 9999)
            service_factory.return_value.started = False
            self.assertEqual(start_app.main(), 1)
            socket_factory.return_value.close.assert_called_once()
            self.assertFalse((self.root/'instance.json').exists())

    def test_atomic_write_failure_preserves_video_and_known_ids(self):
        ch = self.channel()
        m.save_videos_data({'videos': [], 'known_video_ids': {}})
        before = m.VIDEOS_FILE.read_bytes()
        with patch.object(m.os, 'replace', side_effect=OSError('synthetic disk error')):
            with self.assertRaises(OSError):
                m._commit_scan(ch, ScanResult([self.video(ch)]))
        self.assertEqual(m.VIDEOS_FILE.read_bytes(), before)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_concurrent_ingestion_does_not_lose_videos(self):
        ch = self.channel()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: m._commit_scan(ch, ScanResult([self.video(ch, str(i))])), range(40)))
        data = m.load_videos_data()
        self.assertEqual(len(data['videos']), 40)
        self.assertEqual(len(data['known_video_ids'][m.channel_key(ch)]), 40)

    def test_initial_douyin_keeps_twenty_and_repeat_is_idempotent(self):
        ch = self.channel('douyin', sec_uid='profile')
        videos = [self.video(ch, str(i)) | {'published': f'2026-10-{i:02}'} for i in range(1, 26)]
        with patch.dict(m._CHECKERS, {'douyin': lambda c: ScanResult(videos)}):
            first = m.check_channel(ch)
            second = m.check_channel(ch)
        self.assertEqual(first['new_videos_found'], 20)
        self.assertEqual(first['videos'][0]['video_id'], '25')
        self.assertEqual(second['new_videos_found'], 0)
        self.assertEqual(len(m.get_pending_videos()), 20)

    def test_scan_error_is_not_success_and_records_backoff(self):
        ch = self.channel()
        seen = []
        m.register_new_video_callback(lambda rows: seen.append(rows))
        with patch.dict(m._CHECKERS, {'youtube': lambda c: (_ for _ in ()).throw(RuntimeError('fake-token'))}):
            for delay in (15, 30, 60):
                response = self.client.post('/api/channels/'+ch['id']+'/check')
                self.assertEqual(response.status_code, 502)
                self.assertNotIn('fake-token', response.text)
                current = m.load_channels()[0]
                self.assertIsNone(current['last_success'])
                self.assertEqual(current['last_status'], 'error')
                from datetime import datetime, timezone
                minutes = (datetime.fromisoformat(current['next_retry_at'])-datetime.now(timezone.utc)).total_seconds()/60
                self.assertAlmostEqual(minutes, delay, delta=.1)
        self.assertEqual(len(seen), 3)
        with patch.dict(m._CHECKERS, {'youtube': lambda c: ScanResult()}):
            result = m.check_channel(ch)
        self.assertEqual(result['status'], 'complete')
        self.assertIsNone(m.load_channels()[0]['last_error'])
        self.assertIsNone(m.load_channels()[0]['next_retry_at'])

    def test_partial_scan_keeps_videos_and_reports_partial(self):
        ch = self.channel()
        with patch.dict(m._CHECKERS, {'youtube': lambda c: ScanResult([self.video(ch)], False, 'partial')}):
            result = self.client.post('/api/channels/'+ch['id']+'/check')
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()['complete'])
        self.assertEqual(len(m.get_pending_videos()), 1)

    def test_same_channel_scan_conflict_and_release(self):
        ch = self.channel()
        entered, release = threading.Event(), threading.Event()
        def slow(c):
            entered.set(); release.wait(3)
            return ScanResult()
        with patch.dict(m._CHECKERS, {'youtube': slow}), ThreadPoolExecutor(1) as pool:
            future = pool.submit(m.check_channel, ch)
            try:
                self.assertTrue(entered.wait(1))
                response = self.client.post('/api/channels/'+ch['id']+'/check')
                self.assertEqual(response.status_code, 409)
            finally:
                release.set()
            future.result(3)
        self.assertFalse(m.load_channels()[0]['checking'])

    def test_check_all_retains_success_when_other_channel_fails(self):
        first = self.channel()
        self.channel('facebook', page_id='123')
        with patch.dict(m._CHECKERS, {'youtube': lambda c: ScanResult([self.video(c)]),
                                      'facebook': lambda c: (_ for _ in ()).throw(MonitorError('fake_error', 'fake failure'))}):
            response = self.client.post('/api/channels/check-all')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['new_videos_found'], 1)
        self.assertEqual([r['status'] for r in response.json()['results']], ['complete', 'error'])

    def test_rss_empty_or_known_never_calls_ytdlp(self):
        ch = self.channel()
        xml = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title></feed>'
        response = httpx.Response(200, text=xml, request=httpx.Request('GET', 'https://www.youtube.com'))
        with patch.object(m.httpx, 'get', return_value=response), patch('yt_dlp.YoutubeDL') as fallback:
            self.assertEqual(m.check_youtube(ch).videos, [])
            fallback.assert_not_called()

    def test_corrupt_data_is_not_silently_replaced(self):
        m.CHANNELS_FILE.write_text('{broken', encoding='utf-8')
        with self.assertRaises(MonitorError):
            self.channel()
        self.assertEqual(m.CHANNELS_FILE.read_text(), '{broken')

    def test_tiktok_and_invalid_interval_are_rejected(self):
        response = self.client.post('/api/channels', json={'platform':'douyin','url':'https://www.tiktok.com/@test'})
        self.assertEqual(response.status_code, 422)
        response = self.client.post('/api/channels', json={'platform':'douyin','url':'https://www.douyin.com/user/test','check_interval_minutes':1})
        self.assertEqual(response.status_code, 422)

    def test_dismissed_legacy_rows_are_hidden_without_rewriting_data(self):
        ch = self.channel()
        m.save_videos_data({'videos':[self.video(ch, 'old') | {'uuid':'old','status':'dismissed'},
                                     self.video(ch, 'new') | {'uuid':'new','status':'pending'}],
                            'known_video_ids':{}})
        before = m.VIDEOS_FILE.read_bytes()
        rows = self.client.get('/api/channels/videos').json()['videos']
        self.assertEqual([v['uuid'] for v in rows], ['new'])
        self.assertEqual(m.VIDEOS_FILE.read_bytes(), before)

    def test_delete_rows_preserves_files_channels_and_other_rows(self):
        ch = self.channel()
        local = self.root/'downloaded.mp4'
        local.write_bytes(b'video-must-be-preserved')
        m.save_videos_data({'videos':[self.video(ch, 'a') | {'uuid':'a','status':'downloaded','file_path':str(local)},
                                     self.video(ch, 'b') | {'uuid':'b','status':'pending'}],
                            'known_video_ids':{m.channel_key(ch):['a','b']}})
        response = self.client.delete('/api/channels/videos/a')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['removed_count'], 1)
        self.assertEqual(local.read_bytes(), b'video-must-be-preserved')
        self.assertEqual(len(m.load_channels()), 1)
        data = m.load_videos_data()
        self.assertEqual([v['uuid'] for v in data['videos']], ['b'])
        self.assertEqual([v['uuid'] for v in data['pending_videos']], ['b'])
        self.assertEqual(data['known_video_ids'][m.channel_key(ch)], ['a','b'])
        self.assertEqual(self.client.delete('/api/channels/videos/a').status_code, 404)

    def test_bulk_delete_snapshot_skips_active_and_leaves_other_channels(self):
        first = self.channel()
        second = self.channel()
        rows = [self.video(first, vid) | {'uuid':vid, 'status':status} for vid,status in
                [('a','pending'),('b','downloaded'),('busy','pending'),('busy-row','downloading')]]
        rows.append(self.video(second, 'other') | {'uuid':'other','status':'pending'})
        m.save_videos_data({'videos':rows,'known_video_ids':{}})
        with patch.object(server.downloader, 'get_all_tasks', return_value={'busy':{'status':'downloading'}}):
            response = self.client.post('/api/channels/videos/delete-batch',
                                        json={'video_uuids':['a','a','b','busy','busy-row','missing']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['removed_count'], 2)
        self.assertEqual(set(response.json()['skipped_ids']), {'busy','busy-row'})
        self.assertEqual(response.json()['missing_ids'], ['missing'])
        self.assertEqual({v['uuid'] for v in m.get_channel_videos()}, {'busy','busy-row','other'})

    def test_removed_items_stay_suppressed_after_scan_cache_eviction(self):
        ch = self.channel()
        scan = ScanResult([self.video(ch, 'removed')])
        row = m._commit_scan(ch, scan)[0]
        self.client.delete('/api/channels/videos/'+row['uuid'])
        data = m.load_videos_data()
        data['known_video_ids'] = {}  # Simulate rolling cache eviction/restart.
        m.save_videos_data(data)
        self.assertEqual(m._commit_scan(ch, scan), [])
        added = m._commit_scan(ch, ScanResult([self.video(ch, 'fresh')]))
        self.assertEqual([v['video_id'] for v in added], ['fresh'])

    def test_legacy_delete_endpoint_and_legacy_row_ids(self):
        ch = self.channel()
        m.save_videos_data({'videos':[self.video(ch, 'legacy') | {'status':'pending'}], 'known_video_ids':{}})
        response = self.client.delete('/api/channels/pending/legacy')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['dismissed'])
        self.assertEqual(m.get_channel_videos(), [])
        self.assertEqual(m._commit_scan(ch, ScanResult([self.video(ch, 'legacy')])), [])

    def test_delete_failure_keeps_rows_and_suppression_atomic(self):
        ch = self.channel()
        row = m._commit_scan(ch, ScanResult([self.video(ch)]))[0]
        before = m.VIDEOS_FILE.read_bytes()
        with patch.object(m.os, 'replace', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                m.delete_channel_videos([row['uuid']])
        self.assertEqual(m.VIDEOS_FILE.read_bytes(), before)
        self.assertEqual(len(m.get_pending_videos()), 1)

    def test_delete_api_rejects_empty_batch_and_single_active_row(self):
        self.assertEqual(self.client.post('/api/channels/videos/delete-batch',json={'video_uuids':[]}).status_code,422)
        ch = self.channel()
        row = m._commit_scan(ch, ScanResult([self.video(ch, 'busy')]))[0]
        with patch.object(server.downloader, 'get_all_tasks', return_value={'busy':{'status':'downloading'}}):
            self.assertEqual(self.client.delete('/api/channels/videos/'+row['uuid']).status_code,409)
        self.assertEqual(len(m.get_channel_videos()),1)

    def test_slow_scan_does_not_block_api_event_loop(self):
        ch = self.channel()
        entered, release = threading.Event(), threading.Event()
        def slow(c):
            entered.set(); release.wait(3)
            return {'complete': True}
        async def scenario():
            with patch.object(m, 'check_channel', side_effect=slow):
                task = asyncio.create_task(server.api_check_channel_now(ch['id']))
                try:
                    # Wait asynchronously for the worker; the event loop must stay responsive.
                    for _ in range(100):
                        if entered.is_set(): break
                        await asyncio.sleep(.005)
                    self.assertTrue(entered.is_set())
                    self.assertFalse(task.done())
                    self.assertEqual(len((await server.api_get_channels())['channels']), 1)
                finally:
                    release.set()
                    await task
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
