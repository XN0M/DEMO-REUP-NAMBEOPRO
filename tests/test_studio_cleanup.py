import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import contextmanager
from core.studio_cleanup import StudioCleanup, clean_temporary
from core import studio_store as store, studio_media as media
REAL_PROBE = media.probe


class CleanupTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parents[1] / '.runtime' / 'cleanup-tests'
        test_root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=test_root)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.video = self.root / 'source.mp4'
        self.video.write_bytes(b'original')
        self.transcript, self.mirror, self.assets = store.paths(self.video)
        self.assets.mkdir()
        self.output = self.assets / 'export-r1-a.mp4'
        self.output.write_bytes(b'finished')
        self.preview = self.assets / 'preview-r1-a.mp4'
        self.preview.write_bytes(b'preview')
        self.unknown = self.assets / 'notes.txt'
        self.unknown.write_text('keep')
        self.data = {'revision': 1}
        self.transcript.write_text('{}')
        self.busy = False
        def reserve(path):
            if self.busy: return False
            self.busy = True
            return True
        host = SimpleNamespace(current_download_dir=str(self.root), _download_in_progress=lambda p:False,
            _reserve_source_job=reserve, _release_source_job=lambda p:setattr(self,'busy',False),
            _resolve_safe_path=lambda p:str(self.root / p))
        def check(p):
            p = Path(p).absolute()
            if self.root not in p.parents: raise ValueError('outside')
            return str(p)
        self.service = SimpleNamespace(host=host, database=self.root / 'runtime' / 'db', check=check,
            resolve=lambda p:str(self.video), get=lambda j:dict(filepath='source.mp4',kind='export',status='completed',artifact=self.output.name))
        @contextmanager
        def database():
            yield SimpleNamespace(execute=lambda q:[])
        self.service.db = database
        self.service.fingerprint = lambda p:'test'
        self.service.manifest = lambda p:{}
        self.cleanup = StudioCleanup(self.service)
        self.load = patch('core.studio_cleanup.store.load', side_effect=lambda p:self.data)
        self.load.start();self.addCleanup(self.load.stop)
        self.probe = patch('core.studio_cleanup.media.probe', return_value={'duration':1})
        self.probe.start();self.addCleanup(self.probe.stop)

    def plan(self, mode='compact'):
        return self.cleanup.plan('source.mp4', 'job', mode)

    def test_compact_keeps_source_project_and_unknown(self):
        result = self.cleanup.execute(self.plan()['id'], True)
        self.assertEqual(result['status'], 'completed')
        for p in (self.video,self.transcript,self.output,self.unknown):self.assertTrue(p.exists())
        self.assertFalse(self.preview.exists())

    def test_changed_revision_and_busy_rejected(self):
        plan = self.plan();self.data['revision']=2
        with self.assertRaises(store.Conflict):self.cleanup.execute(plan['id'],True)
        self.busy=True
        with self.assertRaises(store.Conflict):self.plan()

    def test_changed_file_rejected(self):
        plan=self.plan();self.preview.write_bytes(b'changed')
        with self.assertRaises(store.Conflict):self.cleanup.execute(plan['id'],True)
        self.assertTrue(self.video.exists())

    def test_output_only_preserves_verified_copy(self):
        plan=self.plan('output_only');result=self.cleanup.execute(plan['id'],True)
        self.assertEqual(result['status'],'completed')
        copies=list((self.root/'finished').glob('*.mp4'))
        self.assertEqual(copies[0].read_bytes(),b'finished')
        self.assertFalse(self.video.exists());self.assertTrue(self.unknown.exists())
        self.assertEqual(self.cleanup.execute(plan['id'],True)['status'],'completed')

    def test_copy_failure_keeps_original(self):
        plan=self.plan('output_only')
        with patch('core.studio_cleanup.shutil.copyfile',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.cleanup.execute(plan['id'],True)
        self.assertTrue(self.video.exists());self.assertTrue(self.output.exists())

    def test_locked_file_retry(self):
        plan=self.plan();unlink=Path.unlink
        def locked(p,*a,**k):
            if p==self.preview:raise PermissionError('locked')
            return unlink(p,*a,**k)
        with patch.object(Path,'unlink',locked):
            self.assertEqual(self.cleanup.execute(plan['id'],True)['status'],'partial')
        self.assertEqual(self.cleanup.execute(plan['id'],True)['status'],'completed')

    def test_unknown_temporary_preserved(self):
        work=self.root/'work';work.mkdir();(work/'voice.wav').write_bytes(b'tmp');(work/'custom.wav').write_bytes(b'keep')
        clean_temporary(work)
        self.assertFalse((work/'voice.wav').exists());self.assertTrue((work/'custom.wav').exists())

    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError):self.cleanup.safe(self.root.parent/'outside')

    def test_finished_copy_is_playable_media(self):
        media.run(['-f','lavfi','-i','color=c=blue:s=160x90:d=0.3','-c:v','libx264','-pix_fmt','yuv420p',self.output])
        with patch('core.studio_cleanup.media.probe', REAL_PROBE):
            plan = self.plan('output_only')
            self.cleanup.execute(plan['id'], True)
            finished = next((self.root/'finished').glob('*.mp4'))
            self.assertGreater(REAL_PROBE(finished)['duration'], 0)

    def test_compact_preserves_referenced_audio_and_deletes_orphan(self):
        self.data = store.normalize({'revision':1, 'segments':[{'id':'cue', 'start':0, 'end':1, 'vietnamese':'Xin chào'}]})
        audio = self.assets/'audio';audio.mkdir()
        key = store.audio_key(self.data,self.data['segments'][0],'test')
        referenced = audio/(key+'.wav');referenced.write_bytes(b'audio')
        orphan = audio/('a'*64+'.wav');orphan.write_bytes(b'orphan')
        self.cleanup.execute(self.plan()['id'],True)
        self.assertTrue(referenced.exists());self.assertFalse(orphan.exists())
