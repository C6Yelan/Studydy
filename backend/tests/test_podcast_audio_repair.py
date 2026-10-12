"""只驗證 DSP 保護條件；合成訊號不代表真人音訊品質驗收。"""
import unittest
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[2] / "ops/podcast"))
try:
    import numpy as np
    import cmudict, wordninja
except ImportError:
    raise unittest.SkipTest("Run with the dedicated CosyVoice Python environment")
from dialogue_audio_repair import clean_onset, soften_edges, spoken_units, pace_plan, assemble
from spoken_input import mixed_sentences

RATE = 24000


def tone(seconds, frequency=160):
    return (.08 * np.sin(2 * np.pi * frequency * np.arange(round(seconds * RATE)) / RATE)).astype('float32')


class AudioRepairTests(unittest.TestCase):
    def test_isolated_click_removed_without_cutting_later_voice(self):
        audio = np.zeros(RATE, dtype='float32')
        audio[480:720] = np.random.default_rng(7).normal(0, .02, 240)
        audio[7200:16800] = tone(.4)
        result, report = clean_onset(audio, RATE)
        self.assertGreater(report['removed_samples'], 720)
        self.assertLessEqual(report['removed_samples'], 7200 - round(.1 * RATE))
        np.testing.assert_array_equal(result, audio[report['removed_samples']:])

    def test_plosive_close_to_vowel_is_preserved(self):
        audio = np.concatenate([np.random.default_rng(2).normal(0, .02, 240), np.zeros(960), tone(.5)]).astype('float32')
        result, report = clean_onset(audio, RATE)
        self.assertEqual(report['removed_samples'], 0)
        np.testing.assert_array_equal(result, audio)

    def test_short_voiced_word_before_pause_is_preserved(self):
        audio = np.concatenate([tone(.04), np.zeros(round(.18 * RATE)), tone(.5, 210)]).astype('float32')
        result, report = clean_onset(audio, RATE)
        self.assertEqual(report['removed_samples'], 0)
        np.testing.assert_array_equal(result, audio)

    def test_fade_keeps_interior_and_duration(self):
        audio = tone(.4) + .01
        result = soften_edges(audio, RATE)
        self.assertEqual(len(result), len(audio))
        self.assertEqual(result[0], 0)
        self.assertEqual(result[-1], 0)
        np.testing.assert_array_equal(result[240:-480], audio[240:-480])

    def test_plural_suffix_and_letter_name_units(self):
        self.assertEqual(spoken_units('ten megabits per second'), (7, []))
        self.assertEqual(spoken_units('W'), (3, []))

    def test_pace_uses_audio_not_sentence_identity(self):
        text = '應用程式會依需求處理資料並檢查傳送結果'
        items = [{'text': text, 'audio': tone(s)} for s in (3, 5, 5, 5)]
        items.append({'text': '懂了', 'audio': tone(.5)})
        plan = pace_plan(items, RATE)
        self.assertLess(plan['sentences'][0]['tempo'], 1)
        self.assertTrue(all(s['tempo'] == 1 for s in plan['sentences'][1:]))


class MixedInputTests(unittest.TestCase):
    def plan(self, text):
        return mixed_sentences(text, lambda value: value, lambda value: value)

    def test_full_english_sentence_keeps_article(self):
        plan = self.plan('The client sends a request to the server.')
        self.assertEqual(len(plan), 1)
        self.assertIn(' a ', plan[0]['text'])
        self.assertNotIn(' A ', plan[0]['text'])

    def test_mixed_acronyms_keep_chinese_connectors_in_one_sentence(self):
        plan = self.plan('TCP、UDP 和 HTTP 是通訊協定。')
        self.assertEqual(len(plan), 1)
        self.assertIn('T C P', plan[0]['text'])
        self.assertIn('U D P 和 H T T P', plan[0]['text'])

    def test_values_units_and_decimal_sentence_boundary(self):
        text = '大小是1,500 bytes，等待250ms。速率是8 MB/s 與8 Mb/s，延遲1.2毫秒。'
        plan = self.plan(text)
        self.assertEqual(len(plan), 2)
        self.assertIn('1500', plan[0]['text'])
        self.assertIn('250 毫秒', plan[0]['text'])
        self.assertIn('8 megabytes per second', plan[1]['text'])
        self.assertIn('8 megabits per second', plan[1]['text'])
        self.assertIn('1.2', plan[1]['text'])
        self.assertIn('1,500', text)

    def test_short_replies_are_not_dropped(self):
        self.assertEqual([x['text'] for x in self.plan('懂了。那 UDP 呢？')], ['懂了。', '那 U D P 呢？'])


class AssemblyTests(unittest.TestCase):
    def test_known_speaker_boundary_has_more_pause_without_overlap(self):
        items = [{'text': '短句', 'ending': '。', 'speaker': speaker, 'beat': 0,
                  'audio': tone(.3)} for speaker in ('host', 'host')]
        same = assemble(items, RATE)
        items[1]['speaker'] = 'guest'
        switched = assemble(items, RATE)
        self.assertGreater(len(switched) - len(same), .07 * RATE)
        self.assertTrue(np.isfinite(switched).all())

    def test_tempo_failure_is_not_silently_accepted(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        items = [{'text': '應用程式會依需求處理資料並檢查傳送結果', 'ending': '。',
                  'speaker': 'host', 'beat': 0, 'audio': tone(s)} for s in (3, 5, 5, 5)]
        with patch('audio_mastering.ffmpeg', return_value='/synthetic/ffmpeg'), patch('subprocess.run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(ValueError, 'PODCAST_AUDIO_INVALID'):
                assemble(items, RATE)


if __name__ == '__main__':
    unittest.main()
