from copy import deepcopy
import pytest

from knowledge_map.claim_literals import mask_literals, language_correction_template, restore_literals, preserves_literals
from knowledge_map.localization import localize_block, _needs_translation
from test_knowledge_structure_v1 import _page, _block
from knowledge_map.structure import build_document_context


def sample():
    source = 'The mass of the object is 5 kg.'
    context = build_document_context([_page(1, [_block(1, 0, 'paragraph', source)])], page_count=1)
    bundle = {'evidence': context['evidence'], 'sections': context['sections']}
    response = {'concepts': [{'l': 'Mass', 'a': [], 'c': [{'m': None, 's': [0]}]}], 'relations': []}
    return source, context, bundle, response


def test_translation_restores_exact_quantities_and_keeps_original_name_as_alias():
    source, context, bundle, response = sample()
    before = deepcopy(response)
    def translate(request, schema):
        items = request['localization']
        assert schema['required'] == list(items)
        assert list(items['claim_0_0']['literals'].values()) == ['5 kg']
        return {'label_0': '質量', 'claim_0_0': '物體質量為' + next(iter(items['claim_0_0']['literals'])) + '。'}
    result = localize_block(response, context, bundle, translate)
    assert result['concepts'][0]['l'] == '質量'
    assert result['concepts'][0]['a'] == ['Mass']
    assert result['concepts'][0]['c'][0] == {'m': '物體質量為5 kg。', 's': [0]}
    assert context['evidence'][0]['exact_text'] == source and response == before


@pytest.mark.parametrize('failure', ['omitted', 'duplicated', 'foreign', 'missing_key'])
def test_invalid_translation_cannot_publish_original_foreign_text_as_success(failure):
    _, context, bundle, response = sample()
    def translate(request, schema):
        item = request['localization']['claim_0_0']
        token = next(iter(item['literals']))
        result = {'label_0': '質量', 'claim_0_0': '物體質量為' + token + '。'}
        if failure == 'omitted': result['claim_0_0'] = '物體質量為6 kg。'
        if failure == 'duplicated': result['claim_0_0'] += token
        if failure == 'foreign': result['claim_0_0'] = '中文：' + item['text']
        if failure == 'missing_key': result.pop('label_0')
        return {key: value for key, value in result.items() if key in request['localization']}
    with pytest.raises(ValueError, match='SEMANTIC_OUTPUT_INVALID'):
        localize_block(response, context, bundle, translate)


def test_valid_chinese_needs_no_extra_model_call():
    _, context, bundle, response = sample()
    response['concepts'][0].update(l='質量', c=[{'m': '物體質量為5 kg。', 's': [0]}])
    def forbidden(*_): raise AssertionError('No localization needed')
    assert localize_block(response, context, bundle, forbidden) == response


@pytest.mark.parametrize('failure', ['omitted', 'foreign', 'no_chinese'])
def test_localization_repairs_only_failed_items_once(failure):
    _, context, bundle, response = sample()
    calls = []
    def translate(request, schema):
        calls.append(deepcopy(request))
        item = request['localization']['claim_0_0']
        token = next(iter(item['literals']))
        if len(calls) == 1:
            bad = {'omitted': '物體質量。', 'foreign': '中文：' + item['text'], 'no_chinese': token}[failure]
            return {'label_0': '質量', 'claim_0_0': bad}
        assert list(request['localization']) == schema['required'] == ['claim_0_0']
        assert item['correction']
        if failure == 'omitted':
            assert token in item['correction']
        assert item['previous_output'] == {'omitted': '物體質量。', 'foreign': '中文：' + item['text'], 'no_chinese': token}[failure]
        return {'claim_0_0': '物體質量為' + token + '。'}
    result = localize_block(response, context, bundle, translate)
    assert len(calls) == 2
    assert result['concepts'][0]['c'][0]['m'] == '物體質量為5 kg。'
    assert result['concepts'][0]['a'] == ['Mass']


def test_localization_stops_after_one_failed_repair():
    _, context, bundle, response = sample()
    calls = []
    def translate(request, schema):
        calls.append(request)
        return {key: '錯誤翻譯。' for key in request['localization']}
    with pytest.raises(ValueError, match='SEMANTIC_OUTPUT_INVALID'):
        localize_block(response, context, bundle, translate)
    assert len(calls) == 2


def test_mask_protects_whole_code_and_latex_without_overlapping_tokens():
    code = 'return f(x) + 1;'
    masked_code, code_literals = mask_literals(code, 'code')
    assert list(code_literals.values()) == [code]
    assert restore_literals(masked_code, code_literals) == code
    text = 'Example: `' + code + '` Formula: $$ E = mc^2 $$.'
    masked, literals = mask_literals(text)
    assert set(literals.values()) == {'`'+code+'`', '$$ E = mc^2 $$'}
    for token, original in literals.items():
        assert masked.count(token) == 1
        masked = masked.replace(token, original)
    assert masked == text


def test_restore_does_not_expand_placeholder_text_inside_an_original_literal():
    literals = {'⟪保留值0⟫': '`⟪保留1⟫`', '⟪保留1⟫': '8 MB/s'}
    assert restore_literals('⟪保留值0⟫ / ⟪保留1⟫', literals) == '`⟪保留1⟫` / 8 MB/s'


def test_multi_letter_operands_are_protected_without_masking_neighboring_prose():
    text = 'Need + RuntimeContract -> r_NRA\nToolCall -> Observation -> Verification -> StateWrite\nAn agent must check its evidence.'
    masked, literals = mask_literals(text)
    assert list(literals.values()) == ['Need + RuntimeContract -> r_NRA', 'ToolCall -> Observation -> Verification -> StateWrite']
    assert 'An agent must check its evidence.' in masked
    assert restore_literals(masked, literals) == text


def test_html_superscript_does_not_turn_neighboring_prose_into_an_expression():
    text = 'Concurrency control<sup>1</sup> avoids conflicts.'
    masked, literals = mask_literals(text)
    assert list(literals.values()) == ['1']
    assert 'Concurrency control<sup>' in masked


def test_translation_can_translate_prose_next_to_signed_values_and_slashes():
    text = 'The difference +0.1 is measured at the search/answer boundary.'
    masked, literals = mask_literals(text)
    assert list(literals.values()) == ['+0.1']
    assert 'difference' in masked and 'search/answer' in masked
    assert preserves_literals('搜尋／回答邊界的差異為 +0.1。', [{'kind': 'paragraph', 'exact_text': text}])


def test_unknown_placeholder_cannot_leak_into_learning_text():
    with pytest.raises(ValueError, match='SEMANTIC_OUTPUT_INVALID'):
        restore_literals('⟪保留0⟫ 與 ⟪保留99⟫', {'⟪保留0⟫': '99'})


def test_japanese_explanation_is_not_mistaken_for_chinese():
    assert _needs_translation('遅延は250 msです。')
    assert not _needs_translation('延遲為250 ms。')


@pytest.mark.parametrize('text', ['問題 формулировка', '問題 ձևակերպում', '問題 διατύπωση', '條件 شرح', '數值 विवरण'])
def test_foreign_word_inside_chinese_is_still_rejected(text):
    assert _needs_translation(text)


def test_original_foreign_code_string_is_not_translated():
    assert not _needs_translation('原始字串為 `Привет`，不得改寫。')


def test_mixed_script_correction_preserves_all_other_text_and_formula():
    text = '問題 формулировка：J(πθ) = E " T X t=1 γt−1rt。原字串 `Привет`。'
    template, literals = language_correction_template(text)
    assert 'формулировка' in template and 'J(' not in template and 'Привет' not in template
    corrected = restore_literals(template.replace('формулировка', '表述'), literals)
    assert corrected == text.replace('формулировка', '表述')


def test_formula_and_next_paragraph_are_not_masked_as_one_expression():
    formula = 'Combine(A, B) = scale(AB^T / root(d) + N)B'
    explanation = '^T 表示轉置；root 表示平方根；遮罩可見位置加 0。'
    context = build_document_context([_page(1, [
        _block(1, 0, 'paragraph', formula), _block(1, 1, 'paragraph', explanation),
    ])], page_count=1)
    bundle = {'evidence': context['evidence'], 'sections': context['sections']}
    response = {'concepts': [{'l': '矩陣運算', 'a': [], 'c': [{'m': '矩陣運算包含遮罩。', 's': [0, 1]}]}], 'relations': []}
    def translate(request, schema):
        item = request['localization']['claim_0_0']
        assert list(item['literals'].values()) == [formula, '0']
        assert '\n\n' in item['text']
        tokens = list(item['literals'])
        return {'claim_0_0': f'計算公式為 {tokens[0]}。^T 表示轉置；root 表示平方根；遮罩可見位置加 {tokens[1]}。'}
    result = localize_block(response, context, bundle, translate)
    assert result['concepts'][0]['c'][0]['m'].count(formula) == 1


@pytest.mark.parametrize('omit_values', [False, True])
def test_numeric_table_cells_are_restored_together_without_losing_order(omit_values):
    texts = ['Utility', '0.0', '+0.057', '0.02', 'Cost', '5', '8']
    context = build_document_context([_page(1, [
        _block(1, i, 'paragraph', value) for i, value in enumerate(texts)
    ])], page_count=1)
    bundle = {'evidence': context['evidence'], 'sections': context['sections']}
    response = {'concepts': [{'l': '實驗表格', 'a': [], 'c': [
        {'m': '效用及成本比較。', 's': list(range(len(texts)))}
    ]}], 'relations': []}
    def translate(request, schema):
        item = request['localization']['claim_0_0']
        assert list(item['literals'].values()) == ['0.0\n\n+0.057\n\n0.02', '5\n\n8']
        value = item['text'].replace('Utility', '效用').replace('Cost', '成本')
        if omit_values:
            value = value.replace(next(iter(item['literals'])), '')
        return {'claim_0_0': value}
    if omit_values:
        with pytest.raises(ValueError, match='SEMANTIC_OUTPUT_INVALID'):
            localize_block(response, context, bundle, translate)
    else:
        result = localize_block(response, context, bundle, translate)
        assert result['concepts'][0]['c'][0]['m'] == '\n\n'.join(['效用', *texts[1:4], '成本', *texts[5:]])
        assert [e['exact_text'] for e in context['evidence']] == texts


def test_isolated_source_labels_are_not_mistaken_for_untranslated_prose():
    labels = [{'kind': 'heading', 'exact_text': word} for word in ['un', 'believ', 'able']]
    text = '子詞切分示意：un believ able。'
    assert not _needs_translation(text, labels)
    assert not _needs_translation(text, [{'kind': 'heading', 'quote': word} for word in ['un', 'believ', 'able']])
    assert _needs_translation(text)
    assert _needs_translation(text, [{**item, 'kind': 'paragraph'} for item in labels])
    assert _needs_translation('子詞切分示意：un believ invented。', labels)
    assert _needs_translation('說明：these words need translation。', labels)
    assert _needs_translation('un believ able', labels)
    assert _needs_translation('子詞切分 формулировка：un believ able。', labels)


def test_localization_only_accepts_labels_from_the_claims_own_evidence():
    blocks = [_block(1, 0, 'paragraph', '子詞切分示意。')]
    blocks += [_block(1, i+1, 'heading', word) for i, word in enumerate(['un', 'believ', 'able'])]
    context = build_document_context([_page(1, blocks)], page_count=1)
    bundle = {'evidence': context['evidence'], 'sections': context['sections']}
    response = {'concepts': [{'l': '子詞切分', 'a': [], 'c': [{'m': '子詞切分示意：un believ able。', 's': [0,1,2,3]}]}], 'relations': []}
    def forbidden(*_): raise AssertionError('No translation needed for cited diagram labels')
    assert localize_block(response, context, bundle, forbidden) == response
    response['concepts'][0]['c'][0]['s'] = [0]
    with pytest.raises(AssertionError, match='No translation needed'):
        localize_block(response, context, bundle, forbidden)
