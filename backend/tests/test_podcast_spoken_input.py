"""通用縮寫與原標點的 TTS 輸入回歸；聽感另以真實語音驗證。"""
import importlib.util
from pathlib import Path
import pytest

pytest.importorskip('cmudict')
pytest.importorskip('wordninja')
spec = importlib.util.spec_from_file_location('spoken_input', Path(__file__).resolve().parents[2]/'ops/podcast/spoken_input.py')
spoken = importlib.util.module_from_spec(spec); spec.loader.exec_module(spoken)


def spans(text):
    return spoken.speech_spans(text, lambda t: t, lambda t: t)


def test_initialism_letters_stay_together_and_only_enumeration_gets_commas():
    text = '依序是 FIN、ACK、SYN。'
    result = spans(text)
    english = [s for s in result if s['language'] == 'en']
    assert english == [{'language': 'en', 'text': 'F I N, A C K, S Y N', 'ending': '。'}]
    assert text == '依序是 FIN、ACK、SYN。'


def test_mixed_sentence_keeps_join_and_sentence_boundary_distinct():
    result = spans('先送 TCP 封包，再回 ACK。')
    assert next(s for s in result if s['text'] == 'T C P')['ending'] == ''
    assert result[-1]['text'] == 'A C K' and result[-1]['ending'] == '。'
    assert all(s['text'].strip() not in {'。', '，', '、'} for s in result)


def test_compound_protocol_notation_keeps_operators_without_letter_pauses():
    assert spans('TCP/IP')[0]['text'] == 'T C P slash I P'
    assert spans('SYN + ACK')[0]['text'] == 'S Y N plus A C K'
    assert spans('ABC → XYZ')[0]['text'] == 'A B C then X Y Z'
    assert spans('ABC XYZ')[0]['text'] == 'A B C X Y Z'


def test_clause_punctuation_belongs_to_the_preceding_english_span():
    result = spans('送出 FIN，另一個方向仍可傳送資料。')
    assert result[1]['text'] == 'F I N' and result[1]['ending'] == '，'
    assert not result[2]['text'].startswith('，')


def test_units_preserve_bit_byte_case_and_numeric_values():
    result = spans('8 Mb/s 與 8 MB/s')
    assert result[0]['text'] == '8 megabits per second and 8 megabytes per second'
