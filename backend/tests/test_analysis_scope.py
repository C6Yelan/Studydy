from hashlib import sha256

import pymupdf
import pytest

from pdf_evidence.analysis_scope import locate_scope, requested_scope, scoped_page, selected_pages


def pdf_document():
    with pymupdf.open() as document:
        for number in range(10):
            page = document.new_page()
            page.insert_text((40, 50), f'Section {number + 1}: unrelated background.')
            if number == 6:
                page.insert_text((40, 140), 'An empty queue must not remove an element.')
                page.insert_text((40, 180), 'Check whether the queue is empty before removal.')
        return document.tobytes()


def test_long_document_only_selects_needed_original_page_with_context():
    data = pdf_document()
    requested = requested_scope(data, [{'quote': 'An empty queue must not remove an element.'}])
    scope = locate_scope(data, requested)
    item = {'analysis_scope': scope, 'original_sha256': sha256(data).hexdigest(), 'page_count': 10}
    assert selected_pages(item) == [7]
    assert len(scope['pages'][0]['regions']) == 3
    with pymupdf.open(stream=data, filetype='pdf') as original:
        assert len(original) == 10


def test_unmatched_quote_never_falls_back_to_full_document():
    data = pdf_document()
    with pytest.raises(ValueError, match='SOURCE_SCOPE_UNRESOLVED'):
        locate_scope(data, requested_scope(data, [{'quote': 'A fabricated quote.'}]))


def test_changed_source_cannot_reuse_selection():
    data = pdf_document()
    scope = locate_scope(data, requested_scope(data, [{'quote': 'An empty queue must not remove an element.'}]))
    with pytest.raises(ValueError, match='SOURCE_SCOPE_INVALID'):
        selected_pages({'analysis_scope': scope, 'original_sha256': '0' * 64, 'page_count': 10})


def test_paragraph_selection_keeps_whole_original_evidence_not_just_matching_words():
    first = {'text': 'Required definition and exception.', 'locator': {'region': [0, 0, 100, 40]}}
    second = {'text': 'Unrelated experiment.', 'locator': {'region': [0, 100, 100, 140]}}
    scope = {'pages': [{'page': 7, 'regions': [[5, 5, 80, 20]]}]}
    page = {'page_number': 7, 'evidence_blocks': [first, second]}
    result = scoped_page(page, scope)
    assert result['evidence_blocks'] == [first]
    assert page['evidence_blocks'] == [first, second]


def test_missing_or_empty_selection_is_not_accepted_for_research():
    with pytest.raises(ValueError, match='SOURCE_SCOPE_EMPTY'):
        requested_scope(b'original', [])


def test_selected_explanation_keeps_the_referenced_figure_on_another_page():
    with pymupdf.open() as document:
        document.new_page().insert_text((40, 50), 'See Figure 2 for the tool permission boundary.')
        page = document.new_page()
        page.insert_text((40, 50), 'Figure 2: External tool authorization.')
        page.draw_rect((40, 100, 400, 400))
        document.new_page().insert_text((40, 50), 'Unrelated bibliography.')
        data = document.tobytes()
    scope = locate_scope(data, requested_scope(data, [{'quote': 'See Figure 2 for the tool permission boundary.'}]))
    assert [page['page'] for page in scope['pages']] == [1, 2]
    assert scope['pages'][1]['regions'] == [[0.0, 0.0, 595.0, 842.0]]
