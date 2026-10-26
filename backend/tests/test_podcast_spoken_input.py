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


def test_initialism_enumeration_keeps_source_and_separate_pauses():
    text = '依序是 FIN、ACK、SYN。'
    result = sentences(text)
    assert [x['text'] for x in result] == ['依序是 F I N ，', 'A C K ，', 'S Y N 。']
    assert ''.join(x['source_text'] for x in result) == text
    assert [x['ending'] for x in result] == ['、', '、', '。']


def test_mixed_sentence_preserves_chinese_connectors_and_punctuation():
    result = sentences('先送 TCP 封包，再回 ACK。')
    assert len(result) == 1
    assert result[0]['text'] == '先送 T C P 封包，再回 A C K 。'
    assert result[0]['ending'] == '。'


def test_compound_protocol_notation_preserves_operators():
    assert sentences('TCP/IP')[0]['text'] == 'T C P / I P'
    assert sentences('SYN + ACK')[0]['text'] == 'S Y N 加 A C K'
    assert sentences('ABC → XYZ')[0]['text'] == 'A B C 到 X Y Z'


def test_english_compound_words_do_not_read_typographic_hyphens():
    assert sentences('Retrieval-Augmented Generation')[0]['text'] == 'retrieval augmented generation'
    assert sentences('使用 evidence-based 方法。')[0]['text'] == '使用 evidence based 方法。'
    assert 'hyphen' not in sentences('state-of-the-art')[0]['text']


def test_identifier_hyphens_and_underscores_remain_audible():
    assert sentences('SYN-ACK')[0]['text'] == 'S Y N hyphen A C K'
    assert sentences('version-2')[0]['text'] == 'version hyphen 2'
    assert sentences('sample_name')[0]['text'] == 'sample underscore name'


def test_units_preserve_bit_byte_case_and_chinese_connector():
    result = sentences('8 Mb/s 與 8 MB/s')
    assert result[0]['text'] == '8 megabits per second 與 8 megabytes per second'


def test_original_sentence_length_is_preserved_before_spoken_expansion():
    result=sentences('8 MB/s，等待250ms。')
    assert result[0]['source_text']=='8 MB/s，等待250ms。'
    assert 'megabytes per second' in result[0]['text']
    assert '250 毫秒' in result[0]['text']


def test_closing_quotes_stay_with_sentence_and_question_boundary():
    result=sentences('他問：「可以嗎？」接著確認條件。')
    assert [x['source_text'] for x in result]==['他問：「可以嗎？」','接著確認條件。']
    assert [x['ending'] for x in result]==['？','。']


def test_token_list_has_measured_boundaries_without_changing_display():
    text = '輸入「開始／片段／<結束>」，再繼續。'
    result = sentences(text)
    assert [x['ending'] for x in result[:2]] == ['／', '／']
    assert ''.join(x['source_text'] for x in result) == text
    assert '結束標記' in result[-1]['text']


def test_formulas_units_paths_and_two_part_ratio_are_not_list_separators():
    text = 'f(x,y)、1/2、/tmp/a/b、8 MB/s、時間／距離。'
    result = sentences(text)
    assert ''.join(x['source_text'] for x in result) == text
    assert result[0]['source_text'] == 'f(x,y)、'
    assert '2 分之 1' in result[1]['text']
    assert result[2]['text'].count('/') == 3
    assert 'megabytes per second' in result[3]['text']
    assert '時間／距離' in result[4]['text']


def test_comparison_operators_and_greek_variables_remain_audible_without_changing_source():
    text='ρ_t > τ；ρ_t ≤ τ。'
    result=sentences(text)
    assert ''.join(x['source_text'] for x in result)==text
    assert '大於' in result[0]['text'] and '小於或等於' in result[1]['text']
    assert 'rho' in result[0]['text'] and 'tau' in result[0]['text']


def test_math_normalization_does_not_rewrite_path_or_code_literals():
    result=sentences('路徑 /tmp/x>y/θ，程式 `x<=y`。')
    joined=''.join(x['text'] for x in result)
    assert '大於' not in joined and '小於' not in joined and 'theta' not in joined
    assert '>' in joined and '<=' in joined and 'θ' in joined


def test_choice_formula_and_pause_conversion_keeps_display_text():
    text = '二選一：A/B；QK^T / √d_k = 1/2。RAG（或 DDPM）：A、B。'
    result = sentences(text)
    rendered = ''.join(x['text'] for x in result)
    assert ''.join(x['source_text'] for x in result) == text
    assert 'A 或 B' in rendered
    assert 'Q 乘以 K 的轉置' in rendered and '根號 d k' in rendered
    assert '等於 2 分之 1' in rendered
    assert 'R A G ，或 D D P M ，，' in rendered
    assert 'A ，' in rendered


def test_literal_operators_and_non_choice_slashes_keep_their_meaning():
    result = sentences('程式 `x+y=z`，路徑 /tmp/a+b=c，TCP/IP，時間／距離，8 MB/s。')
    rendered = ''.join(x['text'] for x in result)
    assert '+' in rendered and '=' in rendered
    assert 'T C P / I P' in rendered and '時間／距離' in rendered
    assert 'megabytes per second' in rendered


def test_position_expression_and_enumeration_used_in_attention_dialogue():
    text = '位置 i 的表示預測位置 i+1；A、B、C。'
    result = sentences(text)
    assert ''.join(x['source_text'] for x in result) == text
    assert 'i 加 1' in ''.join(x['text'] for x in result)
    assert [x['ending'] for x in result] == ['；', '、', '、', '。']


def test_chinese_adjacent_math_and_protocol_selection():
    rendered = ''.join(x['text'] for x in sentences('機率為1/2，計算QK^T，再用√d_k縮放。選用 TCP/IP 協定。'))
    assert '2 分之 1' in rendered
    assert 'Q 乘以 K 的轉置' in rendered and '根號 d k' in rendered
    assert 'T C P / I P' in rendered
    assert '或' not in rendered
    assert '除以根號' in sentences('QK^T/√d_k')[0]['text']
