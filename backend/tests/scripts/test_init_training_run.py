# backend/tests/scripts/test_init_training_run.py
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from scripts.training.init_training_run import (
    DEFAULT_RUNS_DIR_FALLBACK,
    init_training_run,
)
from scripts.training.validate_training_config import DEFAULT_SCHEMA_PATH


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _build_config_without_output(train_path: Path, valid_path: Path) -> dict:
    return {
        "version": "v1",
        "base_model": "Qwen/Qwen3-0.6B",
        "training_method": "lora",
        "dataset": {
            "train_path": str(train_path),
            "valid_path": str(valid_path),
            "dataset_version": "instruction_dataset.v1",
        },
        "hyperparams": {
            "learning_rate": 0.0002,
            "epochs": 1,
            "per_device_train_batch_size": 2,
            "gradient_accumulation_steps": 8,
            "seed": 42,
            "max_seq_len": 1024,
        },
        "lora": {
            "r": 8,
            "lora_alpha": 16,
            "lora_dropout": 0.05,
            "target_modules": ["q_proj", "v_proj"],
        },
    }


def test_init_training_run_creates_metadata_and_fingerprint(tmp_path) -> None:
    train_path = tmp_path / "instruction_dataset.v1.train.jsonl"
    valid_path = tmp_path / "instruction_dataset.v1.valid.jsonl"
    train_path.write_text('{"id":"train-1","text":"hello"}\n', encoding="utf-8")
    valid_path.write_text('{"id":"valid-1","text":"world"}\n', encoding="utf-8")

    runs_dir = tmp_path / "runs"
    config = {
        "version": "v1",
        "base_model": "Qwen/Qwen3-0.6B",
        "training_method": "lora",
        "dataset": {
            "train_path": str(train_path),
            "valid_path": str(valid_path),
            "dataset_version": "instruction_dataset.v1",
        },
        "hyperparams": {
            "learning_rate": 0.0002,
            "epochs": 1,
            "per_device_train_batch_size": 2,
            "gradient_accumulation_steps": 8,
            "seed": 42,
            "max_seq_len": 1024,
        },
        "lora": {
            "r": 8,
            "lora_alpha": 16,
            "lora_dropout": 0.05,
            "target_modules": ["q_proj", "v_proj"],
        },
        "output": {
            "runs_dir": str(runs_dir),
        },
    }
    config_path = tmp_path / "training_config.yaml"
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    result = init_training_run(config_path=config_path)

    assert re.fullmatch(r"run-\d{8}T\d{6}Z-[0-9a-f]{8}", result.run_id)
    assert result.run_dir.exists()
    assert result.run_meta_path.exists()
    assert result.summary_path.exists()
    assert (result.run_dir / "config.snapshot.yaml").exists()

    meta = json.loads(result.run_meta_path.read_text(encoding="utf-8"))
    assert meta["run_id"] == result.run_id
    assert meta["base_model"] == "Qwen/Qwen3-0.6B"
    assert meta["training_method"] == "lora"
    assert meta["dataset_fingerprint"]["dataset_version"] == "instruction_dataset.v1"
    assert meta["dataset_fingerprint"]["train"]["sha256"] == _sha256(train_path)
    assert meta["dataset_fingerprint"]["valid"]["sha256"] == _sha256(valid_path)


def test_init_training_run_without_output_uses_schema_default(tmp_path) -> None:
    train_path = tmp_path / "instruction_dataset.v1.train.jsonl"
    valid_path = tmp_path / "instruction_dataset.v1.valid.jsonl"
    train_path.write_text('{"id":"train-1","text":"hello"}\n', encoding="utf-8")
    valid_path.write_text('{"id":"valid-1","text":"world"}\n', encoding="utf-8")

    schema = json.loads(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    schema_default_runs_dir = str(tmp_path / "schema_default_runs")
    schema["$defs"]["output"]["properties"]["runs_dir"]["default"] = schema_default_runs_dir
    schema_path = tmp_path / "training_config.schema.v1.json"
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    config = _build_config_without_output(train_path, valid_path)
    config_path = tmp_path / "training_config_without_output.yaml"
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    result = init_training_run(config_path=config_path, schema_path=schema_path)

    meta = json.loads(result.run_meta_path.read_text(encoding="utf-8"))
    assert meta["output"]["runs_dir"] == schema_default_runs_dir
    assert result.run_dir.parent == Path(schema_default_runs_dir).resolve()
    assert meta["dataset_fingerprint"]["train"]["sha256"] == _sha256(train_path)


def test_init_training_run_is_cwd_independent(monkeypatch, tmp_path) -> None:
    train_path = tmp_path / "instruction_dataset.v1.train.jsonl"
    valid_path = tmp_path / "instruction_dataset.v1.valid.jsonl"
    train_path.write_text('{"id":"train-1","text":"hello"}\n', encoding="utf-8")
    valid_path.write_text('{"id":"valid-1","text":"world"}\n', encoding="utf-8")

    config = _build_config_without_output(train_path, valid_path)
    config_path = tmp_path / "training_config.yaml"
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(other_cwd)

    result = init_training_run(config_path=config_path.resolve())
    meta = json.loads(result.run_meta_path.read_text(encoding="utf-8"))

    assert result.run_dir.exists()
    assert result.run_meta_path.exists()
    assert meta["output"]["runs_dir"]


def test_init_training_run_without_schema_default_uses_fallback(tmp_path) -> None:
    train_path = tmp_path / "instruction_dataset.v1.train.jsonl"
    valid_path = tmp_path / "instruction_dataset.v1.valid.jsonl"
    train_path.write_text('{"id":"train-1","text":"hello"}\n', encoding="utf-8")
    valid_path.write_text('{"id":"valid-1","text":"world"}\n', encoding="utf-8")

    schema = json.loads(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    del schema["$defs"]["output"]["properties"]["runs_dir"]["default"]
    schema_path = tmp_path / "training_config.schema.v1.json"
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    config = _build_config_without_output(train_path, valid_path)
    config_path = tmp_path / "training_config_without_output.yaml"
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    result = init_training_run(config_path=config_path, schema_path=schema_path)

    meta = json.loads(result.run_meta_path.read_text(encoding="utf-8"))
    assert meta["output"]["runs_dir"] == DEFAULT_RUNS_DIR_FALLBACK
