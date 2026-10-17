"""Offline contracts for voice uploads, dispatch, cache and local worker lifecycle."""
import asyncio
import copy
import io
import json
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch, PropertyMock
import numpy as np
from fastapi.testclient import TestClient
import server
from core import studio_store as store
from core.tts_runtime import VoiceLibrary, TTSError, GPU_LOCK, gpu_slot, VieneuRuntime


def wav(seconds=3.2, silent=False):
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as audio:
        audio.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        values = np.zeros(round(seconds*48000)) if silent else np.sin(np.arange(round(seconds*48000))*440*2*np.pi/48000)*5000
        audio.writeframes(values.astype('<i2').tobytes())
    return stream.getvalue()


class PersonalVoices(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = VoiceLibrary(Path(self.temp.name) / 'voices')
        self.patches = [patch('core.tts_runtime.library', self.library), patch('core.tts_api.library', self.library),
                        patch('core.dubber.voice_library', self.library), patch('core.studio_service.voice_library', self.library)]
        for p in self.patches:
            p.start()
        self.client = TestClient(server.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def create(self):
        return self.library.create('Mẫu của tôi', 'sample.wav', wav(), True)

    def test_upload_normalize_rename_and_delete(self):
        response = self.client.post('/api/tts/voices/custom?name=Test&filename=sample.wav&consent=true', content=wav())
        self.assertEqual(response.status_code, 200, response.text)
        identity = response.json()['id']
        sample = self.client.get(f'/api/tts/voices/custom/{identity}/sample')
        with wave.open(io.BytesIO(sample.content)) as audio:
            self.assertEqual((audio.getframerate(), audio.getnchannels()), (48000, 1))
        self.assertEqual(self.client.patch(f'/api/tts/voices/custom/{identity}', json={'name':'Mới'}).json()['name'], 'Mới')
        self.assertEqual(self.client.delete(f'/api/tts/voices/custom/{identity}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/tts/voices/custom/{identity}/sample').status_code, 404)

    def test_invalid_audio_and_consent_leave_no_assets(self):
        for name, content, consent in [('bad.wav', b'not audio', True), ('short.wav', wav(1), True),
                                       ('long.wav', wav(9), True), ('silent.wav', wav(silent=True), True),
                                       ('sample.wav', wav(), False), ('file.exe', wav(), True)]:
            with self.subTest(name=name, consent=consent), self.assertRaises(TTSError):
                self.library.create('Test', name, content, consent)
        self.assertEqual(self.library.list(), [])
        with self.assertRaises(TTSError):
            self.library.get('../outside')

    def test_active_lease_blocks_deletion_and_releases_after_error(self):
        v = self.create()
        with self.assertRaisesRegex(RuntimeError, 'failure'):
            with self.library.pin(['vieneu:custom:'+v['id']]):
                self.assertEqual(self.client.delete('/api/tts/voices/custom/'+v['id']).status_code, 409)
                raise RuntimeError('failure')
        self.library.delete(v['id'])

    def test_cache_uses_reference_content_not_name_or_timeline(self):
        v = self.create()
        data = store.normalize({'detected_language':'vi','segments':[{'id':'one','start':0,'end':4,'text':'Xin chào'}]})
        data['studio_config']['voice'] = {'provider':'vieneu','model':'v3turbo','voice':'vieneu:custom:'+v['id']}
        seg = data['segments'][0]
        before = store.audio_key(data, seg)
        self.library.rename(v['id'], 'Tên mới')
        seg['start'] = .2
        self.assertEqual(before, store.audio_key(data, seg))
        seg['dub_text'] = 'Lời khác'
        self.assertNotEqual(before, store.audio_key(data, seg))
        seg.pop('dub_text')
        meta = self.library.get(v['id']);meta['sha256'] = 'different'
        self.library._save(self.library.path(v['id']), meta)
        self.assertNotEqual(before, store.audio_key(data, seg))

    def test_provider_validation_and_legacy_cache(self):
        data = store.normalize({'segments':[]})
        self.assertEqual(store.validate(copy.deepcopy(data), 6)['studio_config']['voice']['provider'], 'edge')
        data['studio_config']['voice']={'provider':'vieneu','model':'v3turbo','voice':'vieneu:preset:Mai Anh'}
        store.validate(data, 6)
        data['studio_config']['voice']['model']='unknown'
        with self.assertRaises(ValueError):store.validate(data, 6)

    def test_dispatch_and_preview_wav_content_type(self):
        voice = 'vieneu:preset:Mai Anh'
        with patch.object(server.dubber, 'synthesize_vieneu', new_callable=AsyncMock, return_value=wav()) as synth:
            response = self.client.post('/api/dub/preview', json={'voice':voice,'text':'Xin chào'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('audio/wav', response.headers['content-type'])
            synth.assert_awaited_once()
        with patch.object(server.dubber, 'synthesize_edge', new_callable=AsyncMock, return_value=b'edge') as edge:
            result=asyncio.run(server.dubber.synthesize_sentence('Xin chào', 'vi-VN-HoaiMyNeural'))
            self.assertEqual(result, b'edge');edge.assert_awaited_once()
        with patch.object(server.dubber, 'synthesize_openai', new_callable=AsyncMock, return_value=b'openai') as paid:
            self.assertEqual(asyncio.run(server.dubber.synthesize_sentence('Text','alloy')),b'openai')
            paid.assert_awaited_once()

    def test_unknown_provider_rejected(self):
        with self.assertRaises(TTSError):
            asyncio.run(server.dubber.synthesize_sentence('Text', 'bad', provider='bad'))

    def test_cancelled_waiter_does_not_enter_gpu(self):
        started, cancelled = threading.Event(), threading.Event()
        errors = []
        def wait():
            started.set()
            try:
                with gpu_slot(cancelled.is_set):
                    errors.append('entered')
            except TTSError:
                errors.append('cancelled')
        with GPU_LOCK:
            thread=threading.Thread(target=wait);thread.start();started.wait(2)
            cancelled.set();thread.join(2)
        self.assertEqual(errors, ['cancelled'])

    def test_missing_install_is_actionable(self):
        # Use an uninitialized instance to avoid creating a lifecycle thread in tests.
        engine=object.__new__(VieneuRuntime)
        engine.root=Path(self.temp.name);engine.control=threading.RLock()
        engine.process=None;engine.connection=None;engine.install_process=None
        with self.assertRaisesRegex(TTSError, 'Chưa cài'):
            engine.start()

    def test_shared_audio_cache_skips_worker_and_rename_keeps_cache(self):
        from unittest.mock import Mock
        voice=self.create();identity='vieneu:custom:'+voice['id']
        engine=object.__new__(VieneuRuntime)
        engine.directory=Path(self.temp.name)/'engine'
        engine.gpu_release=None
        engine.start=Mock()
        engine.request=Mock(return_value=wav())
        first=engine.synthesize('Xin chào',identity)
        self.library.rename(voice['id'],'Tên mới')
        self.assertEqual(engine.synthesize('Xin chào',identity),first)
        engine.request.assert_called_once()
        engine.synthesize('Câu mới',identity)
        self.assertEqual(engine.request.call_count,2)

    def test_legacy_real_mux_and_long_voice_preserves_previous_export(self):
        from core import studio_media as media
        video=Path(self.temp.name)/'video.mp4'
        media.run(['-f','lavfi','-i','color=c=blue:s=320x180:d=2','-c:v','libx264','-pix_fmt','yuv420p',video])
        transcript={'segments':[{'start':.1,'end':1.5,'vietnamese':'Xin chào'}]}
        with patch.object(server.dubber,'synthesize_vieneu',new_callable=AsyncMock,return_value=wav(.4)):
            result=asyncio.run(server.dubber.execute_dubbing(str(video),transcript,single_voice='vieneu:preset:Mai Anh',task_id='test-vieneu'))
        self.assertEqual(result['status'],'success')
        previous=Path(result['dubbed_video']).read_bytes(),Path(result['dubbed_audio']).read_bytes()
        with patch.object(server.dubber,'synthesize_vieneu',new_callable=AsyncMock,return_value=wav(3)):
            with self.assertRaisesRegex(TTSError,'quá dài'):
                asyncio.run(server.dubber.execute_dubbing(str(video),transcript,single_voice='vieneu:preset:Mai Anh',task_id='test-vieneu-long'))
        self.assertEqual(previous,(Path(result['dubbed_video']).read_bytes(),Path(result['dubbed_audio']).read_bytes()))
        self.assertFalse(list(video.parent.glob('.dub_*')))

    def test_worker_authentication_and_restart_after_exit(self):
        import shutil
        import sys
        import httpx
        root=Path(self.temp.name)
        (root/'core').mkdir()
        shutil.copy2(Path(server.BASE_DIR)/'core'/'vieneu_worker.py', root/'core'/'vieneu_worker.py')
        engine=VieneuRuntime(root)
        with patch.object(VieneuRuntime, 'python', new_callable=PropertyMock, return_value=Path(sys.executable)):
            try:
                engine.start()
                self.assertEqual(engine.request('GET', '/health')['state'], 'idle')
                with httpx.Client(trust_env=False) as client:
                    response=client.get(f'http://127.0.0.1:{engine.connection["port"]}/health')
                self.assertEqual(response.status_code, 403)
                first=engine.process.pid
                engine.process.terminate();engine.process.wait(5)
                engine.start()
                self.assertNotEqual(first, engine.process.pid)
                self.assertEqual(engine.request('GET', '/health')['device'], 'cuda')
            finally:
                engine.stop()


if __name__ == '__main__':
    unittest.main()
