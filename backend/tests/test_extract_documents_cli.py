import json
import subprocess
import sys
from pathlib import Path

from docx import Document


BASE = Path(__file__).resolve().parents[1]
SCRIPT = BASE / "scripts" / "datasets" / "extract_documents.py"


def _build_docx(path: Path) -> None:
    document = Document()
    document.add_paragraph("CLI smoke paragraph")
    document.save(path)


def test_extract_documents_cli_smoke(tmp_path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir(parents=True, exist_ok=True)

    docx_path = input_dir / "cli-smoke.docx"
    _build_docx(docx_path)

    run_id = "test-run-cli-smoke"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(input_dir),
            "--output",
            str(output_dir),
            "--run-id",
            run_id,
        ],
        cwd=BASE,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    report_path = output_dir / "reports" / f"{run_id}.json"
    assert report_path.exists()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["run_id"] == run_id
    assert report["totals"]["discovered_documents"] == 1
    assert report["totals"]["processed_documents"] == 1
    assert report["totals"]["succeeded"] == 1
    assert report["totals"]["failed"] == 0

    doc_uid = report["documents"][0]["doc_uid"]
    document_dir = output_dir / "runs" / run_id / "documents" / doc_uid
    raw_path = document_dir / "raw.jsonl"
    meta_path = document_dir / "meta.json"
    log_path = document_dir / "extract.log"

    assert raw_path.exists()
    assert meta_path.exists()
    assert log_path.exists()

    raw_lines = [line for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(raw_lines) == 1

    raw_record = json.loads(raw_lines[0])
    assert raw_record["file_type"] == "docx"
    assert raw_record["unit_type"] == "paragraph"
    assert raw_record["unit_index"] == 1
    assert raw_record["text"] == "CLI smoke paragraph"

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["run_id"] == run_id
    assert meta["stats"]["segments"] == 1
    assert meta["stats"]["non_empty_segments"] == 1
    assert meta["stats"]["empty_segments"] == 0
