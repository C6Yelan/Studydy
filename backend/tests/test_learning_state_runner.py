import json

import pytest

from knowledge_map.artifacts import _with_revision, build_initial_learning_path
from learning_state.assessment import canonical_sha256
from learning_state.local_runner import main, write_learning_state
from learning_state.learning_state import LearningStateError


def _write_local_runner_files(tmp_path) -> dict[str, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    map_content = {
        "schema": "knowledge-map/v1",
        "source_output_id": "source:test",
        "material_ref": "material:test",
        "pages": [],
        "concepts": [
            {
                "concept_id": "concept:a",
                "members": [
                    {
                        "candidate_id": "candidate:a",
                        "page_number": 1,
                        "evidence_ids": ["evidence:a"],
                    }
                ],
            }
        ],
        "evidence_index": [{"evidence_id": "evidence:a"}],
        "relations": [],
        "review_items": [],
        "known_limitations": [],
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "KNOWLEDGE_MAP_ACCEPTED",
    }
    knowledge_map = _with_revision("knowledge-map", map_content)
    learning_path = build_initial_learning_path(knowledge_map)
    questions = [
        {
            "question_id": f"question:{index}",
            "concept_id": "concept:a",
            "question_type": "single_choice",
            "prompt": f"題目 {index}",
            "options": [
                {"option_id": "option:correct", "text": "正確"},
                {"option_id": "option:wrong", "text": "錯誤"},
            ],
            "answer_key_option_id": "option:correct",
            "source_evidence_ids": ["evidence:a"],
        }
        for index in range(1, 4)
    ]
    assessment_content = {
        "schema": "assessment/v1",
        "assessment_id": "assessment:test",
        "version": "1",
        "knowledge_map_revision": knowledge_map["revision"],
        "learning_path_revision": learning_path["revision"],
        "scoring_rule_version": "single-choice-exact/v1",
        "questions": questions,
        "practice_sets": [
            {
                "practice_set_id": "practice:a",
                "concept_id": "concept:a",
                "question_ids": ["question:1", "question:2", "question:3"],
            }
        ],
        "processing": "succeeded",
        "quality": "accepted",
        "decision": "retain",
        "reason_code": "ASSESSMENT_ACCEPTED",
    }
    assessment = {
        "revision": f"assessment:sha256:{canonical_sha256(assessment_content)}",
        **assessment_content,
    }
    submission = {
        "schema": "assessment-submission/v1",
        "assessment_id": assessment["assessment_id"],
        "assessment_revision": assessment["revision"],
        "idempotency_key": "submission:1",
        "submitted_at": "2026-08-09T10:00:00+08:00",
        "responses": [
            {
                "question_id": f"question:{index}",
                "selected_option_id": "option:correct",
            }
            for index in range(1, 4)
        ],
    }
    inputs = {
        "learner_context": {
            "schema": "trusted-learner-context/v1",
            "learner_id": "learner:trusted",
        },
        "knowledge_map": knowledge_map,
        "learning_path": learning_path,
        "assessment": assessment,
        "submissions": [submission, submission],
        "learning_events": [],
    }
    paths = {}
    for name, value in inputs.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        paths[name] = path
    paths["output"] = tmp_path / "learning-state.json"
    return paths


def _build_runner_arguments(paths: dict[str, object]) -> list[str]:
    return [
        "--learner-context",
        str(paths["learner_context"]),
        "--knowledge-map",
        str(paths["knowledge_map"]),
        "--learning-path",
        str(paths["learning_path"]),
        "--assessment",
        str(paths["assessment"]),
        "--submissions",
        str(paths["submissions"]),
        "--learning-events",
        str(paths["learning_events"]),
        "--output",
        str(paths["output"]),
    ]


def test_cli_scores_replay_once_and_writes_canonical_state(tmp_path):
    paths = _write_local_runner_files(tmp_path)

    assert main(_build_runner_arguments(paths)) == 0
    state = json.loads(paths["output"].read_text(encoding="utf-8"))

    assert state["schema"] == "learning-state/v1"
    assert state["learner_id"] == "learner:trusted"
    assert len(state["source_answer_event_ids"]) == 3
    assert state["mastery"][0]["valid_answer_count"] == 3


def test_cli_same_inputs_produce_identical_bytes_in_separate_outputs(tmp_path):
    first_paths = _write_local_runner_files(tmp_path / "first")
    second_paths = _write_local_runner_files(tmp_path / "second")

    assert main(_build_runner_arguments(first_paths)) == 0
    assert main(_build_runner_arguments(second_paths)) == 0
    assert first_paths["output"].read_bytes() == second_paths["output"].read_bytes()


def test_cli_subset_submission_reaches_insufficient_medium_fallback(tmp_path):
    paths = _write_local_runner_files(tmp_path)
    submissions = json.loads(paths["submissions"].read_text(encoding="utf-8"))
    submissions = [submissions[0]]
    submissions[0]["responses"] = [submissions[0]["responses"][0]]
    paths["submissions"].write_text(
        json.dumps(submissions, ensure_ascii=False), encoding="utf-8"
    )

    assert main(_build_runner_arguments(paths)) == 0
    state = json.loads(paths["output"].read_text(encoding="utf-8"))

    assert state["mastery"][0]["valid_answer_count"] == 1
    assert state["mastery"][0]["final_status"] != "mastered"
    assert state["suggestion"]["learning_suggestion_score"] == pytest.approx(0.60)
    assert state["suggestion"]["level"] == "medium"
    assert state["suggestion"]["is_personalized"] is False
    assert state["suggestion"]["action"] == "follow_initial_path"


def test_cli_missing_event_stream_fails_without_output(tmp_path, capsys):
    paths = _write_local_runner_files(tmp_path)
    paths["learning_events"].unlink()

    assert main(_build_runner_arguments(paths)) == 1
    assert capsys.readouterr().err.strip() == "LEARNING_EVENT_STREAM_MISSING"
    assert not paths["output"].exists()


def test_artifact_write_failure_leaves_no_partial_output(tmp_path, monkeypatch):
    output = tmp_path / "learning-state.json"

    def fail_link(source, destination):
        raise OSError("simulated write failure")

    monkeypatch.setattr("learning_state.local_runner.os.link", fail_link)
    with pytest.raises(LearningStateError, match="ARTIFACT_WRITE_FAILED"):
        write_learning_state(output, {"schema": "learning-state/v1"})

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []
