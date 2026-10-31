"""Synthetic cookies, mocked YouTube, and real Windows protection; no account access."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import server
from core import extractor_yt as yt, youtube_api as api
from core.extractor_bilibili import BilibiliExtractor
from core.youtube_session import (SessionStore, YouTubeQueue, YouTubeError,
    parse_cookies, cookiejar, classify, MAX_BYTES, youtube_host, dpapi)

URL = 'https://www.youtube.com/watch?v=uv_QG8c4MCY'
FAKE_SECRET = 'synthetic-cookie-not-an-account'


def content(expiry=0, domain='.youtube.com', value=FAKE_SECRET):
    return (f'# Netscape HTTP Cookie File\n#HttpOnly_{domain}\tTRUE\t/\tTRUE\t{expiry}\tSID\t{value}\n'
            '.example.com\tTRUE\t/\tTRUE\t0\tOTHER\tDO-NOT-KEEP\n').encode()


class CookieParsing(unittest.TestCase):
    def test_only_youtube_and_httponly_survive(self):
        parsed = parse_cookies(content())
        self.assertEqual(len(parsed), 1)
        self.assertTrue(parsed[0]['http_only'])
        jar = cookiejar(parsed)
        self.assertIn(FAKE_SECRET, jar.get_cookie_header(URL))
        self.assertFalse(jar.get_cookie_header('https://www.bilibili.com/'))
        self.assertFalse(jar.get_cookie_header('https://youtube.com.evil.example/'))

    def test_invalid_expired_oversized_and_foreign_only(self):
        for payload in (b'not cookies', content(expiry=1), content(domain='.example.com'),
                        content(value='bad\x01value'), b'x'*(MAX_BYTES+1)):
            with self.subTest(size=len(payload)), self.assertRaises(YouTubeError):
                parse_cookies(payload)

    def test_hosts_and_errors_do_not_expose_details(self):
        for url in ('https://youtube.com.evil.example/watch?v=uv_QG8c4MCY',
                    'https://youtube.com@evil.example/', 'file:///youtube.com/watch'):
            self.assertFalse(youtube_host(url))
            with self.assertRaises(YouTubeError):
                yt.YouTubeExtractor.clean_youtube_url(url)
        for message, expected in [('Sign in to confirm you are not a bot', 'youtube_auth_required'),
                                  ('cookies are no longer valid', 'youtube_session_invalid'),
                                  ('Private video', 'youtube_restricted'),
                                  ('Network unavailable', 'youtube_network')]:
            error=classify(RuntimeError(message+' '+FAKE_SECRET))
            self.assertEqual(error.code, expected)
            self.assertNotIn(FAKE_SECRET, str(error))


@unittest.skipUnless(os.name == 'nt', 'Real DPAPI is Windows-specific')
class SessionIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nambeo-youtube-test-')
        self.addCleanup(self.temp.cleanup)
        self.store = SessionStore(Path(self.temp.name)/'session')
        self.queue = YouTubeQueue(interval=0)
        for module in (yt, api):
            for name, value in (('session_store',self.store), ('youtube_queue',self.queue)):
                patcher=patch.object(module,name,value);patcher.start();self.addCleanup(patcher.stop)
        self.client = TestClient(server.app, base_url='http://127.0.0.1:8000', client=('127.0.0.1',45678),
            headers={'Origin':'http://127.0.0.1:8000','Sec-Fetch-Site':'same-origin'})
        token=self.client.get('/api/youtube/session').json()['csrf_token']
        self.client.headers['X-Nambeo-CSRF']=token

    def test_real_encryption_reload_invalid_import_and_atomic_failure(self):
        first=self.store.import_file(content())
        path=self.store.root/'session.bin';before=path.read_bytes()
        self.assertNotIn(FAKE_SECRET.encode(),before)
        self.assertNotIn(b'DO-NOT-KEEP',dpapi(before, decrypt=True))
        reloaded=SessionStore(self.store.root)
        self.assertEqual(reloaded.snapshot()['cookies'][0]['value'],FAKE_SECRET)
        for payload in (b'bad format',content(expiry=1)):
            with self.assertRaises(YouTubeError):self.store.import_file(payload)
            self.assertEqual(path.read_bytes(),before)
        with patch('core.youtube_session.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.store.import_file(content(value='replacement'))
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(sorted(p.name for p in self.store.root.iterdir()),['session.bin'])
        with patch('core.youtube_session.dpapi',side_effect=YouTubeError('youtube_session_invalid','Other Windows user')):
            self.assertTrue(self.store.status()['storage_error'])
            with self.assertRaises(YouTubeError):self.store.snapshot()
        self.assertEqual(self.store.status()['version'],first['version'])

    def test_snapshot_changes_delete_and_late_result(self):
        first=self.store.import_file(content());snapshot=self.store.snapshot()
        self.store.enable(False)
        self.assertFalse(self.store.snapshot()['cookies'])
        self.assertEqual(snapshot['cookies'][0]['value'],FAKE_SECRET)
        self.store.record_check(first['version'],{'state':'accessible','message':'old'})
        self.assertIsNone(self.store.status()['last_check'])
        self.store.delete()
        self.assertFalse(self.store.status()['stored'])
        self.assertEqual(snapshot['cookies'][0]['value'],FAKE_SECRET)

    def test_guest_check_persists_without_recreating_deleted_session(self):
        snapshot=self.store.snapshot()
        outcome={'state':'network_error','message':'Mock network failure'}
        self.assertTrue(self.store.record_check(snapshot['version'],outcome))
        self.assertEqual(SessionStore(self.store.root).status()['last_check']['state'],'network_error')
        self.store.delete()
        self.assertFalse(self.store.record_check(snapshot['version'],outcome))
        self.assertIsNone(self.store.status()['last_check'])

    def test_api_rejects_cross_origin_dns_rebinding_csrf_and_oversize(self):
        self.assertEqual(self.client.get('/api/youtube/session',headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/youtube/session',headers={'Host':'evil.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/youtube/session',content=content(),headers={'X-Nambeo-CSRF':'wrong'}).status_code,403)
        remote=TestClient(server.app,base_url='http://127.0.0.1:8000',client=('192.168.1.2',12345),headers={'Origin':'http://127.0.0.1:8000'})
        self.assertEqual(remote.get('/api/youtube/session').status_code,403)
        response=self.client.post('/api/youtube/session',content=content())
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn(FAKE_SECRET,response.text)
        self.assertEqual(self.client.post('/api/youtube/session',content=b'x'*(MAX_BYTES+1)).status_code,413)
        self.assertEqual(self.client.post('/api/youtube/session',content=b'bad').status_code,400)
        self.assertEqual(self.store.snapshot()['cookies'][0]['value'],FAKE_SECRET)
        # Metadata endpoints cannot expose a logged-in session to another website.
        self.assertEqual(self.client.post('/api/parse',json={'urls':[URL]},headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/download',json={'video_ids':['mock'], 'videos':[{'id':'mock','platform':'youtube'}]},headers={'Origin':'https://evil.example'}).status_code,403)

    def fake_factory(self, seen, failure=None):
        class Fake:
            def __init__(self, options):self.options=options;self.cookiejar=None
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def extract_info(self,url,download=False):
                seen.append((copy.deepcopy(self.options),self.cookiejar))
                if failure:raise failure
                return {'id':'uv_QG8c4MCY','title':'<b>Mock title</b>','width':1920,'height':1080}
            def download(self,urls):
                seen.append((copy.deepcopy(self.options),self.cookiejar))
                Path(self.options['outtmpl'].replace('%(ext)s','mp4')).write_bytes(b'mock media')
                return 0
        return Fake

    def test_parse_download_share_cookie_policy_bilibili_never_reads_session(self):
        self.store.import_file(content());seen=[]
        with patch.object(yt.yt_dlp,'YoutubeDL',self.fake_factory(seen)):
            result=yt.YouTubeExtractor().parse_single_url_sync(URL)
            yt.YouTubeExtractor().download_video_sync(URL,str(Path(self.temp.name)/'output.mp4'))
            with patch.object(self.store,'snapshot',side_effect=AssertionError('Bilibili must not read YouTube cookies')):
                BilibiliExtractor().parse_single_url_sync('https://www.bilibili.com/video/BV1Pvp46CEQr/')
                BilibiliExtractor().download_video_sync('https://www.bilibili.com/video/BV1Pvp46CEQr/',str(Path(self.temp.name)/'bili.mp4'))
        self.assertEqual(result['resolution'],'1920x1080')
        for options, jar in seen[:2]:
            self.assertFalse(jar.get_cookie_header(URL))
            self.assertNotIn('cookiefile',options)
            self.assertNotIn('cookiesfrombrowser',options)
        self.assertEqual(seen[1][0]['format'],'bestvideo+bestaudio/best')
        self.assertTrue(all(jar is None for _,jar in seen[2:]))

    def test_network_retry_limit_and_auth_pause_are_sanitized(self):
        seen=[]
        with patch.object(yt.yt_dlp,'YoutubeDL',self.fake_factory(seen, OSError('Network '+FAKE_SECRET))),patch.object(yt.time,'sleep'):
            with self.assertRaises(YouTubeError) as error:yt.YouTubeExtractor().parse_single_url_sync(URL)
        self.assertEqual(len(seen),3);self.assertNotIn(FAKE_SECRET,str(error.exception))
        seen=[]
        with patch.object(yt.yt_dlp,'YoutubeDL',self.fake_factory(seen,RuntimeError('Sign in to confirm you are not a bot '+FAKE_SECRET))):
            with self.assertRaises(YouTubeError):yt.YouTubeExtractor().parse_single_url_sync(URL)
            with self.assertRaises(YouTubeError) as paused:yt.YouTubeExtractor().parse_single_url_sync(URL)
        self.assertEqual(paused.exception.code,'youtube_auth_required');self.assertEqual(len(seen),2)
        self.queue.resume();self.assertFalse(self.queue.paused)

    def test_warning_about_expired_session_is_not_guest_success(self):
        self.store.import_file(content())
        def action(ydl):
            ydl.params['logger'].warning('Your account cookies are no longer valid '+FAKE_SECRET)
            return {'id':'guest-result'}
        with self.assertRaises(YouTubeError) as error:
            yt.YouTubeExtractor()._execute(URL,{},action,check=True)
        self.assertEqual(error.exception.code,'youtube_session_invalid')
        self.assertNotIn(FAKE_SECRET,str(error.exception))

    def test_check_job_persists_only_current_snapshot_and_no_media(self):
        self.store.import_file(content());seen=[]
        with patch.object(yt.yt_dlp,'YoutubeDL',self.fake_factory(seen)):
            result=self.client.post('/api/youtube/session/check',json={'url':URL})
            self.assertEqual(result.status_code,200,result.text)
            identity=result.json()['id'];deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                job=self.client.get('/api/youtube/session/check/'+identity).json()
                if job['status']=='completed':break
                time.sleep(.02)
        self.assertEqual(job['result']['state'],'accessible')
        self.assertNotIn(FAKE_SECRET,json.dumps(job))
        self.assertTrue(seen[0][0]['skip_download'])
        self.assertEqual(SessionStore(self.store.root).status()['last_check']['state'],'accessible')

    def test_partial_parse_keeps_success_and_download_failure_is_paused(self):
        self.queue.paused=True
        info={'id':'mock-keep','platform':'youtube','play_url':URL}
        with patch.object(server.extractor,'parse_single_url',AsyncMock(side_effect=[info,YouTubeError('youtube_paused','Paused')])):
            response=self.client.post('/api/parse',json={'urls':[URL,URL]})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['status'],'partial')
        self.assertEqual(len(response.json()['videos']),1)
        self.assertEqual(len(response.json()['failed']),1)
        from core.downloader import DouyinDownloader
        downloader=DouyinDownloader(self.temp.name)
        with patch.object(downloader,'_get_next_sequence',return_value=1), patch.object(yt.YouTubeExtractor, 'download_video_sync', side_effect=YouTubeError('youtube_auth_required', 'Authentication required')):
            task=asyncio.run(downloader.download_video(info))
        self.assertEqual(task['status'],'paused')
        self.assertEqual(task['error_code'],'youtube_auth_required')
        self.assertNotEqual(task['percent'],100)
        server.parsed_cache.pop('mock-keep',None)


class QueueContracts(unittest.TestCase):
    def test_single_active_and_spacing(self):
        queue=YouTubeQueue(interval=.03);events=[]
        def run():
            with queue.lease():
                events.append(('start',time.monotonic()));time.sleep(.01)
                events.append(('end',time.monotonic()))
        workers=[threading.Thread(target=run) for _ in range(3)]
        for worker in workers:worker.start()
        for worker in workers:worker.join(3);self.assertFalse(worker.is_alive())
        self.assertEqual([name for name,_ in events],['start','end']*3)
        for index in (2,4):self.assertGreaterEqual(events[index][1]-events[index-1][1],.025)

    def test_auth_stops_waiters_even_if_resume_races(self):
        queue=YouTubeQueue(interval=0);started=threading.Event();errors=[]
        def waiting():
            started.set()
            try:
                with queue.lease():errors.append('incorrectly started')
            except YouTubeError as error:errors.append(error.code)
        with self.assertRaises(YouTubeError):
            with queue.lease():
                worker=threading.Thread(target=waiting);worker.start();started.wait(1)
                deadline=time.monotonic()+2
                while not queue.tickets and time.monotonic()<deadline:time.sleep(.005)
                raise YouTubeError('youtube_auth_required','Stop')
        queue.resume();worker.join(2)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,['youtube_paused'])
        with queue.lease():pass


if __name__=='__main__':unittest.main()
