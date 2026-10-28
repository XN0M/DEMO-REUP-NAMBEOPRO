"""Offline cut contracts and short real FFmpeg exports; no downloads or AI."""
import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import httpx
import server
from core.editor import VideoEditor
from core.studio_media import probe
from core.video_cuts import execute_cuts, validate_segments


class VideoCuts(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        runtime = Path(__file__).resolve().parents[1] / '.runtime'
        runtime.mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='nambeo-cuts-', dir=runtime)
        cls.source = Path(cls.temp.name) / 'video mẫu.mp4'
        cls.editor = VideoEditor()
        subprocess.run([cls.editor.ffmpeg_exe, '-y', '-f', 'lavfi', '-i',
                        'testsrc2=size=160x120:rate=25:duration=3', '-f', 'lavfi', '-i',
                        'sine=frequency=440:sample_rate=48000:duration=3',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                        str(cls.source)], check=True, capture_output=True)
        cls.info = probe(cls.source)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_validation(self):
        for segments in [[], [{'start': -1, 'end': 1}], [{'start': 0, 'end': 4}],
                         [{'start': 1, 'end': 1}], [{'start': 0, 'end': .01}],
                         [{'start': float('nan'), 'end': 1}], [{'start': 0, 'end': float('inf')}],
                         [{'start': 0, 'end': 2}, {'start': 1, 'end': 3}],
                         [{'start': 2, 'end': 3}, {'start': 0, 'end': 1}]]:
            with self.subTest(segments=segments), self.assertRaises(ValueError):
                validate_segments(segments, self.info)
        self.assertEqual(len(validate_segments([{'start': 0, 'end': .04}], self.info)), 1)

    async def export(self, segments, **kwargs):
        task_id = os.urandom(6).hex()
        self.editor.tasks[task_id] = {}
        await execute_cuts(self.editor, task_id, str(self.source), segments, **kwargs)
        task = self.editor.tasks[task_id]
        self.assertEqual(task['status'], 'completed', task)
        return task

    async def test_one_two_many_and_unique_folders(self):
        folders = set()
        original = self.source.read_bytes()
        for marks in [[0, 3], [.2, 1.4, 2.8], [.12, .84, 1.64, 2.84]]:
            segments = [{'start': a, 'end': b} for a, b in zip(marks, marks[1:])]
            task = await self.export(segments)
            folders.add(task['output_folder'])
            self.assertEqual(Path(task['output_folder']).parent, self.source.parent)
            self.assertEqual(len(task['outputs']), len(segments))
            self.assertEqual(len(list(Path(task['output_folder']).iterdir())), len(segments))
            for output in task['outputs']:
                path = Path(output['path'])
                self.assertEqual(path.name, f"part_{output['index']:03d}.mp4")
                info = probe(path)
                self.assertAlmostEqual(info['duration'], output['end']-output['start'], delta=.041)
                self.assertEqual((info['width'], info['height']), (160, 120))
                # Decode audio: copied metadata alone cannot prove the clip has sound.
                audio = subprocess.run([self.editor.ffmpeg_exe, '-i', str(path), '-vn',
                                        '-f', 's16le', '-'], capture_output=True, check=True)
                self.assertGreater(len(audio.stdout), 1000)
                self.assertNotEqual(set(audio.stdout), {0})
                source_cap, clip_cap = cv2.VideoCapture(str(self.source)), cv2.VideoCapture(str(path))
                try:
                    source_cap.set(cv2.CAP_PROP_POS_MSEC, output['start']*1000)
                    ok1, frame1 = source_cap.read(); ok2, frame2 = clip_cap.read()
                    self.assertTrue(ok1 and ok2)
                    self.assertLess(cv2.absdiff(frame1, frame2).mean(), 15)
                finally:
                    source_cap.release(); clip_cap.release()
        self.assertEqual(len(folders), 3)
        self.assertEqual(self.source.read_bytes(), original)

    async def test_crop_and_fast_export(self):
        task = await self.export([{'start': .4, 'end': 1.2}], crop={'x': 0, 'y': 0, 'w': 80, 'h': 60})
        self.assertEqual(probe(task['outputs'][0]['path'])['width'], 80)
        await self.export([{'start': 0, 'end': 1}], lossless=True)

    async def test_failure_keeps_completed_and_removes_temp(self):
        original = self.editor.crop_trim_video
        count = 0
        def fail_second(source, target, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                Path(target).write_bytes(b'partial')
                raise RuntimeError('synthetic failure')
            return original(source, target, **kwargs)
        self.editor.tasks['fail'] = {}
        with patch.object(self.editor, 'crop_trim_video', side_effect=fail_second):
            await execute_cuts(self.editor, 'fail', str(self.source),
                               [{'start': 0, 'end': 1}, {'start': 1, 'end': 2}])
        task = self.editor.tasks['fail']
        self.assertEqual(task['status'], 'error')
        self.assertIn('2', task['error'])
        self.assertEqual(len(task['outputs']), 1)
        self.assertEqual([p.name for p in Path(task['output_folder']).iterdir()], ['part_001.mp4'])

    async def test_api_validation_legacy_download_and_lock(self):
        transport = httpx.ASGITransport(app=server.app)
        with patch.object(server, 'current_download_dir', self.temp.name):
            async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
                base = {'filepath': self.source.name}
                for fields in [{'segments': []}, {'segments': [{'start': 0, 'end': 9}]},
                               {'segments': [{'start': 0, 'end': 1}], 'trim': {'start': 0, 'end': 1}},
                               {'crop': {'x': 0, 'y': 0, 'w': 999, 'h': 60}},
                               {'crop': {'x': 'bad'}}, {'trim': {'start': 'nan', 'end': 2}}]:
                    response = await client.post('/api/video/crop-trim', json={**base, **fields})
                    self.assertEqual(response.status_code, 400, response.text)
                info = await client.get('/api/video/crop-trim/media', params=base)
                self.assertEqual(info.status_code, 200)
                self.assertEqual(info.json()['fps'], 25)
                # A delay gives the API a live processing job while metadata stays responsive.
                original = server.editor.crop_trim_video
                def slow(*args, **kwargs):
                    import time
                    time.sleep(.15)
                    return original(*args, **kwargs)
                with patch.object(server.editor, 'crop_trim_video', side_effect=slow):
                    response = await client.post('/api/video/crop-trim', json={**base, 'trim': {'start': .2, 'end': 1.4}})
                    self.assertEqual(response.status_code, 200, response.text)
                    task_id = response.json()['task_id']
                    conflict = await client.post('/api/video/crop-trim', json={**base, 'trim': {'start': 0, 'end': 1}})
                    self.assertEqual(conflict.status_code, 409)
                    for _ in range(100):
                        status = (await client.get('/api/video/crop-trim/tasks/'+task_id)).json()
                        if status['status'] in ('completed', 'error'):
                            break
                        await asyncio.sleep(.05)
                self.assertEqual(status['status'], 'completed', status)
                self.assertEqual(status['output_video_rel'], status['outputs'][0]['rel'])
                file = await client.get(status['outputs'][0]['download_url'])
                self.assertEqual(file.status_code, 200)
                self.assertIn('attachment', file.headers['content-disposition'])
                self.assertEqual((await client.get('/api/video/crop-trim/tasks/missing/files/1')).status_code, 404)
                self.assertTrue(server._reserve_source_job(str(self.source)))
                server._release_source_job(str(self.source))


if __name__ == '__main__':
    unittest.main()
