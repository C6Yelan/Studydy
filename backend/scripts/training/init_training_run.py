# backend/scripts/training/init_training_run.py
"""Initialize a reproducible training run folder and metadata (T6)."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml


BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.training.validate_training_config import (  # noqa: E402
    TrainingConfigValidationResult,
    validate_training_config_file,
)


DEFAULT_TRAINING_SCHEMA_PATH = (
    BACKEND_ROOT / "docs" / "ai" / "training" / "training_config.schema.v1.json"
)
DEFAULT_RUNS_DIR_FALLBACK = "backend/datasets_local/training/runs"


@dataclass(slots=True)
class InitRunResult:
    run_id: str
    run_dir: Path
    run_meta_path: Path
    summary_path: Path
    meta: dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def generate_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{timestamp}-{uuid4().hex[:8]}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def to_repo_relative(path: Path) -> str:
    resolved = path.resolve()
    for root in (REPO_ROOT, BACKEND_ROOT, Path.cwd()):
        try:
            return resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
    return resolved.as_posix()


def _unique_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve())
        if key in seen:
            continue
        unique.append(path)
        seen.add(key)
    return unique


def _preferred_roots(path_obj: Path, config_dir: Path) -> list[Path]:
    if path_obj.parts and path_obj.parts[0] == "backend":
        return _unique_paths([REPO_ROOT, Path.cwd(), BACKEND_ROOT, config_dir])

    if path_obj.parts and path_obj.parts[0] in {"datasets_local", "docs", "scripts", "app", "tests"}:
        return _unique_paths([BACKEND_ROOT, Path.cwd(), REPO_ROOT, config_dir])

    return _unique_paths([config_dir, Path.cwd(), BACKEND_ROOT, REPO_ROOT])


def resolve_config_path(path_text: str, *, config_dir: Path, must_exist: bool) -> Path:
    path_obj = Path(path_text)
    if path_obj.is_absolute():
        resolved = path_obj.resolve()
        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"path does not exist: {path_text}")
        return resolved

    roots = _preferred_roots(path_obj, config_dir)
    checked: list[str] = []
    for root in roots:
        candidate = (root / path_obj).resolve()
        checked.append(candidate.as_posix())
        if candidate.exists():
            return candidate

    if must_exist:
        checked_preview = ", ".join(checked)
        raise FileNotFoundError(f"path does not exist: {path_text}; checked: {checked_preview}")

    return (roots[0] / path_obj).resolve()


def _resolve_git_dir(repo_root: Path) -> Path | None:
    git_path = repo_root / ".git"
    if git_path.is_dir():
        return git_path
    if not git_path.is_file():
        return None

    text = git_path.read_text(encoding="utf-8").strip()
    if not text.startswith("gitdir:"):
        return None

    git_dir_text = text.split(":", 1)[1].strip()
    git_dir_path = Path(git_dir_text)
    if not git_dir_path.is_absolute():
        git_dir_path = (repo_root / git_dir_path).resolve()
    if git_dir_path.is_dir():
        return git_dir_path
    return None


def _read_packed_ref(git_dir: Path, ref_name: str) -> str | None:
    packed_refs = git_dir / "packed-refs"
    if not packed_refs.exists():
        return None

    with packed_refs.open("r", encoding="utf-8") as file_obj:
        for line in file_obj:
            payload = line.strip()
            if not payload or payload.startswith("#") or payload.startswith("^"):
                continue
            columns = payload.split(" ", 1)
            if len(columns) != 2:
                continue
            commit, ref = columns
            if ref == ref_name:
                return commit
    return None


def get_git_commit(repo_root: Path = REPO_ROOT) -> str | None:
    git_dir = _resolve_git_dir(repo_root)
    if git_dir is None:
        return None

    head_path = git_dir / "HEAD"
    if not head_path.exists():
        return None

    head = head_path.read_text(encoding="utf-8").strip()
    if head.startswith("ref:"):
        ref_name = head.split(":", 1)[1].strip()
        ref_path = git_dir / ref_name
        if ref_path.exists():
            commit = ref_path.read_text(encoding="utf-8").strip()
        else:
            commit = _read_packed_ref(git_dir, ref_name)
    else:
        commit = head

    if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit):
        return commit
    return None


def _build_file_fingerprint(path_text: str, *, config_dir: Path) -> dict[str, Any]:
    resolved = resolve_config_path(path_text, config_dir=config_dir, must_exist=True)
    return {
        "path": path_text,
        "resolved_path": to_repo_relative(resolved),
        "sha256": sha256_file(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def build_dataset_fingerprint(dataset: dict[str, Any], *, config_dir: Path) -> dict[str, Any]:
    fingerprint: dict[str, Any] = {
        "dataset_version": dataset.get("dataset_version"),
        "train": _build_file_fingerprint(str(dataset["train_path"]), config_dir=config_dir),
        "valid": None,
    }

    valid_path = dataset.get("valid_path")
    if isinstance(valid_path, str) and valid_path.strip():
        fingerprint["valid"] = _build_file_fingerprint(valid_path.strip(), config_dir=config_dir)

    return fingerprint


def _get_schema_runs_dir_default(
    schema_path: Path,
    *,
    fallback: str = DEFAULT_RUNS_DIR_FALLBACK,
) -> str:
    try:
        schema = json.loads(schema_path.resolve().read_text(encoding="utf-8"))
    except Exception:
        return fallback

    if not isinstance(schema, dict):
        return fallback

    defs = schema.get("$defs")
    if not isinstance(defs, dict):
        return fallback

    output = defs.get("output")
    if not isinstance(output, dict):
        return fallback

    properties = output.get("properties")
    if not isinstance(properties, dict):
        return fallback

    runs_dir = properties.get("runs_dir")
    if not isinstance(runs_dir, dict):
        return fallback

    default_value = runs_dir.get("default")
    if isinstance(default_value, str) and default_value.strip():
        return default_value.strip()
    return fallback


def _resolve_runs_dir(
    config: dict[str, Any],
    *,
    config_dir: Path,
    default_runs_dir_text: str,
) -> tuple[str, Path]:
    output = config.get("output", {})
    runs_dir_text = default_runs_dir_text
    if isinstance(output, dict):
        candidate = output.get("runs_dir")
        if isinstance(candidate, str) and candidate.strip():
            runs_dir_text = candidate.strip()

    runs_dir_path = resolve_config_path(runs_dir_text, config_dir=config_dir, must_exist=False)
    return runs_dir_text, runs_dir_path


def write_run_summary(run_dir: Path, meta: dict[str, Any]) -> Path:
    summary_path = run_dir / "run_summary.md"
    dataset_fingerprint = meta["dataset_fingerprint"]
    lines = [
        "# Training Run Summary",
        "",
        f"- run_id: `{meta['run_id']}`",
        f"- created_at_utc: `{meta['created_at_utc']}`",
        f"- git_commit: `{meta['git_commit']}`",
        f"- base_model: `{meta['base_model']}`",
        f"- training_method: `{meta['training_method']}`",
        f"- config_path: `{meta['config_path']}`",
        f"- config_path_resolved: `{meta['config_path_resolved']}`",
        "",
        "## Dataset Fingerprint",
        f"- dataset_version: `{dataset_fingerprint.get('dataset_version')}`",
        (
            f"- train: `{dataset_fingerprint['train']['resolved_path']}` "
            f"(sha256=`{dataset_fingerprint['train']['sha256']}`)"
        ),
    ]

    valid_fingerprint = dataset_fingerprint.get("valid")
    if isinstance(valid_fingerprint, dict):
        lines.append(
            f"- valid: `{valid_fingerprint['resolved_path']}` "
            f"(sha256=`{valid_fingerprint['sha256']}`)"
        )
    else:
        lines.append("- valid: `null`")

    lines.extend(
        [
            "",
            "## Files",
            "- `run_meta.json`",
            "- `config.snapshot.yaml`",
            "- `run_summary.md`",
        ]
    )

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_path


def _validated_config(
    *,
    config_path: Path,
    schema_path: Path,
) -> TrainingConfigValidationResult:
    result = validate_training_config_file(config_path=config_path, schema_path=schema_path)
    if not result.is_valid:
        details = "\n".join(f"- {error}" for error in result.errors)
        raise ValueError(f"invalid training config:\n{details}")
    return result


def init_training_run(
    *,
    config_path: Path,
    schema_path: Path = DEFAULT_TRAINING_SCHEMA_PATH,
) -> InitRunResult:
    validation = _validated_config(config_path=config_path, schema_path=schema_path)
    config = validation.config
    if config is None:
        raise ValueError("validated config should not be empty")

    config_dir = validation.config_path.parent
    schema_default_runs_dir = _get_schema_runs_dir_default(validation.schema_path)
    runs_dir_text, runs_dir_path = _resolve_runs_dir(
        config,
        config_dir=config_dir,
        default_runs_dir_text=schema_default_runs_dir,
    )

    run_id = generate_run_id()
    run_dir = runs_dir_path / run_id
    if run_dir.exists():
        raise FileExistsError(f"run output already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=False)

    snapshot_path = run_dir / "config.snapshot.yaml"
    snapshot_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    dataset_fingerprint = build_dataset_fingerprint(config["dataset"], config_dir=config_dir)
    created_at = utc_now_iso()
    git_commit = get_git_commit()

    meta: dict[str, Any] = {
        "run_id": run_id,
        "created_at_utc": created_at,
        "git_commit": git_commit,
        "config_path": str(config_path),
        "config_path_resolved": to_repo_relative(validation.config_path),
        "config_snapshot_path": "config.snapshot.yaml",
        "config_snapshot": config,
        "dataset_fingerprint": dataset_fingerprint,
        "base_model": config["base_model"],
        "training_method": config["training_method"],
        "hyperparams": config["hyperparams"],
        "lora": config["lora"],
        "output": {
            "runs_dir": runs_dir_text,
            "resolved_runs_dir": to_repo_relative(runs_dir_path),
            "resolved_run_dir": to_repo_relative(run_dir),
        },
    }

    run_meta_path = run_dir / "run_meta.json"
    run_meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_path = write_run_summary(run_dir, meta)
    return InitRunResult(
        run_id=run_id,
        run_dir=run_dir,
        run_meta_path=run_meta_path,
        summary_path=summary_path,
        meta=meta,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize T6 training run metadata.")
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to training config YAML/JSON.",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_TRAINING_SCHEMA_PATH,
        help=f"Training config schema path (default: {DEFAULT_TRAINING_SCHEMA_PATH})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = init_training_run(config_path=args.config, schema_path=args.schema)
    except ValueError as exc:
        if str(exc).startswith("invalid training config:"):
            print("FAIL: training config validation failed.", file=sys.stderr)
            print(exc, file=sys.stderr)
            return 2
        print(f"init_training_run failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"init_training_run failed: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "run_dir": to_repo_relative(result.run_dir),
                "run_meta_path": to_repo_relative(result.run_meta_path),
                "run_summary_path": to_repo_relative(result.summary_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
