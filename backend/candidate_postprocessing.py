from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from typing import Any


def postprocess_candidates(
    extraction_result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """將原始候選彙整為保留證據順序的穩定詞頻列。"""
    if not isinstance(extraction_result, Mapping):
        raise TypeError("extraction_result must be a mapping")
    candidates = extraction_result.get("candidates")
    if not isinstance(candidates, list):
        raise TypeError("candidates must be a list")

    rows_by_word: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise TypeError("candidate must be a mapping")
        word = candidate.get("text")
        if not isinstance(word, str):
            raise TypeError("candidate text must be a string")
        evidence_ref = candidate.get("evidence_ref")
        if not isinstance(evidence_ref, Mapping):
            raise TypeError("candidate evidence_ref must be a mapping")

        # 只排除可明確判定的格式雜訊，含文字或數字的候選仍保留。
        if word.isspace() or all(
            unicodedata.category(character)[0] in {"P", "S"} for character in word
        ):
            continue

        row = rows_by_word.get(word)
        if row is None:
            row = {"word": word, "occurrence_count": 0, "evidence_refs": []}
            rows_by_word[word] = row
        row["occurrence_count"] += 1
        # 每次出現都保留原始 evidence_ref，避免聚合後失去來源。
        row["evidence_refs"].append(evidence_ref)

    # 次數相同時依文字固定排序，確保相同輸入得到一致結果。
    return sorted(
        rows_by_word.values(),
        key=lambda row: (-row["occurrence_count"], row["word"]),
    )
