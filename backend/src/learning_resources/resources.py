from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unicodedata
from typing import Any


_LIBRARY = Path(__file__).with_name("data") / "resource_library_v1.json"


def normalized_label(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def load_resource_index(path: Path = _LIBRARY) -> dict[str, list[dict[str, Any]]]:
    """只以人工審核 library 的 exact normalized label 提供補充資源。"""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if (
            document.get("schema") != "resource-library/v1"
            or document.get("processing") != "succeeded"
            or document.get("decision") != "retain"
        ):
            raise ValueError
        sources = {source["resource_id"]: source for source in document["sources"]}
        evidence = {item["evidence_id"]: item for item in document["evidence"]}
        index: dict[str, list[dict[str, Any]]] = {}
        for concept in document["concepts"]:
            if concept.get("decision") != "retain" or not concept.get("evidence_ids"):
                continue
            items = [evidence[evidence_id] for evidence_id in concept["evidence_ids"]]
            resource_ids = list(dict.fromkeys(item["resource_id"] for item in items))
            resources = []
            for resource_id in resource_ids:
                source = sources[resource_id]
                pages = sorted({item["page_number"] for item in items if item["resource_id"] == resource_id})
                resources.append({
                    "resource_id": resource_id,
                    "title": source["title"],
                    "authors": deepcopy(source["authors"]),
                    "citation": source["citation"],
                    "license": source["license"],
                    "license_url": source["license_url"],
                    "source_url": source["source_url"],
                    "pages": pages,
                })
            index.setdefault(normalized_label(concept["label"]), []).extend(resources)
        return index
    except (KeyError, OSError, TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        return {}
