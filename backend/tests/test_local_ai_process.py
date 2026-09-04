import sys
from pathlib import Path
import shutil

import pytest

import learning_adaptation.assessment_verifier as assessment_verifier_module
import pdf_evidence.local_ai_process as process_module
from learning_adaptation.assessment_verifier import start_assessment_process
from pdf_evidence.local_ai_process import (
    LocalAIError,
    LocalAIProcess,
    start_equivalence_process,
)


def _verifier_child_settings(tmp_path: Path) -> dict[str, str]:
    site_packages = tmp_path / "site-packages"
    source_package = Path(__file__).parents[2] / "local_ai/src/studydy_local_ai"
    shutil.copytree(source_package, site_packages / "studydy_local_ai")
    return {
        "python_executable": sys.executable,
        "site_packages": str(site_packages),
        "verifier_model_root": str(tmp_path / "model"),
    }


def test_bounded_ndjson_pipe_and_clean_exit():
    code = "import sys,json; value=json.loads(sys.stdin.buffer.readline()); print(json.dumps({'request_id':value['request_id']}),flush=True)"
    child = LocalAIProcess([sys.executable, "-c", code], request_limit=100, response_limit=100)
    assert child.request({"request_id": "public"}, None) == {"request_id": "public"}
    child.close()
    child.close()


def test_real_child_timeout_is_not_masked_and_cleanup_is_idempotent():
    child = LocalAIProcess(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        request_limit=2_000_000,
        response_limit=100,
    )
    with pytest.raises(LocalAIError, match="CHILD_TIMEOUT"):
        child.request({"render": "x" * 1_000_000}, 0.01)
    child.close()
    child.close()


def test_real_nonzero_child_remains_child_exited_and_close_is_idempotent():
    child = LocalAIProcess(
        [sys.executable, "-c", "raise SystemExit(7)"],
        request_limit=100,
        response_limit=100,
    )
    with pytest.raises(LocalAIError, match="CHILD_EXITED"):
        child.close()
    child.close()


def test_child_exit_request_limit_and_stderr_are_not_exposed():
    child = LocalAIProcess(
        [sys.executable, "-c", "raise SystemExit(1)"],
        request_limit=10,
        response_limit=10,
    )
    with pytest.raises(LocalAIError):
        child.request({"too_long": "public"}, 2)
    child.abort()

    code = "import sys,json; value=json.loads(sys.stdin.buffer.readline()); print(json.dumps(value),flush=True); print('diagnostic',file=sys.stderr,flush=True)"
    child = LocalAIProcess([sys.executable, "-c", code], request_limit=100, response_limit=100)
    assert child.request({"request_id": "public"}, 2) == {"request_id": "public"}
    child.close()


@pytest.mark.parametrize(
    ("torch_source", "transformers_source", "expected_reason"),
    [
        (
            "raise ImportError('private dependency diagnostic')\n",
            "",
            "CONCEPT_EQUIVALENCE_VERIFIER_DEPENDENCY_MISSING",
        ),
        (
            "class cuda:\n    @staticmethod\n    def is_available(): return False\n",
            "class AutoConfig: pass\nclass AutoModelForSequenceClassification: pass\nclass AutoTokenizer: pass\n",
            "CONCEPT_EQUIVALENCE_VERIFIER_CUDA_UNAVAILABLE",
        ),
    ],
)
def test_real_equivalence_child_distinguishes_startup_failures(
    tmp_path, torch_source, transformers_source, expected_reason
):
    settings = _verifier_child_settings(tmp_path)
    site_packages = Path(settings["site_packages"])
    (site_packages / "torch.py").write_text(torch_source, encoding="utf-8")
    if transformers_source:
        (site_packages / "transformers.py").write_text(
            transformers_source, encoding="utf-8"
        )

    with pytest.raises(LocalAIError) as failure:
        start_equivalence_process(settings, 2)

    assert failure.value.reason_code == expected_reason


@pytest.mark.parametrize(
    ("torch_source", "transformers_source", "expected_reason"),
    [
        (
            "raise ImportError('private dependency diagnostic')\n",
            "",
            "ASSESSMENT_VERIFIER_DEPENDENCY_MISSING",
        ),
        (
            "class cuda:\n    @staticmethod\n    def is_available(): return False\n",
            "class AutoConfig: pass\nclass AutoModelForSequenceClassification: pass\nclass AutoTokenizer: pass\n",
            "ASSESSMENT_VERIFIER_CUDA_UNAVAILABLE",
        ),
        (
            "class cuda:\n    @staticmethod\n    def is_available(): return True\n",
            "class Broken:\n    @staticmethod\n    def from_pretrained(*args, **kwargs): raise OSError('private model diagnostic')\nAutoConfig = AutoModelForSequenceClassification = AutoTokenizer = Broken\n",
            "ASSESSMENT_VERIFIER_MODEL_LOAD_FAILED",
        ),
    ],
)
def test_real_assessment_child_distinguishes_startup_failures(
    tmp_path, torch_source, transformers_source, expected_reason
):
    settings = _verifier_child_settings(tmp_path)
    site_packages = Path(settings["site_packages"])
    (site_packages / "torch.py").write_text(torch_source, encoding="utf-8")
    if transformers_source:
        (site_packages / "transformers.py").write_text(
            transformers_source, encoding="utf-8"
        )

    with pytest.raises(LocalAIError) as failure:
        start_assessment_process(settings, 2)

    assert failure.value.reason_code == expected_reason


@pytest.mark.parametrize(
    ("bootstrap", "startup_timeout", "expected_reason"),
    [
        (
            "import json;print(json.dumps({'schema':'wrong'}),flush=True)",
            2,
            "CONCEPT_EQUIVALENCE_VERIFIER_RESPONSE_INVALID",
        ),
        (
            "import time;time.sleep(10)",
            0.02,
            "CONCEPT_EQUIVALENCE_VERIFIER_TIMEOUT",
        ),
    ],
)
def test_equivalence_startup_invalid_response_and_timeout_fail_closed(
    tmp_path, monkeypatch, bootstrap, startup_timeout, expected_reason
):
    monkeypatch.setattr(process_module, "_EQUIVALENCE_BOOTSTRAP", bootstrap)
    with pytest.raises(LocalAIError) as failure:
        start_equivalence_process(
            _verifier_child_settings(tmp_path), startup_timeout
        )

    assert failure.value.reason_code == expected_reason


@pytest.mark.parametrize(
    ("bootstrap", "startup_timeout", "expected_reason"),
    [
        (
            "import json;print(json.dumps({'schema':'wrong'}),flush=True)",
            2,
            "ASSESSMENT_VERIFIER_RESPONSE_INVALID",
        ),
        (
            "import time;time.sleep(10)",
            0.02,
            "ASSESSMENT_VERIFIER_TIMEOUT",
        ),
    ],
)
def test_assessment_startup_invalid_response_and_timeout_fail_closed(
    tmp_path, monkeypatch, bootstrap, startup_timeout, expected_reason
):
    monkeypatch.setattr(
        assessment_verifier_module, "_ASSESSMENT_BOOTSTRAP", bootstrap
    )
    with pytest.raises(LocalAIError) as failure:
        start_assessment_process(
            _verifier_child_settings(tmp_path), startup_timeout
        )
    assert failure.value.reason_code == expected_reason
