from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from learning_state.assessment import score_submission
from learning_state.learning_state import LearningStateError, build_learning_state


def _read_json(input_path: Path) -> Any:
    try:
        return json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LearningStateError("INPUT_READ_FAILED") from error


def _get_trusted_learner_id(context: Any) -> str:
    if (
        not isinstance(context, dict)
        or set(context) != {"schema", "learner_id"}
        or context["schema"] != "trusted-learner-context/v1"
        or not isinstance(context["learner_id"], str)
        or not context["learner_id"].strip()
    ):
        raise LearningStateError("LEARNING_INPUT_INVALID")
    return context["learner_id"]


def build_learning_state_from_files(
    *,
    learner_context_path: Path,
    knowledge_map_path: Path,
    learning_path_path: Path,
    assessment_path: Path,
    submissions_path: Path,
    learning_events_path: Path,
) -> dict[str, Any]:
    """讀取 local JSON，先原子評分每次 submission，再建立學習狀態。"""
    trusted_learner_id = _get_trusted_learner_id(_read_json(learner_context_path))
    knowledge_map = _read_json(knowledge_map_path)
    learning_path = _read_json(learning_path_path)
    assessment = _read_json(assessment_path)
    submissions = _read_json(submissions_path)
    if not learning_events_path.is_file():
        raise LearningStateError("LEARNING_EVENT_STREAM_MISSING")
    learning_events = _read_json(learning_events_path)
    if not isinstance(submissions, list):
        raise LearningStateError("SUBMISSION_INVALID")

    answer_events: list[dict[str, Any]] = []
    for submission in submissions:
        scored_submission = score_submission(
            assessment,
            submission,
            trusted_learner_id=trusted_learner_id,
            knowledge_map=knowledge_map,
            learning_path_revision=(
                learning_path.get("revision")
                if isinstance(learning_path, dict)
                else None
            ),
            existing_events=answer_events,
        )
        if scored_submission["processing"] != "succeeded":
            raise LearningStateError(scored_submission["reason_code"])
        if not scored_submission["replayed"]:
            answer_events.extend(scored_submission["answer_events"])

    return build_learning_state(
        trusted_learner_id=trusted_learner_id,
        knowledge_map=knowledge_map,
        learning_path=learning_path,
        assessment=assessment,
        answer_events=answer_events,
        learning_event_stream=learning_events,
    )


def write_learning_state(output_path: Path, learning_state: dict[str, Any]) -> None:
    """同目錄暫存後一次發布；既有 artifact 不會被覆寫。"""
    if output_path.exists():
        raise LearningStateError("ARTIFACT_ALREADY_EXISTS")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(
            learning_state,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_name, output_path)
        except FileExistsError as error:
            raise LearningStateError("ARTIFACT_ALREADY_EXISTS") from error
        Path(temporary_name).unlink()
        temporary_name = None
    except LearningStateError:
        raise
    except OSError as error:
        raise LearningStateError("ARTIFACT_WRITE_FAILED") from error
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def _build_argument_parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Build a learning state from local JSON files"
    )
    argument_parser.add_argument("--learner-context", type=Path, required=True)
    argument_parser.add_argument("--knowledge-map", type=Path, required=True)
    argument_parser.add_argument("--learning-path", type=Path, required=True)
    argument_parser.add_argument("--assessment", type=Path, required=True)
    argument_parser.add_argument("--submissions", type=Path, required=True)
    argument_parser.add_argument("--learning-events", type=Path, required=True)
    argument_parser.add_argument("--output", type=Path, required=True)
    return argument_parser


def main(argv: list[str] | None = None) -> int:
    arguments = _build_argument_parser().parse_args(argv)
    try:
        learning_state = build_learning_state_from_files(
            learner_context_path=arguments.learner_context,
            knowledge_map_path=arguments.knowledge_map,
            learning_path_path=arguments.learning_path,
            assessment_path=arguments.assessment,
            submissions_path=arguments.submissions,
            learning_events_path=arguments.learning_events,
        )
        write_learning_state(arguments.output, learning_state)
    except LearningStateError as error:
        print(error.reason_code, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
