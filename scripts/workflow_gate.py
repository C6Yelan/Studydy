#!/usr/bin/env python3

import argparse
import json
from collections import Counter
from pathlib import Path


STATUSES = ("pass", "fail", "critical_regression")
EXIT_CODES = {"pass": 0, "fail": 1, "critical_regression": 2}


def evaluate_result(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    case_id = payload.get("case_id")
    checks = payload.get("checks")

    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("case_id must be a non-empty string")
    if not isinstance(checks, list) or not checks:
        raise ValueError("checks must be a non-empty list")

    statuses: list[str] = []
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise ValueError(f"checks[{index}] must be an object")
        check_id = check.get("check_id")
        status = check.get("status")
        if not isinstance(check_id, str) or not check_id.strip():
            raise ValueError(f"checks[{index}].check_id must be a non-empty string")
        if status not in STATUSES:
            raise ValueError(f"checks[{index}].status must be one of: {', '.join(STATUSES)}")
        statuses.append(status)

    if "critical_regression" in statuses:
        conclusion = "critical_regression"
    elif "fail" in statuses:
        conclusion = "fail"
    else:
        conclusion = "pass"

    counts = Counter(statuses)
    return {
        "case_id": case_id,
        "status": conclusion,
        "counts": {status: counts[status] for status in STATUSES},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a thin Studydy S0 result file.")
    parser.add_argument("result_file", type=Path)
    args = parser.parse_args()

    try:
        result = evaluate_result(args.result_file)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return EXIT_CODES[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
