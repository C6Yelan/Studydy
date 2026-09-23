"""State/reason projection expectations are independent of the production helper."""
from itertools import product

import pytest

from knowledge_map.structure import _structure_status


@pytest.mark.parametrize('flags', list(product((False, True), repeat=6)))
def test_structure_status_preserves_order_and_truth_table(flags):
    has_concepts, excluded, claims, literals, relations, review = flags
    expected_reasons = []
    if excluded:
        expected_reasons.append('PAGES_EXCLUDED')
    if claims:
        expected_reasons.append('CLAIMS_REJECTED')
    if literals:
        expected_reasons.append('LITERALS_RESTORED_FROM_SOURCE')
    if relations:
        expected_reasons.append('RELATIONS_REJECTED')
    if review:
        expected_reasons.append('SOURCE_REVIEW_SUGGESTED')
    if not has_concepts:
        expected_reasons.append('NO_CANONICAL_CONCEPT')
    if not has_concepts:
        processing, quality, decision = 'failed', 'needs_review', 'reject'
    elif excluded or claims or literals or relations or review:
        processing, quality, decision = 'partial', 'needs_review', 'review'
    else:
        processing, quality, decision = 'succeeded', 'accepted', 'retain'
    assert _structure_status(has_concepts, excluded, int(claims) * 3,
                             int(literals) * 2, int(relations) * 4, review) == {
        'processing': processing, 'quality': quality, 'decision': decision,
        'reason_codes': expected_reasons,
    }
