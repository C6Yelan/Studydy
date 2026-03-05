# backend/tests/scripts/test_validate_training_config.py
from __future__ import annotations

from pathlib import Path

import yaml

from scripts.training.validate_training_config import (
    DEFAULT_SCHEMA_PATH,
    validate_training_config_file,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG_PATH = BACKEND_ROOT / "docs" / "ai" / "training" / "training_config.example.v1.yaml"


def test_validate_training_config_example_pass() -> None:
    result = validate_training_config_file(
        config_path=EXAMPLE_CONFIG_PATH,
        schema_path=DEFAULT_SCHEMA_PATH,
    )

    assert result.is_valid
    assert result.errors == []
    assert result.config is not None
    assert result.config["base_model"] == "Qwen/Qwen3-0.6B"


def test_validate_training_config_without_output_pass(tmp_path) -> None:
    config_without_output = {
        "version": "v1",
        "base_model": "Qwen/Qwen3-0.6B",
        "training_method": "lora",
        "dataset": {
            "train_path": "backend/datasets_local/exports/instruction_dataset.v1.train.jsonl",
            "valid_path": "backend/datasets_local/exports/instruction_dataset.v1.valid.jsonl",
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
    config_path = tmp_path / "training_config_without_output.yaml"
    config_path.write_text(
        yaml.safe_dump(config_without_output, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    result = validate_training_config_file(
        config_path=config_path,
        schema_path=DEFAULT_SCHEMA_PATH,
    )

    assert result.is_valid
    assert result.errors == []


def test_validate_training_config_missing_required_field_fail(tmp_path) -> None:
    invalid_config = {
        "version": "v1",
        "training_method": "lora",
        "dataset": {
            "train_path": "backend/datasets_local/exports/instruction_dataset.v1.train.jsonl",
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
        },
        "output": {
            "runs_dir": "backend/datasets_local/training/runs",
        },
    }
    config_path = tmp_path / "invalid_training_config.yaml"
    config_path.write_text(
        yaml.safe_dump(invalid_config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    result = validate_training_config_file(
        config_path=config_path,
        schema_path=DEFAULT_SCHEMA_PATH,
    )

    assert not result.is_valid
    assert len(result.errors) >= 1
    assert any("base_model" in error for error in result.errors)
