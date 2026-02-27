"""Build T4 instruction dataset JSONL files from T3 chunk JSONL outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_PATH = BACKEND_ROOT / "datasets_local" / "exports" / "chunks"
DEFAULT_OUT_DIR = BACKEND_ROOT / "datasets_local" / "exports"
DEFAULT_STUDY_PACK_SCHEMA = (
    BACKEND_ROOT / "docs" / "ai" / "study_pack_v1" / "study_pack.schema.v1.json"
)
DATASET_VERSION = "instruction_dataset.v1"
PREFERENCE_DATASET_VERSION = "preference_dataset.v1"

PROMPT_COMPLETION = "prompt_completion"
CONVERSATIONAL = "conversational"
FORMAT_CHOICES = (PROMPT_COMPLETION, CONVERSATIONAL)

LOCATOR_KEYS = (
    "page_start",
    "page_end",
    "paragraph_start",
    "paragraph_end",
    "slide_start",
    "slide_end",
    "title",
    "heading",
)


@dataclass(slots=True)
class BuildResult:
    sft_records: list[dict[str, Any]]
    dpo_records: list[dict[str, Any]]
    split_counts: dict[str, int]
    input_count: int


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_split_spec(split_spec: str) -> list[tuple[str, float]]:
    chunks = [part.strip() for part in split_spec.split(",") if part.strip()]
    if not chunks:
        raise ValueError("split spec is empty; expected format like train:0.9,valid:0.1")

    parsed: list[tuple[str, float]] = []
    total = 0.0
    for chunk in chunks:
        if ":" not in chunk:
            raise ValueError(f"invalid split spec chunk: {chunk}")
        split_name, ratio_text = chunk.split(":", 1)
        split_name = split_name.strip()
        if not split_name:
            raise ValueError(f"split name cannot be empty: {chunk}")
        try:
            ratio = float(ratio_text.strip())
        except ValueError as exc:
            raise ValueError(f"split ratio must be numeric: {chunk}") from exc
        if ratio <= 0:
            raise ValueError(f"split ratio must be > 0: {chunk}")
        parsed.append((split_name, ratio))
        total += ratio

    if total <= 0:
        raise ValueError("split ratios sum to zero")

    return [(split_name, ratio / total) for split_name, ratio in parsed]


def assign_split(record_id: str, splits: list[tuple[str, float]], seed: int) -> str:
    digest = hashlib.sha256(f"{seed}:{record_id}".encode("utf-8")).digest()
    sample = int.from_bytes(digest[:8], byteorder="big", signed=False) / float(1 << 64)

    threshold = 0.0
    for split_name, ratio in splits:
        threshold += ratio
        if sample < threshold:
            return split_name
    return splits[-1][0]


def discover_chunk_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path.resolve()]
    if not input_path.exists():
        raise FileNotFoundError(f"input path does not exist: {input_path}")
    if not input_path.is_dir():
        raise ValueError(f"input path must be file or directory: {input_path}")

    preferred = sorted(path.resolve() for path in input_path.rglob("*.chunks.v1.jsonl"))
    if preferred:
        return preferred

    fallback = sorted(path.resolve() for path in input_path.rglob("*.jsonl"))
    if fallback:
        return fallback
    raise ValueError(f"no JSONL files found under: {input_path}")


def _normalize_chunk_record(
    raw_record: dict[str, Any], *, source_file: Path, line_number: int
) -> dict[str, Any]:
    chunk_id = raw_record.get("chunk_id")
    if not isinstance(chunk_id, str) or not chunk_id.strip():
        raise ValueError(f"{source_file}:{line_number} missing valid chunk_id")

    text = raw_record.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{source_file}:{line_number} missing valid text")

    meta = raw_record.get("meta")
    if not isinstance(meta, dict):
        raise ValueError(f"{source_file}:{line_number} missing valid meta object")

    return {"chunk_id": chunk_id.strip(), "text": text, "meta": meta}


def load_chunks_from_jsonl(input_path: Path) -> tuple[list[dict[str, Any]], list[Path]]:
    files = discover_chunk_files(input_path)
    records: list[dict[str, Any]] = []
    for file_path in files:
        with file_path.open("r", encoding="utf-8") as file_obj:
            for line_number, line in enumerate(file_obj, start=1):
                payload = line.strip()
                if not payload:
                    continue
                try:
                    raw_record = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON at {file_path}:{line_number}: {exc}") from exc
                if not isinstance(raw_record, dict):
                    raise ValueError(f"{file_path}:{line_number} must be a JSON object")
                records.append(
                    _normalize_chunk_record(
                        raw_record,
                        source_file=file_path,
                        line_number=line_number,
                    )
                )
    if not records:
        raise ValueError(f"no chunk records loaded from {input_path}")
    return records, files


def load_chunks_from_db(_input_value: str) -> tuple[list[dict[str, Any]], list[Path]]:
    raise NotImplementedError(
        "input-format=db is not implemented yet. "
        "Please export chunk JSONL first and use --input-format jsonl."
    )


def trim_context(text: str, max_context_chars: int) -> str:
    if max_context_chars <= 0:
        raise ValueError("max_context_chars must be > 0")
    content = text.strip()
    if len(content) <= max_context_chars:
        return content
    return content[:max_context_chars].rstrip()


def _compact_text(text: str) -> str:
    return " ".join(part for part in text.split())


def _short_key_point(text: str, max_chars: int = 280) -> str:
    compact = _compact_text(text)
    if not compact:
        return "重點整理"
    if len(compact) <= max_chars:
        return compact
    cropped = compact[:max_chars].rstrip()
    boundary = cropped.rfind(" ")
    if boundary >= max_chars // 3:
        cropped = cropped[:boundary].rstrip()
    return cropped or compact[:max_chars]


def build_study_pack_output(context: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "language": "zh-TW",
        "outline": [],
        "key_points": [_short_key_point(context)],
        "glossary": [],
        "quiz": [],
        "story_nodes": [],
    }


def build_locator(meta: dict[str, Any]) -> dict[str, Any]:
    locator: dict[str, Any] = {}
    for key in LOCATOR_KEYS:
        if key in meta:
            locator[key] = meta[key]

    nested_locator = meta.get("locator")
    if isinstance(nested_locator, dict):
        for key, value in nested_locator.items():
            locator.setdefault(key, value)
    return locator


def infer_doc_id(meta: dict[str, Any]) -> str:
    existing = meta.get("doc_id")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()

    source_relative_path = str(meta.get("source_relative_path", "")).strip()
    sha256 = str(meta.get("sha256", "")).strip()
    dataset_id = str(meta.get("dataset_id", "")).strip()
    basis = "|".join([source_relative_path, sha256, dataset_id])
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    return f"doc-{digest}"


def build_prompts(
    *,
    chunk_id: str,
    source_relative_path: str,
    locator: dict[str, Any],
    context: str,
    schema_path: Path,
) -> tuple[str, str]:
    system_prompt = (
        "You are a Studydy dataset annotation assistant. "
        "Return exactly one JSON object that must satisfy Study Pack schema v1. "
        "Do not output markdown, code fences, or extra commentary. "
        f"Schema reference: {schema_path}."
    )
    user_prompt = (
        f"chunk_id: {chunk_id}\n"
        f"source_relative_path: {source_relative_path}\n"
        f"locator: {json.dumps(locator, ensure_ascii=False, sort_keys=True)}\n\n"
        "Generate a Study Pack JSON object from the following context:\n"
        f"{context}"
    )
    return system_prompt, user_prompt


def _build_prompt_text(system_prompt: str, user_prompt: str) -> str:
    return f"[SYSTEM]\n{system_prompt}\n\n[USER]\n{user_prompt}"


def build_sft_record(
    chunk: dict[str, Any],
    *,
    split: str,
    format_name: str,
    max_context_chars: int,
    schema_path: Path,
) -> dict[str, Any]:
    chunk_id = str(chunk["chunk_id"])
    meta = dict(chunk["meta"])
    context = trim_context(str(chunk["text"]), max_context_chars=max_context_chars)
    locator = build_locator(meta)
    source_relative_path = str(meta.get("source_relative_path", ""))

    source = {
        "doc_id": infer_doc_id(meta),
        "dataset_id": meta.get("dataset_id"),
        "chunk_id": chunk_id,
        "source_relative_path": source_relative_path,
        "sha256": meta.get("sha256"),
        "locator": locator,
    }
    output_json = build_study_pack_output(context)
    system_prompt, user_prompt = build_prompts(
        chunk_id=chunk_id,
        source_relative_path=source_relative_path,
        locator=locator,
        context=context,
        schema_path=schema_path,
    )

    record: dict[str, Any] = {
        "id": f"ins-{chunk_id}",
        "dataset_version": DATASET_VERSION,
        "split": split,
        "chunk_id": chunk_id,
        "source": source,
        "meta": meta,
        "output_json": output_json,
    }

    if format_name == PROMPT_COMPLETION:
        record["prompt"] = _build_prompt_text(system_prompt, user_prompt)
        record["completion"] = json.dumps(output_json, ensure_ascii=False)
    else:
        record["messages"] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    return record


def build_dpo_record(sft_record: dict[str, Any]) -> dict[str, Any]:
    prompt = sft_record.get("prompt")
    if not isinstance(prompt, str):
        messages = sft_record.get("messages", [])
        prompt = json.dumps(messages, ensure_ascii=False)

    chosen_json = json.dumps(sft_record["output_json"], ensure_ascii=False)
    rejected_json = json.dumps({"schema_version": "1.0", "outline": []}, ensure_ascii=False)
    return {
        "id": f"dpo-{sft_record['chunk_id']}",
        "dataset_version": PREFERENCE_DATASET_VERSION,
        "split": sft_record["split"],
        "prompt": prompt,
        "chosen": chosen_json,
        "rejected": rejected_json,
        "source": sft_record["source"],
        "chunk_id": sft_record["chunk_id"],
    }


def build_instruction_records(
    *,
    chunks: list[dict[str, Any]],
    split_spec: str,
    seed: int,
    max_context_chars: int,
    format_name: str,
    schema_path: Path,
    with_dpo: bool,
) -> BuildResult:
    if format_name not in FORMAT_CHOICES:
        raise ValueError(f"unsupported format: {format_name}")

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    split_definitions = parse_split_spec(split_spec)

    sft_records: list[dict[str, Any]] = []
    dpo_records: list[dict[str, Any]] = []
    split_counts: dict[str, int] = {}

    for chunk in sorted(chunks, key=lambda item: str(item["chunk_id"])):
        record_id = f"ins-{chunk['chunk_id']}"
        split = assign_split(record_id=record_id, splits=split_definitions, seed=seed)
        sft_record = build_sft_record(
            chunk,
            split=split,
            format_name=format_name,
            max_context_chars=max_context_chars,
            schema_path=schema_path,
        )

        try:
            validator.validate(sft_record["output_json"])
        except jsonschema.ValidationError as exc:
            raise ValueError(f"{chunk['chunk_id']} generated invalid output_json: {exc.message}") from exc

        sft_records.append(sft_record)
        split_counts[split] = split_counts.get(split, 0) + 1
        if with_dpo:
            dpo_records.append(build_dpo_record(sft_record))

    return BuildResult(
        sft_records=sft_records,
        dpo_records=dpo_records,
        split_counts=split_counts,
        input_count=len(chunks),
    )


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file_obj:
        for record in records:
            file_obj.write(json.dumps(record, ensure_ascii=False))
            file_obj.write("\n")


def write_split_jsonl(
    *,
    out_dir: Path,
    records: list[dict[str, Any]],
    dataset_version: str,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        split = str(record["split"])
        grouped.setdefault(split, []).append(record)

    for split_name in sorted(grouped):
        items = sorted(grouped[split_name], key=lambda item: str(item["id"]))
        output_path = out_dir / f"{dataset_version}.{split_name}.jsonl"
        _write_jsonl(output_path, items)
        paths.append(output_path)

    return paths


def write_build_report(
    *,
    out_dir: Path,
    args: argparse.Namespace,
    result: BuildResult,
    written_files: list[Path],
    input_files: list[Path],
) -> Path:
    payload = {
        "version": "v1",
        "built_at": utc_now_iso(),
        "config": {
            "input": args.input,
            "input_format": args.input_format,
            "study_pack_schema": str(args.study_pack_schema),
            "split": args.split,
            "seed": args.seed,
            "max_context_chars": args.max_context_chars,
            "format": args.format,
            "with_dpo": args.with_dpo,
            "out_dir": str(args.out_dir),
        },
        "input_files": [str(path) for path in input_files],
        "input_records": result.input_count,
        "sft_records": len(result.sft_records),
        "dpo_records": len(result.dpo_records),
        "split_counts": result.split_counts,
        "output_files": [str(path) for path in written_files],
    }
    report_path = out_dir / "instruction_dataset.build_report.v1.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build T4 instruction dataset from T3 chunks.")
    parser.add_argument(
        "--input",
        type=str,
        default=str(DEFAULT_INPUT_PATH),
        help=f"Chunk input path (file or dir) or db DSN (default: {DEFAULT_INPUT_PATH})",
    )
    parser.add_argument(
        "--input-format",
        type=str,
        choices=("jsonl", "db"),
        default="jsonl",
        help="Input type for chunk records (default: jsonl)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory for instruction dataset JSONL files (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--study-pack-schema",
        type=Path,
        default=DEFAULT_STUDY_PACK_SCHEMA,
        help=f"Study Pack schema path (default: {DEFAULT_STUDY_PACK_SCHEMA})",
    )
    parser.add_argument(
        "--split",
        type=str,
        default="train:0.9,valid:0.1",
        help="Split ratios, e.g. train:0.9,valid:0.1",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic seed for split assignment (default: 42)",
    )
    parser.add_argument(
        "--max-context-chars",
        type=int,
        default=8000,
        help="Max chars kept in prompt context per sample (default: 8000)",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=FORMAT_CHOICES,
        default=PROMPT_COMPLETION,
        help=f"SFT format (default: {PROMPT_COMPLETION})",
    )
    parser.add_argument(
        "--with-dpo",
        action="store_true",
        help="Also write preference dataset JSONL files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.input_format == "jsonl":
            chunks, input_files = load_chunks_from_jsonl(Path(args.input))
        else:
            chunks, input_files = load_chunks_from_db(args.input)

        result = build_instruction_records(
            chunks=chunks,
            split_spec=args.split,
            seed=args.seed,
            max_context_chars=args.max_context_chars,
            format_name=args.format,
            schema_path=args.study_pack_schema,
            with_dpo=args.with_dpo,
        )

        written_files = write_split_jsonl(
            out_dir=args.out_dir,
            records=result.sft_records,
            dataset_version=DATASET_VERSION,
        )
        if args.with_dpo:
            written_files.extend(
                write_split_jsonl(
                    out_dir=args.out_dir,
                    records=result.dpo_records,
                    dataset_version=PREFERENCE_DATASET_VERSION,
                )
            )

        report_path = write_build_report(
            out_dir=args.out_dir,
            args=args,
            result=result,
            written_files=written_files,
            input_files=input_files,
        )
    except Exception as exc:
        print(f"build_instruction_dataset failed: {exc}", file=sys.stderr)
        return 1

    print(
        "Built instruction dataset: "
        f"input_records={result.input_count}, "
        f"sft_records={len(result.sft_records)}, "
        f"dpo_records={len(result.dpo_records)}, "
        f"out_dir={args.out_dir.resolve()}, "
        f"report={report_path.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
