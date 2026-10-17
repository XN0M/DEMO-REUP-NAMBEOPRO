"""Studio contracts: offline AI/TTS mocks plus real FFmpeg integration fixtures."""
import asyncio
import copy
import io
import json
import math
import os
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch
import cv2
import numpy as np
from fastapi.testclient import TestClient
import server
from core import studio_store as store, studio_media as media
from core.studio_service import StudioService, TERMINAL


def sound(seconds=.4):
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as audio:
        audio.setparams((1,2,24000,0,'NONE','not compressed'))
        values=(np.sin(np.arange(round(seconds*24000))*2*np.pi*440/24000)*5000).astype('<i2')
        audio.writeframes(values.tobytes())
    return stream.getvalue()


class Studio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples=tempfile.TemporaryDirectory(prefix='nambeo-studio-fixture-')
        cls.silent=Path(cls.samples.name)/'silent.mp4'
        cls.audible=Path(cls.samples.name)/'audible.mp4'
        media.run(['-f','lavfi','-i','color=c=blue:s=640x360:d=6','-c:v','libx264','-pix_fmt','yuv420p',cls.silent])
        media.run(['-i',cls.silent,'-f','lavfi','-i','sine=frequency=220:duration=6','-map','0:v','-map','1:a','-c:v','copy','-c:a','aac',cls.audible])

    @classmethod
    def tearDownClass(cls):
        cls.samples.cleanup()

    def setUp(self):
        import shutil
        self.temp=tempfile.TemporaryDirectory(prefix='nambeo-studio-test-')
        self.root=Path(self.temp.name)
        self.video=self.root/'sample.mp4'
        shutil.copy2(self.audible,self.video)
        self.old_root=server.current_download_dir
        server.current_download_dir=str(self.root)
        self.svc=server.studio_service
        self.old_db,self.old_ready=self.svc.database,self.svc.ready
        self.svc.database=str(self.root/'jobs.sqlite3');self.svc.ready=False
        self.client=TestClient(server.app)
        self.legacy={'detected_language':'zh','segments':[{'start':.2,'end':1.5,'chinese':'你好','vietnamese':'Xin chào','speaker':'A'},{'start':2,'end':3.5,'chinese':'谢谢','vietnamese':'Cảm ơn','speaker':'B'}]}
        store.atomic_json(store.paths(self.video)[0],self.legacy)

    def tearDown(self):
        deadline=time.time()+15
        while self.svc.events and time.time()<deadline:
            for event in list(self.svc.events.values()):event.set()
            time.sleep(.05)
        server.current_download_dir=self.old_root
        self.svc.database,self.svc.ready=self.old_db,self.old_ready
        server.active_source_jobs.clear()
        self.temp.cleanup()

    def project(self):
        r=self.client.get('/api/studio/project',params={'filepath':'sample.mp4'})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def save(self, project):
        r=self.client.put('/api/studio/project',json={'filepath':'sample.mp4','expected_revision':project['revision'],'project':project})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()['project']

    def job(self, kind, data=None, **opts):
        data=data or self.project()['project']
        response=self.client.post('/api/studio/'+kind,json={'filepath':'sample.mp4','revision':data['revision'],**opts})
        self.assertEqual(response.status_code,200,response.text)
        jid=response.json()['id']
        return self.wait(jid)

    def wait(self,jid):
        deadline=time.time()+30
        while time.time()<deadline:
            result=self.svc.get(jid)
            if result['status'] in TERMINAL:
                # DB final write precedes lock release by a few instructions.
                while jid in self.svc.events:time.sleep(.01)
                return result
            time.sleep(.05)
        self.fail('Job timed out')

    def test_legacy_read_no_migration_revision_backup_and_exports(self):
        before=store.paths(self.video)[0].read_bytes()
        first=self.project()['project'];second=self.project()['project']
        self.assertEqual(first['segments'][0]['id'],second['segments'][0]['id'])
        self.assertEqual(store.paths(self.video)[0].read_bytes(),before)
        first['segments'][0]['vietnamese']='Đã sửa tiếng Việt'
        saved=self.save(first)
        self.assertEqual(saved['revision'],1)
        self.assertEqual(store.read_json(store.paths(self.video)[2]/'original.transcript.json',{}),self.legacy)
        self.assertIn('Đã sửa tiếng Việt',self.video.with_suffix('.vi.srt').read_text(encoding='utf-8'))
        stale=self.client.put('/api/studio/project',json={'filepath':'sample.mp4','expected_revision':0,'project':first})
        self.assertEqual(stale.status_code,409)

    def test_versions_keep_20_restore_new_revision(self):
        project=self.project()['project']
        for i in range(23):
            project['segments'][0]['vietnamese']=f'Bản {i}'
            project=self.save(project)
        versions=self.project()['versions']
        self.assertEqual(len(versions),20)
        r=self.client.put('/api/studio/project',json={'filepath':'sample.mp4','expected_revision':23,'restore_revision':versions[-1]})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['project']['revision'],24)

    def test_invalid_timing_style_and_paths(self):
        for value in [-1,7]:
            data=self.project()['project'];data['segments'][0]['start']=value
            r=self.client.put('/api/studio/project',json={'filepath':'sample.mp4','expected_revision':0,'project':data})
            self.assertEqual(r.status_code,400,r.text)
        data=self.project()['project'];data['studio_config']['style']['font']="x':evil"
        self.assertEqual(self.client.put('/api/studio/project',json={'filepath':'sample.mp4','expected_revision':0,'project':data}).status_code,400)
        for path in ['../sample.mp4','%2e%2e/sample.mp4','C:/outside.mp4']:
            self.assertEqual(self.client.get('/api/studio/project',params={'filepath':path}).status_code,403)
        data=self.project()['project'];data['segments'][0]['start']=float('nan')
        with self.assertRaises(ValueError):store.validate(data,6)

    def test_incremental_audio_and_style_does_not_invalidate(self):
        data=self.save(self.project()['project'])
        mock=AsyncMock(return_value=sound())
        with patch.object(server.dubber,'synthesize_edge',mock):
            result=self.job('tts',data)
            self.assertEqual(result['status'],'completed',result)
            self.assertEqual(mock.await_count,2)
            self.assertTrue(all(e['status']=='ready' for e in self.project()['audio'].values()))
            data=self.project()['project'];data['studio_config']['style']['color']='#FFAA00';data=self.save(data)
            self.job('tts',data)
            self.assertEqual(mock.await_count,2)
            data['segments'][0]['vietnamese']='Nội dung mới';data=self.save(data)
            statuses=self.project()['audio']
            self.assertEqual(statuses[data['segments'][0]['id']]['status'],'stale')
            self.job('tts',data)
            self.assertEqual(mock.await_count,3)
            data['segments'][0]['end']=1.7;data=self.save(data)
            self.assertEqual(self.project()['audio'][data['segments'][0]['id']]['status'],'needs_alignment')
            self.job('tts',data)
            self.assertEqual(mock.await_count,3)

    def test_long_voice_extends_export_and_preserves_words(self):
        data=self.project()['project']
        data['segments']=[dict(id='last',start=5,end=5.8,text='Cuối',vietnamese='Lời dài',speaker='')]
        data=self.save(data)
        with patch.object(server.dubber,'synthesize_edge',AsyncMock(return_value=sound(4))):
            result=self.job('tts',data)
        self.assertEqual(result['status'],'completed',result)
        self.assertTrue(all(e['status']=='ready' for e in self.project()['audio'].values()))
        data['studio_config']['render']['dubbed']=True;data=self.save(data)
        result=self.job('export',data)
        self.assertEqual(result['status'],'completed',result)
        rendered=store.paths(self.video)[2]/result['artifact']
        self.assertTrue(media.has_audio(rendered))
        self.assertGreater(media.probe(rendered)['duration'],7,result)
        self.assertGreater(result['render']['extended_seconds'],1)

    def test_preview_and_export_with_audio_mix(self):
        data=self.save(self.project()['project'])
        with patch.object(server.dubber,'synthesize_edge',AsyncMock(return_value=sound())):
            self.job('tts',data)
        data['studio_config']['render'].update(dubbed=True,start=.5,end=4.5,speed=2)
        data['studio_config']['style'].update(background=True,bilingual=True,mask=True)
        data=self.save(data)
        result=self.job('export',data)
        self.assertEqual(result['status'],'completed',result)
        self.assertTrue(all(abs(e['tempo_factor']-.5)<.01 for e in self.project()['audio'].values()))
        output=store.paths(self.video)[2]/result['artifact']
        self.assertAlmostEqual(media.probe(output)['duration'],2,delta=.15)
        self.assertTrue(media.has_audio(output))
        response=self.client.get('/api/studio/asset',params={'filepath':'sample.mp4','name':result['artifact']})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get('/api/studio/asset',params={'filepath':'sample.mp4','name':'../sample.mp4'}).status_code,404)

    def test_silent_video_speed_matrix_trim_and_subtitle_render(self):
        import shutil
        shutil.copy2(self.silent,self.video)
        data=self.project()['project'];data['studio_config']['style'].update(size=100,color='#FFFFFF',mask=False)
        for speed in [.5,1,2]:
            data['studio_config']['render'].update(start=.5,end=3,speed=speed,aspect='9:16')
            data=self.save(data)
            result=self.job('export',data)
            self.assertEqual(result['status'],'completed',result)
            output=store.paths(self.video)[2]/result['artifact'];info=media.probe(output)
            self.assertAlmostEqual(info['duration'],2.5/speed,delta=.2)
            self.assertFalse(media.has_audio(output))
            cap=cv2.VideoCapture(str(output));ok,frame=cap.read();cap.release();self.assertTrue(ok)
            # White pixels establish that libass actually drew text over blue fixture.
            self.assertGreater(np.count_nonzero(np.min(frame,axis=2)>180),5)
        srt=self.client.get('/api/studio/subtitles',params={'filepath':'sample.mp4','format':'srt'}).text
        self.assertIn('00:00:00,000 --> 00:00:00,500',srt)

    def test_cancel_tts_does_not_publish_late_result(self):
        data=self.save(self.project()['project'])
        entered=threading.Event()
        async def slow(*args,**kwargs):
            entered.set();await asyncio.sleep(20);return sound()
        with patch.object(server.dubber,'synthesize_edge',side_effect=slow):
            response=self.client.post('/api/studio/tts',json={'filepath':'sample.mp4','revision':data['revision']}).json()
            self.assertTrue(entered.wait(5))
            self.client.post('/api/studio/jobs/'+response['id']+'/cancel')
            result=self.wait(response['id'])
            self.assertEqual(result['status'],'cancelled')
        self.assertFalse((store.paths(self.video)[2]/'audio.json').exists())

    def test_recovery_and_revision_snapshot(self):
        self.svc.recover()
        self.svc.update(dict(id='interrupted',status='processing',snapshot={},filepath='sample.mp4'))
        restarted=StudioService(server,self.svc.database);restarted.recover()
        self.assertEqual(restarted.get('interrupted')['status'],'interrupted')
        data=self.save(self.project()['project'])
        entered,release=threading.Event(),threading.Event()
        async def synth(*args):
            entered.set()
            while not release.is_set():await asyncio.sleep(.02)
            return sound()
        with patch.object(server.dubber,'synthesize_edge',side_effect=synth):
            response=self.client.post('/api/studio/tts',json={'filepath':'sample.mp4','revision':data['revision']}).json()
            self.assertTrue(entered.wait(5))
            data['segments'][0]['vietnamese']='Đã thay đổi khi đang tạo';self.save(data)
            conflict=self.client.post('/api/studio/export',json={'filepath':'sample.mp4','revision':data['revision']+1})
            self.assertEqual(conflict.status_code,409)
            release.set();result=self.wait(response['id'])
        self.assertTrue(result['stale'])
        self.assertEqual(self.project()['audio'][data['segments'][0]['id']]['status'],'stale')

    def test_translation_proposals_never_overwrite(self):
        data=self.save(self.project()['project']);sid=data['segments'][0]['id']
        answer=json.dumps([{'id':sid,'text':'Bản dịch đề xuất'}])
        with patch.object(server,'load_app_config',return_value={'gemini_api_key':'mock'}),patch.object(server.translator,'_call_ai',AsyncMock(return_value=answer)) as mock:
            result=self.job('translate',data,segment_ids=[sid])
        self.assertEqual(result['proposals'][0]['text'],'Bản dịch đề xuất')
        self.assertEqual(self.project()['project']['segments'][0]['vietnamese'],'Xin chào')
        self.assertIn('character_map',mock.call_args.args[0])

    def test_move_delete_studio_assets_and_history_ignores_cache(self):
        data=self.save(self.project()['project'])
        assets=store.paths(self.video)[2]
        import shutil
        shutil.copy2(self.silent,assets/'preview.mp4')
        (self.root/'Anime').mkdir()
        result=self.client.post('/api/history/move',json={'filepaths':['sample.mp4'],'collection':'Anime'}).json()
        self.assertEqual(len(result['moved']),1,result)
        new=result['moved'][0]['new_filepath']
        p=self.client.get('/api/studio/project',params={'filepath':new})
        self.assertEqual(p.status_code,200,p.text)
        history=self.client.get('/api/history').json()['videos']
        self.assertEqual(len(history),1,history)
        self.assertEqual(self.client.delete('/api/history/'+new).status_code,200)
        self.assertFalse(store.paths(self.root/new)[2].exists())

    def test_failed_render_preserves_previous_artifact_and_source(self):
        data=self.save(self.project()['project'])
        result=self.job('export',data);good=store.paths(self.video)[2]/result['artifact'];before=good.read_bytes();source=self.video.read_bytes()
        with patch('core.studio_media.render',side_effect=OSError('disk full')):
            result=self.job('export',data)
        self.assertEqual(result['status'],'error');self.assertEqual(good.read_bytes(),before);self.assertEqual(self.video.read_bytes(),source)

    def test_legacy_writer_revision_and_studio_opt_in(self):
        store.persist_legacy(self.video,self.legacy)
        self.assertFalse(store.load(self.video).get('studio_enabled',False))
        self.assertFalse(store.paths(self.video)[1].exists())
        old=store.load(self.video)
        self.save(self.project()['project'])
        with self.assertRaises(store.Conflict):store.persist_legacy(self.video,old)
        self.assertTrue(store.load(self.video)['studio_enabled'])

    def test_batch_reuses_saved_config_and_generates_missing_audio(self):
        data=self.project()['project'];data['studio_config']['render'].update(dubbed=True,speed=2,end=4)
        data=self.save(data)
        mock=AsyncMock(return_value=sound())
        with patch.object(server.dubber,'synthesize_edge',mock):
            result=self.svc.batch(str(self.video))
            self.assertTrue(result.exists())
            self.assertAlmostEqual(media.probe(result)['duration'],2,delta=.15)
            self.assertEqual(mock.await_count,2)
            self.svc.batch(str(self.video));self.assertEqual(mock.await_count,2)

    def test_synthesis_concurrency_and_explicit_provider(self):
        data=self.project()['project']
        data['segments']=[dict(id=f's{i}',start=i,end=i+.8,text=f'Text {i}',vietnamese=f'Câu {i}',speaker='') for i in range(5)]
        # Even a voice ID resembling Edge must route to the explicitly chosen provider.
        data['studio_config']['voice']={'provider':'openai','model':'tts-1','voice':'vi-VN-test'}
        data=self.save(data)
        active=0;maximum=0
        async def synth(*args):
            nonlocal active,maximum
            active+=1;maximum=max(maximum,active)
            await asyncio.sleep(.2)
            active-=1;return sound(.2)
        with patch.object(server.dubber,'synthesize_openai',side_effect=synth) as mock,patch.object(server.dubber,'synthesize_edge',AsyncMock()) as edge:
            result=self.job('tts',data)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(maximum,2);self.assertEqual(mock.await_count,5);edge.assert_not_awaited()

    def test_atomic_save_failure_does_not_commit_revision(self):
        data=self.project()['project'];before=store.paths(self.video)[0].read_bytes()
        with patch('core.studio_store.write_exports',side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):store.save(self.video,data,0,6)
        self.assertEqual(store.paths(self.video)[0].read_bytes(),before)

    def test_provider_outage_stops_repeated_calls_preserves_cache_and_other_provider(self):
        from core.tts_runtime import ProviderUnavailable
        data=self.project()['project']
        data['segments']=[dict(id=f'cue{i}',start=i*.4,end=i*.4+.35,text=f'Hello {i}',vietnamese=f'Chào {i}',speaker='A' if i<12 else 'B') for i in range(13)]
        data['studio_config']['speakers']={'B':{'provider':'openai','model':'tts-1','voice':'alloy'}}
        data=self.save(data)
        with patch.object(server.dubber,'synthesize_edge',AsyncMock(return_value=sound(.1))):
            self.job('tts',data,segment_ids=['cue0','cue1'])
        assets=store.paths(self.video)[2]
        manifest=self.svc.manifest(self.video)
        cached=(assets/manifest['cue0']['aligned']).read_bytes()
        error=ProviderUnavailable('edge','Edge TTS bị từ chối (HTTP 403). Chạy cap_nhat.bat.')
        with patch.object(server.dubber,'synthesize_edge',AsyncMock(side_effect=error)) as edge, patch.object(server.dubber,'synthesize_openai',AsyncMock(return_value=sound(.1))) as paid:
            result=self.job('tts',data)
        self.assertEqual(result['status'],'partial',result)
        self.assertLessEqual(edge.await_count,2)
        self.assertEqual(len(result['failed']),10)
        self.assertEqual(len(result['completed']),3)
        paid.assert_awaited_once()
        self.assertIn('cap_nhat.bat',result['message'])
        self.assertEqual(cached,(assets/manifest['cue0']['aligned']).read_bytes())

    def test_cancellable_ffmpeg_stops_process(self):
        event=threading.Event();result=[]
        def encode():
            try:media.run(['-re','-f','lavfi','-i','sine=duration=30',self.root/'long.wav'],event.is_set)
            except media.Cancelled:result.append('cancelled')
        thread=threading.Thread(target=encode);thread.start();time.sleep(.3);event.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(result,['cancelled'])

    def test_invalid_request_and_symlink_asset_are_rejected(self):
        self.assertEqual(self.client.post('/api/studio/tts',json={'filepath':'sample.mp4','revision':-1}).status_code,400)
        data=self.project()['project'];data['segments']=['not a segment']
        self.assertEqual(self.client.put('/api/studio/project',json={'filepath':'sample.mp4','expected_revision':0,'project':data}).status_code,400)
        with tempfile.TemporaryDirectory() as outside:
            link=store.paths(self.video)[2]
            try:os.symlink(outside,link,target_is_directory=True)
            except OSError:
                # A junction exercises the same resolved-path boundary without
                # requiring Windows Developer Mode / symbolic-link privilege.
                import subprocess
                result=subprocess.run(['cmd','/c','mklink','/J',str(link),outside],capture_output=True)
                if result.returncode:self.skipTest('Windows reparse-point creation unavailable')
            try:self.assertEqual(self.client.get('/api/studio/project',params={'filepath':'sample.mp4'}).status_code,403)
            finally:os.rmdir(str(self.video.with_suffix('.studio-assets')))

    def test_batch_endpoint_uses_studio_without_legacy_ai(self):
        data=self.project()['project'];data['studio_config']['render'].update(speed=2,end=4)
        self.save(data)
        with patch.object(server.dubber,'execute_dubbing',AsyncMock(side_effect=AssertionError('legacy dub called'))),patch.object(server.translator,'translate_with_character_map',AsyncMock(side_effect=AssertionError('legacy translate called'))):
            response=self.client.post('/api/batch/run',json={'filepaths':['sample.mp4']})
            self.assertEqual(response.status_code,200,response.text)
            jid=response.json()['batch_id']
            deadline=time.time()+20
            while server.batch_tasks[jid]['status'] not in ('completed','partial','error') and time.time()<deadline:time.sleep(.05)
        result=server.batch_tasks.pop(jid)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(len(result['completed']),1)

    def test_empty_dialogue_can_render_without_tts(self):
        data=self.project()['project'];data['segments']=[];data['studio_config']['render']['dubbed']=True
        data=self.save(data)
        with patch.object(server.dubber,'synthesize_edge',AsyncMock()) as mock:
            result=self.job('export',data)
        self.assertEqual(result['status'],'completed',result);mock.assert_not_awaited()

    def test_job_history_and_audio_follow_source_move(self):
        data=self.save(self.project()['project'])
        with patch.object(server.dubber,'synthesize_edge',AsyncMock(return_value=sound())):
            result=self.job('tts',data)
        (self.root/'Anime').mkdir()
        moved=self.client.post('/api/history/move',json={'filepaths':['sample.mp4'],'collection':'Anime'}).json()['moved'][0]['new_filepath']
        result=self.client.get('/api/studio/project',params={'filepath':moved}).json()
        self.assertTrue(all(e['status']=='ready' for e in result['audio'].values()))
        self.assertTrue(self.client.get('/api/studio/jobs',params={'filepath':moved}).json()['jobs'])

    def test_transcribe_job_uses_engine_without_writing_until_apply(self):
        from types import SimpleNamespace
        model=SimpleNamespace(transcribe=lambda *a,**kw:(iter([
            SimpleNamespace(start=.3,end=1.3,text='Hello world')]),
            SimpleNamespace(language='en',duration=6)))
        before=store.paths(self.video)[0].read_bytes()
        with patch.object(server.transcriber,'get_model',return_value=model):
            result=self.job('transcribe',language='auto',model_size='tiny')
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['transcript_result']['segments'][0]['chinese'],'Hello world')
        self.assertEqual(store.paths(self.video)[0].read_bytes(),before)
        applied=self.client.post(f'/api/studio/jobs/{result["id"]}/apply-transcript',json={'expected_revision':0})
        self.assertEqual(applied.status_code,200,applied.text)
        self.assertEqual(applied.json()['project']['revision'],1)
        self.assertEqual(applied.json()['project']['detected_language'],'en')
        self.assertEqual(store.read_json(store.paths(self.video)[2]/'original.transcript.json',{}),self.legacy)
        self.assertEqual(self.client.post(f'/api/studio/jobs/{result["id"]}/apply-transcript',json={'expected_revision':1}).status_code,409)

    def test_stale_recognition_keeps_user_edit_and_can_be_reviewed(self):
        async def recognize(*args,**kwargs):
            self.assertFalse(kwargs['persist'])
            await asyncio.sleep(.2)
            return {'status':'completed','detected_language':'vi','segments':[dict(start=0,end=1,text='Xin chào',vietnamese='Xin chào')]}
        with patch.object(server.transcriber,'transcribe_video',side_effect=recognize):
            response=self.client.post('/api/studio/transcribe',json={'filepath':'sample.mp4','revision':0})
            edited=self.project()['project'];edited['segments'][0]['vietnamese']='Bản sửa của tôi';edited=self.save(edited)
            result=self.wait(response.json()['id'])
        self.assertTrue(result['stale'])
        self.assertEqual(store.load(self.video)['segments'][0]['vietnamese'],'Bản sửa của tôi')
        self.assertEqual(self.client.post(f'/api/studio/jobs/{result["id"]}/apply-transcript',json={'expected_revision':0}).status_code,409)
        response=self.client.post(f'/api/studio/jobs/{result["id"]}/apply-transcript',json={'expected_revision':edited['revision']})
        self.assertEqual(response.status_code,200,response.text)
        restore=self.client.put('/api/studio/project',json={'filepath':'sample.mp4','expected_revision':2,'restore_revision':1})
        self.assertEqual(restore.json()['project']['segments'][0]['vietnamese'],'Bản sửa của tôi')

    def test_recognition_cancel_waits_for_engine_and_never_applies(self):
        started=threading.Event();release=threading.Event()
        async def recognize(*args,**kwargs):
            started.set()
            while not release.is_set():await asyncio.sleep(.02)
            return {'status':'completed','detected_language':'en','segments':[]}
        before=store.paths(self.video)[0].read_bytes()
        with patch.object(server.transcriber,'transcribe_video',side_effect=recognize):
            response=self.client.post('/api/studio/transcribe',json={'filepath':'sample.mp4','revision':0})
            jid=response.json()['id']
            self.assertTrue(started.wait(3))
            self.client.post(f'/api/studio/jobs/{jid}/cancel')
            self.assertNotIn(self.svc.get(jid)['status'],TERMINAL)
            # Source stays locked until the engine has actually stopped.
            self.assertEqual(self.client.post('/api/studio/transcribe',json={'filepath':'sample.mp4','revision':0}).status_code,409)
            release.set();result=self.wait(jid)
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual(store.paths(self.video)[0].read_bytes(),before)
        self.assertEqual(self.client.post(f'/api/studio/jobs/{jid}/apply-transcript',json={'expected_revision':0}).status_code,400)

    def test_silent_recognition_and_export_without_subtitles_or_tts(self):
        import shutil
        shutil.copy2(self.silent,self.video)
        with patch.object(server.transcriber,'transcribe_video',AsyncMock()) as mock:
            result=self.job('transcribe')
        mock.assert_not_awaited();self.assertTrue(result['empty_dialogue'])
        response=self.client.post(f'/api/studio/jobs/{result["id"]}/apply-transcript',json={'expected_revision':0})
        data=response.json()['project'];data['studio_config']['render'].update(subtitles=False,dubbed=False,speed=2)
        data=self.save(data)
        with patch.object(server.dubber,'synthesize_edge',AsyncMock()) as tts:
            rendered=self.job('export',data)
        self.assertEqual(rendered['status'],'completed',rendered);tts.assert_not_awaited()
        self.assertAlmostEqual(media.probe(store.paths(self.video)[2]/rendered['artifact'])['duration'],3,delta=.15)

    def test_character_map_proposal_revision_and_history_status(self):
        proposal={'characters':[{'name':'<b>Nhân vật</b>','self_pronoun':'ta'}]}
        with patch.object(server,'load_app_config',return_value={'gemini_api_key':'mock'}),patch.object(server.translator,'generate_character_map',AsyncMock(return_value=proposal)):
            result=self.job('character-map')
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['character_map_proposal'],proposal)
        self.assertFalse(store.load(self.video).get('character_map'))
        data=self.project()['project'];data['character_map']=proposal;data['segments'][1]['vietnamese']='';self.save(data)
        history=self.client.get('/api/history').json()['videos'][0]['studio_summary']
        self.assertEqual(history['missing_translation'],1)
        self.assertEqual(history['segments'],2)

    def test_transcribe_validation_and_recovery(self):
        for extra in [{'model_size':'../../evil'},{'language':'invalid!'}]:
            response=self.client.post('/api/studio/transcribe',json={'filepath':'sample.mp4','revision':0,**extra})
            self.assertEqual(response.status_code,400)
        self.assertEqual(self.client.post('/api/studio/transcribe',json={'filepath':'../sample.mp4','revision':0}).status_code,403)
        self.assertEqual(self.client.post('/api/studio/transcribe',json={'filepath':'sample.mp4','revision':3}).status_code,409)
        self.svc.update(dict(id='lost-recognition',kind='transcribe',filepath='sample.mp4',revision=0,status='processing'))
        recovered=StudioService(server,self.svc.database);recovered.recover()
        self.assertEqual(recovered.get('lost-recognition')['status'],'interrupted')

    def test_subtitles_toggle_controls_real_render_without_requiring_tts(self):
        data=self.project()['project'];data['studio_config']['style'].update(size=100)
        data['studio_config']['render'].update(start=.5,end=1.2,subtitles=False,dubbed=False)
        data=self.save(data)
        with patch.object(server.dubber,'synthesize_edge',AsyncMock()) as tts:
            result=self.job('export',data)
        self.assertEqual(result['status'],'completed',result);tts.assert_not_awaited()
        cap=cv2.VideoCapture(str(store.paths(self.video)[2]/result['artifact']));ok,frame=cap.read();cap.release()
        self.assertTrue(ok);self.assertEqual(np.count_nonzero(np.min(frame,axis=2)>180),0)
        data['studio_config']['render']['subtitles']=True;data=self.save(data)
        result=self.job('export',data)
        cap=cv2.VideoCapture(str(store.paths(self.video)[2]/result['artifact']));ok,frame=cap.read();cap.release()
        self.assertTrue(ok);self.assertGreater(np.count_nonzero(np.min(frame,axis=2)>180),5)

    @unittest.skipUnless(os.name=='nt','Windows folder action')
    def test_result_folder_opens_validated_artifact_directory(self):
        result=self.job('export')
        with patch('core.studio_api.os.startfile') as open_folder:
            response=self.client.post(f'/api/studio/jobs/{result["id"]}/open-folder')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(Path(open_folder.call_args.args[0]).resolve(),store.paths(self.video)[2].resolve())
        self.svc.update({**result,'artifact':'../../sample.mp4'})
        with patch('core.studio_api.os.startfile') as open_folder:
            self.assertEqual(self.client.post(f'/api/studio/jobs/{result["id"]}/open-folder').status_code,403)
        open_folder.assert_not_called()


if __name__=='__main__':unittest.main()
