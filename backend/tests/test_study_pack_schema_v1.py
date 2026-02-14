import json
from pathlib import Path

import jsonschema


BASE = Path(__file__).resolve().parents[1]  # backend/
SCHEMA_PATH = BASE / "docs" / "ai" / "study_pack_v1" / "study_pack.schema.v1.json"
SAMPLES_DIR = BASE / "docs" / "ai" / "study_pack_v1" / "golden_samples"


def _load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def test_schema_is_valid_draft_2020_12():
    schema = _load_json(SCHEMA_PATH)
    jsonschema.Draft202012Validator.check_schema(schema)


def test_golden_samples_validate_against_schema():
    schema = _load_json(SCHEMA_PATH)
    validator = jsonschema.Draft202012Validator(schema)

    for name in ["minimal_valid.json", "typical.json", "edge_case.json"]:
        instance = _load_json(SAMPLES_DIR / name)
        validator.validate(instance)
