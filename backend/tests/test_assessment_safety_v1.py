import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from learning_adaptation.assessments import AssessmentError, _candidate, _checked_candidate, _documents
from learning_adaptation.map_context import ClaimContext, ConceptContext, EvidenceContext
from runtime.storage.tables import StudySession


def _claim() -> ClaimContext:
    return ClaimContext(
        "claim:sha256:" + "1" * 64,
        "The null character is written as '\\0' and the array has 8 bytes.",
        (
            EvidenceContext(
                "evidence:sha256:" + "2" * 64,
                4,
                "The null character is written as '\\0' and the array has 8 bytes.",
                {"page": 4, "block_id": "block:sha256:" + "3" * 64, "region": [1, 2, 3, 4]},
            ),
        ),
    )


def _proposal() -> dict:
    return {
        "learning_angle": "exact null character spelling",
        "novelty": "distinct",
        "safety": "safe",
        "prompt": "根據教材，null character 的寫法是什麼？",
        "correct_answer": "'\\0'",
        "supporting_evidence_ids": ["evidence:sha256:" + "2" * 64],
        "distractors": ["'\\n'", "'EOF'", "nullptr"],
    }


def test_source_span_candidate_preserves_technical_token():
    candidate = _candidate(_proposal(), _claim(), set())
    assert candidate is not None
    assert candidate["correct_answer"] == "'\\0'"
    assert candidate["options"] == ["'\\0'", "'\\n'", "'EOF'", "nullptr"]


def test_unsupported_correct_duplicate_options_and_model_reject_are_blocked():
    unsupported = _proposal()
    unsupported["correct_answer"] = "16 bytes"
    assert _candidate(unsupported, _claim(), set()) is None

    duplicate = _proposal()
    duplicate["distractors"][0] = duplicate["correct_answer"]
    assert _candidate(duplicate, _claim(), set()) is None

    rejected = _proposal()
    rejected["safety"] = "reject"
    assert _candidate(rejected, _claim(), set()) is None


def test_distractor_can_appear_elsewhere_in_evidence_without_answering_this_question():
    """教材裡有 8 bytes 不代表它能回答 null character 的寫法。"""
    proposal = _proposal()
    proposal["distractors"][0] = "8 bytes"
    candidate = _candidate(proposal, _claim(), set())
    assert candidate is not None
    assert "8 bytes" in candidate["options"]


def test_exact_duplicate_is_blocked_but_checked_item_does_not_require_novelty():
    first = _candidate(_proposal(), _claim(), set())
    assert first is not None
    assert _candidate(_proposal(), _claim(), {first["semantic_identity"]}) is None

    uncertain = _proposal()
    uncertain["novelty"] = "uncertain"
    uncertain["prompt"] = "依教材，陣列有多少位元組？"
    uncertain["correct_answer"] = "8 bytes"
    projected = _candidate(uncertain, _claim(), set())
    assert projected is not None
    options = sorted(projected["options"], key=str.casefold)

    def solve(_client, **kwargs):
        assert "correct_answer" not in kwargs["request"]["questions"][0]
        assert "novelty" not in kwargs["request"]["questions"][0]
        return {
            "schema": "assessment-check-response/v1",
            "verdicts": [{
                "question_index": 0,
                "answer_status": "unique",
                "selected_option_index": options.index("8 bytes"),
                "duplicate_prior_index": None,
                "quality_issues": [],
            }],
        }

    projected = _checked_candidate(None, {}, _claim(), [projected], [], solve)
    assert projected is not None
    study = StudySession(
        study_session_id=uuid4(),
        learner_id=uuid4(),
        material_id=uuid4(),
        knowledge_structure_revision="knowledge-structure:sha256:" + "4" * 64,
        current_concept_id="concept:sha256:" + "5" * 64,
        no_safe_claim_ids=[],
        deferred_concept_ids=[],
        status="active",
        idempotency_key_sha256=b"x" * 32,
        request_fingerprint=b"y" * 32,
        started_at=datetime.now(UTC),
        last_event_number=0,
    )
    concept = ConceptContext(study.current_concept_id, "Null character", (_claim(),), ())
    lock = json.loads((Path(__file__).parents[2] / "local_ai/runtime-lock.json").read_text())
    public, private, provenance, qualified = _documents(
        study, concept, _claim(), projected, runtime_lock=lock
    )
    assert public["schema"] == "single-choice-assessment/v1"
    assert "correct_option_id" not in public
    assert private["correct_answer"] == "8 bytes"
    assert provenance["model_id"] == "google/gemma-4-31B-it-qat-w4a16-ct"
    assert qualified is True


def test_visually_equivalent_unicode_question_is_an_exact_duplicate():
    fullwidth = _proposal()
    fullwidth["prompt"] = "Ａ"
    first = _candidate(fullwidth, _claim(), set())
    assert first is not None
    ascii_form = _proposal()
    ascii_form["prompt"] = "A"
    assert _candidate(ascii_form, _claim(), {first["semantic_identity"]}) is None


# none 由下方「數值不能回答型別問題」案例覆蓋；此處保留其他獨立拒絕原因。
@pytest.mark.parametrize("status,selected,duplicate", [
    ("multiple", None, None), ("unique", 0, None), ("unique", 1, 0),
])
def test_blind_check_blocks_ambiguity_wrong_key_and_paraphrase(status, selected, duplicate):
    claim = ClaimContext("claim", "char ch has size 1", (
        EvidenceContext("evidence", 1, "char ch has size 1", {}),
    ))
    candidate = {"prompt": "What is the type of ch?", "correct_answer": "char",
                 "options": ["char", "1", "float", "int"]}
    prior = [SimpleNamespace(public_document={"prompt": "Give ch's type.", "options": [
        {"text": value} for value in ["char", "1", "float", "int"]
    ]})]
    def solve(_client, **kwargs):
        assert kwargs["request"]["questions"][0]["options"] == ["1", "char", "float", "int"]
        return {"schema": "assessment-check-response/v1", "verdicts": [{
            "question_index": 0, "answer_status": status,
            "selected_option_index": selected, "duplicate_prior_index": duplicate, "quality_issues": [],
        }]}
    assert _checked_candidate(None, {}, claim, [candidate], prior, solve) is None


def test_false_safe_numeric_answer_to_type_question_is_not_published():
    claim = ClaimContext("claim", "char ch has size 1", (
        EvidenceContext("evidence", 1, "char ch has size 1", {}),
    ))
    candidate = {"prompt": "What is the type of ch?", "correct_answer": "1",
                 "options": ["1", "char[]", "float", "int"]}
    response = {"schema": "assessment-check-response/v1", "verdicts": [{
        "question_index": 0, "answer_status": "none", "selected_option_index": None,
        "duplicate_prior_index": None, "quality_issues": [],
    }]}
    assert _checked_candidate(None, {}, claim, [candidate], [], lambda *_a, **_k: response) is None


def test_malformed_blind_check_cannot_be_treated_as_valid():
    candidate = _candidate(_proposal(), _claim(), set())
    with pytest.raises(AssessmentError, match="ASSESSMENT_CHECK_INVALID"):
        _checked_candidate(None, {}, _claim(), [candidate], [], lambda *_a, **_k: {
            "schema": "assessment-check-response/v1", "verdicts": [{
                "question_index": 0, "answer_status": "unique", "selected_option_index": True,
                "duplicate_prior_index": None, "quality_issues": [],
            }],
        })


def _ranked_candidates(issues, *, rejected=()):
    candidates = [
        _candidate(
            {**_proposal(), "prompt": f"候選 {index}：null character 的寫法是什麼？"},
            _claim(), set(),
        )
        for index in range(len(issues))
    ]
    calls = []

    def solve(_client, **kwargs):
        calls.append(kwargs)
        request = kwargs["request"]
        assert all(set(question) == {"question_index", "prompt", "options"} for question in request["questions"])
        verdicts = [
            {
                "question_index": index,
                "answer_status": "multiple" if index in rejected else "unique",
                "selected_option_index": (
                    None if index in rejected else question["options"].index("'\\0'")
                ),
                "duplicate_prior_index": None,
                "quality_issues": issues[index],
            }
            for index, question in enumerate(request["questions"])
        ]
        return {"schema": "assessment-check-response/v1", "verdicts": verdicts}

    selected = _checked_candidate(None, {}, _claim(), candidates, [], solve)
    assert len(calls) == 1
    return selected


def test_quality_selects_a_better_safe_candidate_instead_of_first_pass():
    selected = _ranked_candidates([["answer_cue"], [], ["weak_distractors"]])
    assert selected["prompt"].startswith("候選 1")
    assert selected["quality_selection"]["safe_candidate_count"] == 3


def test_quality_is_ranking_only_even_when_every_safe_candidate_has_issues():
    issues = ["trivial_focus", "answer_cue", "unclear_wording", "uneven_options", "weak_distractors"]
    selected = _ranked_candidates([issues, issues, issues])
    assert selected is not None
    assert selected["quality_selection"]["issues"] == issues
    # 同品質保持候選順序；不為美觀再呼叫模型或強迫無題。
    assert selected["quality_selection"]["candidate_index"] == 0


def test_unsafe_candidate_with_perfect_quality_never_beats_safe_basic_question():
    selected = _ranked_candidates([[], ["weak_distractors", "uneven_options"]], rejected={0})
    assert selected["quality_selection"]["candidate_index"] == 1
    assert selected["quality_selection"]["safe_candidate_count"] == 1


def test_repeated_quality_hint_is_counted_once_without_rejecting_question():
    selected = _ranked_candidates([["weak_distractors", "weak_distractors"]])
    assert selected["quality_selection"]["issues"] == ["weak_distractors"]


@pytest.mark.parametrize("issues", [None, "weak_distractors", [False], ["invented_flag"]])
def test_malformed_quality_response_is_reported_not_silently_published(issues):
    with pytest.raises(AssessmentError, match="ASSESSMENT_CHECK_INVALID"):
        _ranked_candidates([issues])


def test_same_answer_to_a_different_attribute_is_not_automatically_duplicate():
    candidate = _candidate(_proposal(), _claim(), set())
    prior = [SimpleNamespace(
        assessment_revision="assessment:sha256:" + "a" * 64,
        public_document={"prompt": "Which character ends a C string?", "options": [
            {"text": text} for text in candidate["options"]]},
    )]
    def solve(_client, **kwargs):
        return {"schema": "assessment-check-response/v1", "verdicts": [{
            "question_index": 0, "answer_status": "unique",
            "selected_option_index": kwargs["request"]["questions"][0]["options"].index("'\\0'"),
            "duplicate_prior_index": None, "quality_issues": [],
        }]}
    selected = _checked_candidate(None, {}, _claim(), [candidate], prior, solve)
    assert selected is not None
    assert selected["compared_assessment_revisions"] == [prior[0].assessment_revision]
