import unittest
import tempfile
import wave
from pathlib import Path

import numpy as np

from core.dub_timing import captions, schedule
from core.studio_media import make_voice_track


class Timing(unittest.TestCase):
    def test_uses_gap_and_keeps_natural_speed(self):
        cues = [dict(id='a', start=0, end=1, duration=2),
                dict(id='b', start=4, end=5, duration=.5)]
        plan = schedule(cues, video_end=8)
        self.assertEqual(plan['a']['speed'], 1)
        self.assertEqual(plan['b']['play_start'], 4)

    def test_dense_dialogue_limits_overlap_and_never_drops_words(self):
        cues = [dict(id=str(i), start=i*.15, end=i*.15+.1, duration=4) for i in range(5)]
        for video_speed in (.5, 1, 2):
            plan = schedule(cues, video_speed=video_speed, video_end=2)
            ordered = [plan[str(i)] for i in range(5)]
            for i, item in enumerate(ordered):
                self.assertLessEqual(item['speed'], 1.65)
                self.assertAlmostEqual((item['play_end']-item['play_start'])/video_speed,
                                       4/item['speed'])
                self.assertLessEqual(item['overlap'], 1.000001)
                if i >= 2:
                    self.assertGreaterEqual(item['play_start'], ordered[i-2]['play_end'])

    def test_caption_end_respects_next_cue_and_limit(self):
        segments=[dict(id='a',start=0,end=30),dict(id='b',start=20,end=25)]
        config=dict(render=dict(dubbed=False,speed=1),caption_hold=12)
        self.assertEqual(captions(segments,config)['a'],(0,12))
        config['render']['dubbed']=True
        entries={'a':dict(status='ready',play_start=2,play_end=22),
                 'b':dict(status='ready',play_start=10,play_end=11)}
        self.assertEqual(captions(segments,config,entries)['a'],(2,10))

    def test_overlapping_voice_track_contains_both_cues(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, frequency in (('a', 440), ('b', 880)):
                samples = (np.sin(np.arange(24000)*2*np.pi*frequency/24000)*10000).astype('<i2')
                with wave.open(str(root / (name+'.wav')), 'wb') as audio:
                    audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
                    audio.writeframes(samples.tobytes())
            data = {'segments': [{'id': 'a', 'start': 0}, {'id': 'b', 'start': .5}]}
            entries = {'a': {'status':'ready','aligned':'a.wav','play_start':0,'play_end':1},
                       'b': {'status':'ready','aligned':'b.wav','play_start':.5,'play_end':1.5}}
            output = root/'track.wav'
            make_voice_track(data, entries, root, output, 1.5, lambda: False)
            with wave.open(str(output), 'rb') as audio:
                signal = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2')
            overlap = signal[round(.6*24000):round(.9*24000)]
            spectrum = np.abs(np.fft.rfft(overlap))
            self.assertGreater(spectrum[round(440*.3)], 100000)
            self.assertGreater(spectrum[round(880*.3)], 100000)
            self.assertLessEqual(np.max(np.abs(signal.astype(np.int32))), 32767)


if __name__ == '__main__':
    unittest.main()
