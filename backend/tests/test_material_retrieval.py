from copy import deepcopy

from knowledge_map.retrieval import build_passages, distinct_passages, rank_texts, terms, text_ranges


def document():
    return {'revision': 'synthetic-revision', 'input_binding': {
        'bundle': {'pages': [{'source_id': 'source-a', 'normalized_page': 8}]},
        'manifest': {'items': [{'source_id': 'source-a', 'original_name': 'synthetic.pdf'}]}},
        'concepts': [{'concept_id': 'c1', 'label': '佇列', 'aliases': ['Queue', '先到先服務'],
                      'claims': [{'evidence_refs': ['e1']}]}],
        'evidence': [{'evidence_id': 'e1', 'page': 1, 'page_ref': 'p1', 'block_order': 0,
            'kind': 'paragraph', 'source': 'native_text', 'source_locator': {'page': 1, 'region': [0, 0, 10, 10]},
            'heading': '資料結構', 'section_id': 'section-a', 'exact_text': '佇列採先進先出；空佇列不可取出元素。'}]}


def test_index_keeps_original_locator_negation_and_search_aliases():
    row, = build_passages(document())
    assert row['evidence']['normalized_page'] == 8
    assert row['evidence']['page'] == 1
    assert row['evidence']['quote'] == document()['evidence'][0]['exact_text']
    assert '先到先服務' in row['hints'] and row['concept_ids'] == ['c1']
    assert '先到' in terms(' '.join(row['hints']))


def test_large_evidence_can_be_retrieved_in_parts_without_losing_characters():
    source = ('第一個步驟必須保留。\n第二步不可略過條件。\n' * 180) + '最後的例外條件。'
    value = document(); value['evidence'][0]['exact_text'] = source
    rows = build_passages(value)
    assert len(rows) > 1
    assert ''.join(row['text'] for row in rows) == source
    assert len({row['passage_id'] for row in rows}) == len(rows)
    assert all(source[row['start']:row['end']] == row['text'] for row in rows)


def test_exact_duplicate_keeps_both_sources_but_negation_remains_separate():
    first, = build_passages(document())
    second = deepcopy(first); second['evidence']['source_id'] = 'source-b'
    changed = deepcopy(first); changed['text'] = first['text'].replace('不可', '可以')
    rows = distinct_passages([first, second, changed])
    assert len(rows) == 2 and len(rows[0]['references']) == 2
    assert rows[1]['text'] != rows[0]['text']


def test_local_search_finds_both_sides_of_comparison_and_excludes_unrelated_page():
    texts = ['TCP provides reliable ordered delivery.', 'UDP does not guarantee delivery order.',
             'Binary search requires sorted data.']
    ranked = rank_texts('Compare TCP and UDP delivery', texts)
    assert {index for index, _ in ranked} == {0, 1}


def test_empty_or_unmatched_query_does_not_return_random_source():
    assert rank_texts('請問什麼', ['queue operations']) == []
    assert rank_texts('Photosynthesis', ['queue operations']) == []


def test_splitting_preserves_technical_literals():
    value = 'TCP\x00 SYN-ACK != ACK\n' * 300
    assert ''.join(value[start:end] for start, end in text_ranges(value)) == value


def test_podcast_context_marks_missing_middle_and_preserves_contiguous_parts(monkeypatch):
    from runtime import podcast_sources
    value=document();value['evidence'][0]['exact_text']='條件與例外必須保留。'*900
    rows=build_passages(value)
    chosen=[rows[0],rows[-2],rows[-1]]
    monkeypatch.setattr(podcast_sources.retrieval,'evidence_context',lambda *a:chosen)
    context=podcast_sources.source_context(None,None,None,None,{'claims':[{'label':'佇列','text':'佇列採先進先出；空佇列不可取出元素。','evidence':[{'evidence_id':'e1'}]}]})
    assert context['p1']['blocks'][0]['text']==rows[0]['text']+'\n[…]\n'+rows[-2]['text']+rows[-1]['text']


def test_podcast_keeps_all_required_text_when_optional_neighbors_do_not_fit(monkeypatch):
    from runtime import podcast_sources
    required=build_passages(document())[0]
    neighbor=deepcopy(required);neighbor.update(position=1,evidence_id='other',text='額外背景。'*2500)
    neighbor['evidence']['evidence_id']='other';neighbor['end']=len(neighbor['text'])
    monkeypatch.setattr(podcast_sources.retrieval,'evidence_context',lambda *a:[required,neighbor])
    context=podcast_sources.source_context(None,None,None,None,{'claims':[{'label':'佇列','text':'佇列採先進先出；空佇列不可取出元素。','evidence':[{'evidence_id':'e1'}]}]})
    assert [b['evidence_id'] for b in context['p1']['blocks']]==['e1']
    assert context['p1']['blocks'][0]['text']==required['text']
