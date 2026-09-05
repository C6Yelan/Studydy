import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from learning_adaptation.assessments import _candidate, _documents
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

    semantic_equivalent = _proposal()
    semantic_equivalent["distractors"][0] = "the zero byte"
    semantic_equivalent["safety"] = "reject"
    assert _candidate(semantic_equivalent, _claim(), set()) is None


def test_distractor_can_appear_elsewhere_in_evidence_without_answering_this_question():
    """教材裡有 8 bytes 不代表它能回答 null character 的寫法。"""
    proposal = _proposal()
    proposal["distractors"][0] = "8 bytes"
    candidate = _candidate(proposal, _claim(), set())
    assert candidate is not None
    assert "8 bytes" in candidate["options"]


def test_exact_duplicate_is_blocked_but_novelty_uncertainty_does_not_block_publication():
    first = _candidate(_proposal(), _claim(), set())
    assert first is not None
    assert _candidate(_proposal(), _claim(), {first["semantic_identity"]}) is None

    uncertain = _proposal()
    uncertain["novelty"] = "uncertain"
    uncertain["prompt"] = "依教材選出 null character。"
    projected = _candidate(uncertain, _claim(), set())
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
        study, concept, _claim(), projected, runtime_lock=lock, prior_angles={"another angle"}
    )
    assert public["schema"] == "single-choice-assessment/v2"
    assert "correct_option_id" not in public
    assert private["correct_answer"] == "'\\0'"
    assert provenance["model_id"] == "Qwen/Qwen3.8-27B-FP8"
    assert qualified is False


def test_visually_equivalent_unicode_question_is_an_exact_duplicate():
    fullwidth = _proposal()
    fullwidth["prompt"] = "Ａ"
    first = _candidate(fullwidth, _claim(), set())
    assert first is not None
    ascii_form = _proposal()
    ascii_form["prompt"] = "A"
    assert _candidate(ascii_form, _claim(), {first["semantic_identity"]}) is None
