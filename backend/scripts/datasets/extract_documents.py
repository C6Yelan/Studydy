"""CLI entrypoint for T2 document extraction pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.extraction.pipeline import DEFAULT_OUTPUT_DIR, run_extraction_batch


DEFAULT_INPUT_DIR = BACKEND_ROOT / "datasets_local" / "redacted"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract PDF/DOCX/PPTX into raw segment JSONL + metadata + report."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"Input file or directory (default: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Optional dataset manifest path; when provided, files are loaded from manifest entries.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output root path (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Optional run id. If omitted, a UTC-based run id is generated.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop processing after first failed document.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        report = run_extraction_batch(
            input_path=args.input,
            output_path=args.output,
            manifest_path=args.manifest,
            run_id=args.run_id,
            fail_fast=args.fail_fast,
        )
    except Exception as exc:
        print(f"Extraction failed before completion: {exc}", file=sys.stderr)
        return 2

    totals = report.get("totals", {})
    failed = int(totals.get("failed", 0))
    succeeded = int(totals.get("succeeded", 0))
    warnings = int(totals.get("warnings", 0))

    print(
        json.dumps(
            {
                "run_id": report.get("run_id"),
                "succeeded": succeeded,
                "failed": failed,
                "warnings": warnings,
                "report_path": report.get("report_path"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
