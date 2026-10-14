"""通用縮寫與原標點的 TTS 輸入回歸；聽感另以真實語音驗證。"""
import importlib.util
from pathlib import Path
import unittest

try:
    import cmudict, wordninja
except ImportError:
    raise unittest.SkipTest('Run with the dedicated CosyVoice Python environment')
spec = importlib.util.spec_from_file_location('spoken_input', Path(__file__).resolve().parents[2]/'ops/podcast/spoken_input.py')
spoken = importlib.util.module_from_spec(spec); spec.loader.exec_module(spoken)


def sentences(text):
    return spoken.mixed_sentences(text, lambda t: t, lambda t: t)


def test_initialism_letters_keep_original_enumeration_in_one_sentence():
    text = '依序是 FIN、ACK、SYN。'
    result = sentences(text)
    assert len(result) == 1
    assert result[0]['text'] == '依序是 F I N 、 A C K 、 S Y N 。'
    assert text == '依序是 FIN、ACK、SYN。'


def test_mixed_sentence_preserves_chinese_connectors_and_punctuation():
    result = sentences('先送 TCP 封包，再回 ACK。')
    assert len(result) == 1
    assert result[0]['text'] == '先送 T C P 封包，再回 A C K 。'
    assert result[0]['ending'] == '。'


def test_compound_protocol_notation_preserves_operators():
    assert sentences('TCP/IP')[0]['text'] == 'T C P / I P'
    assert sentences('SYN + ACK')[0]['text'] == 'S Y N + A C K'
    assert sentences('ABC → XYZ')[0]['text'] == 'A B C → X Y Z'


def test_units_preserve_bit_byte_case_and_chinese_connector():
    result = sentences('8 Mb/s 與 8 MB/s')
    assert result[0]['text'] == '8 megabits per second 與 8 megabytes per second'
