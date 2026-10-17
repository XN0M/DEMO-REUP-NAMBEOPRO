import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import server
from core.video_files import COMPANION_SUFFIXES, is_source


class FolderMoves(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_root = server.current_download_dir
        server.current_download_dir = str(self.root)
        self.client = TestClient(server.app)
        (self.root / 'Anime').mkdir()
        (self.root / 'Đậu đây').mkdir()

    def tearDown(self):
        server.current_download_dir = self.old_root
        server.active_source_jobs.clear()
        server.downloader.tasks.clear()
        self.temp.cleanup()

    def file(self, relative, content=b'test'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def move(self, paths, collection='Anime'):
        return self.client.post('/api/history/move', json={'filepaths': paths, 'collection': collection})

    def test_private_directory_all_assets_and_collision(self):
        source = self.file('Chung/youtube_001_20260921/a.mp4')
        for suffix in COMPANION_SUFFIXES:
            self.file('Chung/youtube_001_20260921/a' + suffix, suffix.encode())
        self.file('Chung/youtube_001_20260921/extra.txt')
        self.file('Anime/youtube_001_20260921/keep.txt', b'unchanged')
        result = self.move([source.relative_to(self.root).as_posix()]).json()
        self.assertEqual(len(result['moved']), 1, result)
        target = self.root / result['moved'][0]['new_filepath']
        self.assertEqual(target.parent.name, 'youtube_001_20260921_2')
        self.assertTrue((target.parent / 'extra.txt').exists())
        for suffix in COMPANION_SUFFIXES:
            self.assertEqual(target.with_name('a' + suffix).read_bytes(), suffix.encode())
        self.assertEqual((self.root / 'Anime/youtube_001_20260921/keep.txt').read_bytes(), b'unchanged')

    def test_shared_legacy_only_moves_selected_assets(self):
        self.file('a.mp4'); self.file('a.vi.srt'); self.file('b.mp4'); self.file('b.json')
        result = self.move(['a.mp4']).json()
        target = self.root / result['moved'][0]['new_filepath']
        self.assertTrue(target.with_suffix('.vi.srt').exists())
        self.assertTrue((self.root / 'b.mp4').exists())
        self.assertTrue((self.root / 'b.json').exists())

    def test_same_destination_and_vietnamese_roundtrip(self):
        self.file('a.mp4')
        self.assertEqual(len(self.move(['a.mp4'], 'Chung').json()['skipped']), 1)
        first = self.move(['a.mp4'], 'Đậu đây').json()['moved'][0]['new_filepath']
        second = self.move([first], 'Chung').json()['moved'][0]['new_filepath']
        self.assertTrue((self.root / second).exists())

    def test_partial_and_job_conflict(self):
        source = self.file('busy.mp4'); self.file('ok.mp4')
        server._reserve_source_job(str(source))
        result = self.move(['busy.mp4', 'missing.mp4', 'ok.mp4']).json()
        self.assertEqual(len(result['failed']), 2)
        self.assertEqual(result['failed'][0]['code'], 409)
        self.assertEqual(len(result['moved']), 1)
        self.assertEqual(self.client.delete('/api/history/busy.mp4').status_code, 409)
        self.assertEqual(self.client.post('/api/history/restore-original', json={'filepath':'busy.mp4'}).status_code, 409)

    def test_downloading_is_blocked(self):
        source = self.file('a.mp4')
        server.downloader.tasks['sample'] = {'status':'downloading', 'file_path':str(source)}
        self.assertEqual(self.move(['a.mp4']).json()['failed'][0]['code'], 409)

    def test_traversal_validation_before_any_move(self):
        self.file('ok.mp4')
        for bad in ['../outside.mp4', '%2e%2e/outside.mp4', 'C:/outside.mp4', '/outside.mp4']:
            self.assertEqual(self.move(['ok.mp4', bad]).status_code, 403)
        self.assertTrue((self.root / 'ok.mp4').exists())
        self.assertEqual(self.move(['ok.mp4'], '../outside').status_code, 403)
        self.assertEqual(self.move(['ok.mp4'], 'Unknown').status_code, 400)
        self.assertEqual(self.move([]).status_code, 400)

    def test_failure_rolls_back_and_continues(self):
        self.file('a.mp4'); self.file('a.json'); self.file('b.mp4')
        real_rename = os.rename
        def fail_json(old, new):
            if str(old).endswith('a.json'):
                raise PermissionError('Windows file lock')
            return real_rename(old, new)
        with patch('core.video_move.os.rename', side_effect=fail_json):
            result = self.move(['a.mp4', 'b.mp4']).json()
        self.assertEqual(len(result['failed']), 1)
        self.assertEqual(len(result['moved']), 1)
        self.assertTrue((self.root / 'a.mp4').exists())
        self.assertTrue((self.root / 'a.json').exists())

    def test_orphan_derivative_is_source(self):
        self.assertTrue(is_source('orphan.final.mp4', ['orphan.final.mp4']))
        self.assertFalse(is_source('a.dubbed.hardsub.vi.mp4', ['a.mp4','a.dubbed.hardsub.vi.mp4']))
        self.file('orphan.final.mp4')
        self.assertEqual(len(self.move(['orphan.final.mp4']).json()['moved']), 1)

    def test_rollback_failure_reports_remaining_location(self):
        self.file('a.mp4'); self.file('a.json')
        real_rename = os.rename
        def failure(old, new):
            if str(old).endswith('a.json') or str(old).startswith(str(self.root / 'Anime')):
                raise PermissionError('locked')
            return real_rename(old, new)
        with patch('core.video_move.os.rename', side_effect=failure):
            result = self.move(['a.mp4']).json()
        self.assertIn('Anime', result['failed'][0]['error'])
        self.assertTrue(list((self.root / 'Anime').rglob('a.mp4')))

if __name__ == '__main__':
    unittest.main()
