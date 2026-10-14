from copy import deepcopy

import pytest

from runtime.card_compaction import CompactionError, source_texts, validate_edits, validate_reviews


def test_edits_check_format_and_leave_meaning_to_reviewer():
    source = '原文的必要說明：傳送 100 bytes 後，確認值為 1200；這裡仍須保留接收條件。'
    valid = '傳送 100 bytes 後，確認值為 1200；須保留接收條件。'
    assert validate_edits({'edits': [{'index': 0, 'text': valid}]}, [source])[0]['text'] == valid
    changed_number = valid.replace('1200', '1201')
    assert validate_edits({'edits': [{'index': 0, 'text': changed_number}]}, [source])[0]['text'] == changed_number
    for changed in (valid + '\n附註', source * 2):
        assert validate_edits({'edits': [{'index': 0, 'text': changed}]}, [source])[0]['text'] == source


def test_numeric_layout_differences_reach_model_review():
    source = 'Latency is 10 ms with this configuration.'
    short = 'Latency: 10ms.'
    assert validate_edits({'edits': [{'index': 0, 'text': short}]}, [source])[0]['text'] == short


def test_reviewer_rejects_semantic_loss_without_publishing_it():
    candidates = [{'source': '原文條件', 'text': '遺失條件'}, {'source': '原文', 'text': '短句'}]
    accepted, rejected = validate_reviews({'reviews': [
        {'index': 1, 'supported': True, 'reason': ''},
        {'index': 0, 'supported': False, 'reason': '遺失條件'},
    ]}, candidates)
    assert accepted == [candidates[1]]
    assert rejected == [{**candidates[0], 'reason': '遺失條件'}]
    with pytest.raises(CompactionError):
        validate_reviews({'reviews': [{'index': 0, 'supported': True, 'reason': ''}]}, candidates)
    accepted, rejected = validate_reviews({'reviews': [{'index': 0, 'supported': False, 'reason': ''}]}, candidates[:1])
    assert accepted == [] and rejected == [{**candidates[0], 'reason': ''}]


def test_source_projection_preserves_input_and_excludes_technical_content():
    prose, code = '說明文字與必要條件。' * 7, 'a = function(long_argument, other_argument)'
    view = {'concepts': [
        {'concept_id': 'a', 'claims': [{'text': prose, 'evidence': [{'kind': 'text', 'quote': prose}]}]},
        {'concept_id': 'b', 'claims': [{'text': code, 'evidence': [{'kind': 'code', 'quote': code}]}]},
        {'concept_id': 'c', 'claims': [{'text': '不相關教材內容。' * 8, 'evidence': []}]},
    ], 'relations': [{'source_concept_id': 'b', 'target_concept_id': 'a', 'learner_reason': ''}]}
    before = deepcopy(view)
    assert source_texts(view, 'a') == [prose]
    assert view == before


def test_duplicate_or_invented_indices_fail_closed():
    for indices in ([0, 0], [0, 2], [True, 0]):
        with pytest.raises(CompactionError):
            validate_edits({'edits': [{'index': i, 'text': '短句'} for i in indices]}, ['來源一', '來源二'])


def test_pdf_wrapping_keeps_complete_sentences_for_compaction():
    text = 'The model predicts the noise, while the sampler applies the update\nto the latent state under the specified schedule.'
    document = {'concepts': [{'concept_id': 'a', 'claims': [{'text': text, 'evidence': []}]}]}
    assert source_texts(document, 'a') == [' '.join(text.split())]


def test_nul_source_stays_in_original_document_without_entering_jsonb_job():
    text = 'The extracted formula contains a control character: ' + '\x00' + ' in its source.'
    document = {'concepts': [{'concept_id': 'a', 'claims': [{'text': text, 'evidence': []}]}]}
    before = deepcopy(document)
    assert source_texts(document, 'a') == []
    assert document == before


def test_longer_rewrite_is_unchanged_not_a_content_rejection():
    source = '條件成立才傳送。'
    result = validate_edits({'edits': [{'index': 0, 'text': '僅在條件成立時，才進行傳送。'}]}, [source])[0]
    assert result == {'source': source, 'text': source, 'rejected': False}
