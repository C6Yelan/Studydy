#!/usr/bin/env python3
"""本機 Luna 呼叫報表；只讀識別與計數紀錄，不讀私人失敗內容。"""

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path

from luna_call_log import call_records


def report(path, *, date=None, material=None, feature=None, work=None):
    groups = defaultdict(lambda: {"calls": 0, "failures": 0, "blocked": 0, "interrupted": 0, "elapsed_seconds": 0.0})
    for row in call_records(path):
        tags = row.get("tags") or {}
        key = (row["at"][:10], tags.get("material_id"), tags.get("feature"), tags.get("work_id"))
        if any(want is not None and want != actual for want, actual in zip((date, material, feature, work), key)):
            continue
        group = groups[key]
        if row["event"] == "blocked":
            group["blocked"] += 1
        else:
            group["calls"] += 1
            group["failures"] += row["outcome"] in {"failed", "cancelled"}
            group["interrupted"] += row["outcome"] == "interrupted"
            group["elapsed_seconds"] += row["elapsed_seconds"]
    return [{"date": key[0], "material_id": key[1], "feature": key[2], "work_id": key[3],
             **value, "elapsed_seconds": round(value["elapsed_seconds"], 6)}
            for key, value in sorted(groups.items(), key=lambda item: tuple(str(part) for part in item[0]))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default=os.environ.get("STUDYDY_LUNA_CALL_LOG", str(Path(__file__).resolve().parents[2] / "data/podcast/luna-calls.jsonl")))
    parser.add_argument("--date")
    parser.add_argument("--material")
    parser.add_argument("--feature")
    parser.add_argument("--work")
    options = parser.parse_args()
    print(json.dumps(report(options.log, date=options.date, material=options.material, feature=options.feature, work=options.work), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
