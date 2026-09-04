from contextlib import contextmanager
import json
from pathlib import Path

import pytest

import runtime.local_runtime as local_runtime
from pdf_evidence.local_ai_process import LocalAIError
from runtime.material_processing import MaterialProcessingError


def _config(tmp_path: Path) -> dict:
    return {
        "private_runtime_root": str(tmp_path / "runtime"),
        "runtime_lock": {},
    }


def _stub_contracts(monkeypatch: pytest.MonkeyPatch, observed: list[str]) -> None:
    monkeypatch.setattr(
        local_runtime,
        "formal_runtime_preflight",
        lambda _: observed.append("preflight"),
    )
    monkeypatch.setattr(
        local_runtime,
        "load_assessment_runtime_lock",
        lambda: {"verifier": {"startup_timeout_seconds": 120}},
    )
    monkeypatch.setattr(
        local_runtime,
        "assessment_runtime_binding",
        lambda *_: observed.append("assessment_binding"),
    )

    @contextmanager
    def model_lock(root):
        observed.append(f"lock:{root.name}")
        yield

    monkeypatch.setattr(local_runtime, "material_analysis_lock", model_lock)


def test_verify_uses_existing_preflight_and_production_model_loaders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    observed: list[str] = []
    _stub_contracts(monkeypatch, observed)

    class Process:
        def __init__(self, name: str):
            self.name = name

        def close(self):
            observed.append(f"close:{self.name}")

    monkeypatch.setattr(
        local_runtime,
        "start_ocr_process",
        lambda _: observed.append("start:ocr") or Process("ocr"),
    )
    monkeypatch.setattr(
        local_runtime,
        "start_assessment_process",
        lambda _, timeout: observed.append(f"start:assessment:{timeout}")
        or Process("assessment"),
    )

    assert local_runtime.verify_local_runtime(_config(tmp_path)) == {
        "status": "succeeded",
        "command": "verify",
    }
    assert observed == [
        "preflight",
        "lock:runtime",
        "assessment_binding",
        "start:ocr",
        "close:ocr",
        "start:assessment:120",
        "close:assessment",
    ]


@pytest.mark.parametrize(
    ("failed_loader", "component"),
    [
        ("ocr", "ocr_model"),
        ("assessment", "verifier_model"),
    ],
)
def test_model_load_smoke_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_loader: str,
    component: str,
):
    observed: list[str] = []
    _stub_contracts(monkeypatch, observed)

    class Process:
        def __init__(self, name: str):
            self.name = name

        def close(self):
            if self.name == failed_loader:
                raise LocalAIError("private diagnostic")

    monkeypatch.setattr(local_runtime, "start_ocr_process", lambda _: Process("ocr"))
    monkeypatch.setattr(
        local_runtime,
        "start_assessment_process",
        lambda *_: Process("assessment"),
    )

    with pytest.raises(MaterialProcessingError) as failure:
        local_runtime.verify_local_runtime(_config(tmp_path))

    assert failure.value.component == component
    assert failure.value.reason == "LOCAL_RUNTIME_SMOKE_FAILED"
    assert "private" not in str(failure.value)


def test_cli_failure_is_fixed_safe_json(capsys, monkeypatch):
    monkeypatch.setattr(
        local_runtime,
        "read_local_ai_config_from_environment",
        lambda _: {},
    )
    monkeypatch.setattr(
        local_runtime,
        "verify_local_runtime",
        lambda _: (_ for _ in ()).throw(
            MaterialProcessingError(
                "private/path/diagnostic",
                component="ocr_model",
                reason="LOCAL_RUNTIME_SMOKE_FAILED",
            )
        ),
    )

    assert local_runtime.main(["verify"], {}) == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed",
        "command": "verify",
        "component": "ocr_model",
        "reason": "LOCAL_RUNTIME_SMOKE_FAILED",
    }


def test_cli_rejects_removed_sync_commands_without_echo(capsys):
    assert local_runtime.main(["sync", "/private/target"], {}) == 1
    failure = json.loads(capsys.readouterr().out)
    assert failure == {
        "status": "failed",
        "command": "verify",
        "component": "layout",
        "reason": "LOCAL_RUNTIME_SETTINGS_MISMATCH",
    }
    assert "/private/target" not in json.dumps(failure)
