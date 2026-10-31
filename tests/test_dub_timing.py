import unittest
import tempfile
import wave
import hashlib
import json
from pathlib import Path

import numpy as np

from core.dub_timing import captions, schedule, natural_schedule
from core.studio_media import make_voice_track
from core import studio_store as store


class Timing(unittest.TestCase):
    def test_natural_timing_keeps_start_and_reports_unfit_voice(self):
        cues = [dict(id='a', start=0, end=2, duration=1),
                dict(id='b', start=2, end=3, duration=2),
                dict(id='c', start=4, end=4.8, duration=.3)]
        for video_speed in (.5, 1, 2):
            plan = natural_schedule(cues, video_speed, video_end=5)
            self.assertEqual(plan['a']['play_start'], 0)
            self.assertEqual(plan['b']['play_start'], 2)
            self.assertEqual(plan['b']['status'], 'needs_timing' if video_speed == 2 else 'ready')
            self.assertLessEqual(plan['b']['tempo'], 1.15)
            self.assertAlmostEqual(plan['b']['speed'],plan['b']['tempo'])
            self.assertAlmostEqual((plan['b']['play_end']-plan['b']['play_start'])/video_speed,
                                   2/plan['b']['tempo'])
            self.assertEqual(plan['b']['overlap'], 0)
            self.assertLessEqual(plan['a']['play_end'], 2 - .08*video_speed + .00001)

    def test_natural_timing_rejects_overlap_and_trimmed_last_cue(self):
        cues = [dict(id='a', start=0, end=2.5, duration=1),
                dict(id='b', start=2, end=4, duration=1)]
        plan = natural_schedule(cues, video_end=5, trim_end=2.6)
        self.assertEqual(plan['a']['status'], 'needs_timing')
        self.assertIn('chồng', plan['a']['reason'])
        self.assertEqual(plan['b']['status'], 'needs_timing')

    def test_pronunciation_only_changes_affected_audio_key(self):
        data = store.normalize({'segments': [], 'detected_language': 'vi'})
        one = dict(id='one', start=0, end=2, text='Nambeo kể chuyện')
        two = dict(id='two', start=2, end=4, text='Chào bạn')
        original = [store.audio_key(data, seg) for seg in (one, two)]
        data['studio_config']['pronunciations'] = [{'term':'Nambeo', 'reading':'Nam bèo'}]
        store.validate(data, 5)
        self.assertEqual(store.spoken_text(data, one), 'Nam bèo kể chuyện')
        self.assertNotEqual(store.audio_key(data, one), original[0])
        self.assertEqual(store.audio_key(data, two), original[1])
        data['studio_config']['pronunciations'].append({'term':'Nam', 'reading':'Năm'})
        self.assertEqual(store.spoken_text(data, one), 'Nam bèo kể chuyện')

    def test_legacy_timing_key_stays_compatible(self):
        data=store.normalize({'segments':[dict(id='a',start=0,end=1,text='Chào')],
                              'detected_language':'vi', 'studio_config':{'timing':{'max_speed':1.65,'max_overlap':.5}}})
        old={'version':5,'speed':1,'timing':{'max_speed':1.65,'max_overlap':.5},
             'segments':[('a',0,1,True)]}
        expected=hashlib.sha256(json.dumps(old,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(store.timing_key(data),expected)

    def test_new_project_uses_natural_policy_existing_transcript_keeps_legacy(self):
        with tempfile.TemporaryDirectory() as temporary:
            video=Path(temporary)/'sample.mp4'
            self.assertEqual(store.load(video)['studio_config']['timing']['policy'],'natural-v1')
            transcript=store.paths(video)[0]
            store.atomic_json(transcript,{'segments':[]})
            original=transcript.read_bytes()
            self.assertEqual(store.load(video)['studio_config']['timing']['policy'],'legacy')
            self.assertEqual(transcript.read_bytes(),original)
    def test_uses_gap_and_keeps_natural_speed(self):
        cues = [dict(id='a', start=0, end=1, duration=2),
                dict(id='b', start=4, end=5, duration=.5)]
        plan = schedule(cues, video_end=8)
        self.assertEqual(plan['a']['speed'], 1)
        self.assertEqual(plan['b']['play_start'], 4)

    def test_speeds_up_before_spending_a_full_second_of_overlap(self):
        cues = [dict(id='a', start=0, end=2, duration=2.4),
                dict(id='b', start=2, end=4, duration=2.4)]
        plan = schedule(cues, max_overlap=1, video_end=5)
        self.assertAlmostEqual(plan['a']['speed'], 1.2)
        self.assertAlmostEqual(plan['b']['overlap'], 0)
        crowded = [dict(id='a', start=0, end=2, duration=3),
                   dict(id='b', start=2, end=4, duration=3)]
        plan = schedule(crowded, max_overlap=1, video_end=5)
        self.assertLessEqual(plan['b']['overlap'], .200001)
        self.assertGreater(plan['a']['speed'], 1.3)

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
        entries={'a':dict(status='ready',play_start=2,play_end=12),
                 'b':dict(status='ready',play_start=10,play_end=13)}
        self.assertEqual(captions(segments,config,entries)['a'],(2,11))
        self.assertEqual(captions(segments,config,entries)['b'],(11,13))

    def test_no_dialogue_cue_does_not_create_or_clip_captions(self):
        segments=[dict(id='a',start=0,end=8),dict(id='silent',start=2,end=3,no_dialogue=True),
                  dict(id='b',start=6,end=7)]
        config=dict(render=dict(dubbed=False,speed=1),caption_hold=12)
        windows=captions(segments,config)
        self.assertNotIn('silent',windows)
        self.assertEqual(windows['a'],(0,6))

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
