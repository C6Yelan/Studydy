from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from material_normalized_blocks import (
    NORMALIZED_BLOCKS_STABLE_PATH,
    SCHEMA_VERSION as NORMALIZED_BLOCKS_SCHEMA_VERSION,
)

# 這份索引只按文字表面建立查找鍵，不判斷意思或相關程度。
POLICY_ID = "han-bigram-access"
# 規則內容改變時才調整版本。
POLICY_VERSION = "v2"
# 漢字範圍固定採 Unicode 17.0。
HAN_RANGE_POLICY_UNICODE_VERSION = "17.0"
# 只接受指定格式版本、且已成功完成的上游教材挑選結果。
SELECTION_RUN_SCHEMA_VERSION = "material-selection-run/v1"

# 收錄一般漢字、擴充 A–J 與相容漢字，精確邊界以下列數值為準。
# 官方區段說明：https://www.unicode.org/versions/Unicode17.0.0/core-spec/chapter-18/
HAN_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2A6DF),
    (0x2A700, 0x2B73F),
    (0x2B740, 0x2B81F),
    (0x2B820, 0x2CEAF),
    (0x2CEB0, 0x2EBEF),
    (0x2EBF0, 0x2EE5D),
    (0x2F800, 0x2FA1F),
    (0x30000, 0x3134A),
    (0x31350, 0x323AF),
    (0x323B0, 0x33479),
)


def generate_lexical_keys(text: str) -> list[str]:
    """把文字轉成查找鍵。

    先用 NFC 統一 Unicode 寫法；連續漢字取相鄰兩字，單一漢字直接保留；
    ASCII 英數技術字串轉成小寫。

    例如：「教材圖 API_v2」會得到 ["教材", "材圖", "api_v2"]。
    """
    if not isinstance(text, str):
        raise ValueError("lexical_text_invalid")

    normalized_text = unicodedata.normalize("NFC", text)
    lexical_keys: list[str] = []
    cursor = 0

    while cursor < len(normalized_text):
        current_character = normalized_text[cursor]

        # 漢字：先找出整段連續漢字，再取相鄰兩字。
        if _is_included_han(current_character):
            run_end = cursor + 1
            while (
                run_end < len(normalized_text)
                and _is_included_han(normalized_text[run_end])
            ):
                run_end += 1

            han_run = normalized_text[cursor:run_end]
            if len(han_run) == 1:
                lexical_keys.append(han_run)
            else:
                lexical_keys.extend(
                    han_run[start : start + 2]
                    for start in range(len(han_run) - 1)
                )

            cursor = run_end
            continue

        # 其他文字與符號只用來分隔，不加入查找鍵。
        if not _is_ascii_alphanumeric(current_character):
            cursor += 1
            continue

        # 技術名稱中的 .、_、- 會和前後英數字放在同一個查找鍵；
        # 例如 API_v2 會保留為 api_v2，避免拆成 api 和 v2。
        run_end = cursor + 1
        while run_end < len(normalized_text):
            next_character = normalized_text[run_end]
            if _is_ascii_alphanumeric(next_character):
                run_end += 1
                continue
            if (
                next_character in "._-"
                and _is_ascii_alphanumeric(normalized_text[run_end - 1])
                and run_end + 1 < len(normalized_text)
                and _is_ascii_alphanumeric(normalized_text[run_end + 1])
            ):
                run_end += 1
                continue
            break

        ascii_run = normalized_text[cursor:run_end]
        lexical_keys.append(ascii_run.lower())
        cursor = run_end

    return lexical_keys


def build_material_lexical_index(
    normalized_blocks: Mapping[str, Any],
    normalization_handoff: Mapping[str, Any],
) -> dict[str, Any]:
    """從整理好的教材區塊建立查找索引。

    原始教材區塊保持原狀；建立的索引只提供查找用途。
    例如，文字「教材 API」會建立「教材」和「api」兩個查找項目，
    每個項目都會保留原本來自哪個教材區塊。
    """
    _validate_normalized_blocks_root(normalized_blocks)
    _validate_normalization_handoff(normalization_handoff)

    # 同一個查找鍵在同一來源出現多次時，只增加出現次數。
    counts_by_key_and_source: dict[
        tuple[str, tuple[Any, ...]], dict[str, Any]
    ] = {}
    omitted_sources: list[dict[str, Any]] = []
    normalized_materials = normalized_blocks["materials"]

    # 逐一處理教材區塊，並記錄每個查找鍵在各來源出現的次數。
    for material_row in normalized_materials:
        if not isinstance(material_row, Mapping):
            raise ValueError("normalized_material_invalid")
        block_rows = material_row.get("blocks")
        if not isinstance(block_rows, list):
            raise ValueError("normalized_material_blocks_invalid")

        for block_row in block_rows:
            if not isinstance(block_row, Mapping):
                raise ValueError("normalized_block_invalid")
            block_locator = block_row.get("locator")
            if not isinstance(block_locator, Mapping):
                raise ValueError("normalized_block_locator_invalid")

            source_record = _source_record(
                material_row,
                block_row,
                block_locator,
            )
            if block_row.get("selection_status") != "selected":
                omitted_sources.append(
                    {**source_record, "omission_reason": "not_selected"}
                )
                continue

            block_text = block_row.get("text")
            if not isinstance(block_text, str) or not block_text:
                raise ValueError("selected_block_text_invalid")
            lexical_keys = generate_lexical_keys(block_text)
            if not lexical_keys:
                omitted_sources.append(
                    {
                        **source_record,
                        "omission_reason": "no_indexable_lexical_units",
                    }
                )
                continue

            # 六個欄位共同辨識唯一來源，避免不同教材或頁面的相同文字被合併。
            source_identity = (
                source_record["material_id"],
                source_record["case_id"],
                source_record["artifact_ref"],
                source_record["locator"]["pdf_page"],
                source_record["block_id"],
                source_record["locator"]["source_ref"],
            )
            for lexical_key in lexical_keys:
                source_count_key = (lexical_key, source_identity)
                source_count = counts_by_key_and_source.get(source_count_key)
                if source_count is None:
                    source_count = {
                        **source_record,
                        "occurrence_count": 0,
                    }
                    counts_by_key_and_source[source_count_key] = source_count
                source_count["occurrence_count"] += 1

    # 整理同一查找鍵的來源，並依固定順序輸出。
    sources_by_key: dict[str, list[dict[str, Any]]] = {}
    for (lexical_key, _), source_record in counts_by_key_and_source.items():
        sources_by_key.setdefault(lexical_key, []).append(source_record)

    entries: list[dict[str, Any]] = []
    for lexical_key in sorted(sources_by_key):
        sorted_sources = sorted(
            sources_by_key[lexical_key],
            key=_source_sort_key,
        )
        entries.append(
            {
                "key": lexical_key,
                "sources": sorted_sources,
            }
        )

    # 依查找項目與省略來源的有無，決定這次執行的狀態。
    omitted_sources.sort(key=_source_sort_key)
    omission_reasons = sorted(
        {
            omitted_source["omission_reason"]
            for omitted_source in omitted_sources
        }
    )
    if entries and not omitted_sources:
        status = "success"
        reasons: list[str] = []
    elif entries:
        status = "partial"
        reasons = omission_reasons
    else:
        status = "failed"
        reasons = omission_reasons or ["no_indexable_lexical_units"]

    # 整理規則與來源資訊，再組合完整輸出。
    policy_info = {
        "id": POLICY_ID,
        "role": "auxiliary_lookup_only",
        "version": POLICY_VERSION,
    }
    source_info = {
        "normalized_blocks": {
            "locator": normalization_handoff["normalized_blocks_locator"],
            "schema_version": normalization_handoff[
                "normalized_blocks_schema_version"
            ],
        },
        "normalization_handoff": {
            "selection_run_locator": normalization_handoff[
                "selection_run_locator"
            ],
            "selection_run_schema_version": normalization_handoff[
                "selection_run_schema_version"
            ],
            "selection_run_status": normalization_handoff[
                "selection_run_status"
            ],
        },
    }
    return {
        "policy": policy_info,
        "status": status,
        "reasons": reasons,
        "provenance": source_info,
        "entries": entries,
        "omissions": omitted_sources,
    }


def _validate_normalized_blocks_root(
    normalized_blocks: Mapping[str, Any],
) -> None:
    """確認教材區塊的最外層格式可供索引使用。

    資料需使用目前支援的版本，並以 list 提供 materials。
    """
    if not isinstance(normalized_blocks, Mapping):
        raise ValueError("normalized_blocks_invalid")
    if (
        normalized_blocks.get("schema_version")
        != NORMALIZED_BLOCKS_SCHEMA_VERSION
    ):
        raise ValueError("normalized_blocks_schema_mismatch")
    if not isinstance(normalized_blocks.get("materials"), list):
        raise ValueError("normalized_blocks_materials_invalid")


def _validate_normalization_handoff(
    normalization_handoff: Mapping[str, Any],
) -> None:
    """確認教材挑選流程已成功，且交接資訊符合目前程式。

    檢查挑選結果的狀態與格式版本，以及整理後教材區塊的版本和位置。
    """
    if not isinstance(normalization_handoff, Mapping):
        raise ValueError("normalization_handoff_invalid")
    required_fields = {
        "selection_run_schema_version",
        "selection_run_status",
        "selection_run_locator",
        "normalized_blocks_schema_version",
        "normalized_blocks_locator",
    }
    if not required_fields.issubset(normalization_handoff):
        raise ValueError("normalization_handoff_invalid")
    if (
        normalization_handoff.get("selection_run_schema_version")
        != SELECTION_RUN_SCHEMA_VERSION
    ):
        raise ValueError("selection_run_schema_mismatch")
    if normalization_handoff.get("selection_run_status") != "success":
        raise ValueError("selection_run_not_success")
    run_locator = normalization_handoff.get("selection_run_locator")
    if not isinstance(run_locator, str) or not run_locator:
        raise ValueError("normalization_handoff_invalid")
    if (
        normalization_handoff.get("normalized_blocks_schema_version")
        != NORMALIZED_BLOCKS_SCHEMA_VERSION
    ):
        raise ValueError("normalized_blocks_handoff_schema_mismatch")
    if (
        normalization_handoff.get("normalized_blocks_locator")
        != NORMALIZED_BLOCKS_STABLE_PATH
    ):
        raise ValueError("normalized_blocks_handoff_locator_mismatch")


def _source_record(
    material: Mapping[str, Any],
    block: Mapping[str, Any],
    locator: Mapping[str, Any],
) -> dict[str, Any]:
    """建立來源資訊，並複製可能包含巢狀資料的 provenance。"""
    source = {
        "material_id": block.get(
            "material_id",
            material.get("material_id"),
        ),
        "case_id": block.get("case_id", material.get("case_id")),
        "artifact_ref": block.get(
            "artifact_ref",
            material.get("artifact_ref"),
        ),
        "block_id": block.get("block_id", material.get("block_id")),
        "locator": {
            "pdf_page": locator.get("pdf_page"),
            "source_ref": locator.get("source_ref"),
        },
        "provenance": deepcopy(block.get("provenance")),
        "native_analysis_status": block.get("native_analysis_status"),
        "selection_status": block.get("selection_status"),
        "reasons": _sorted_unique_strings(block.get("reasons")),
        "warnings": _sorted_unique_strings(block.get("warnings")),
    }
    if "selection_reason" in block:
        source["selection_reason"] = block["selection_reason"]
    return source


def _sorted_unique_strings(value: Any) -> list[str]:
    """確認內容是字串清單，移除重複值後依文字排序。"""
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError("normalized_block_reasons_invalid")
    return sorted(set(value))


def _source_sort_key(source: Mapping[str, Any]) -> tuple[Any, ...]:
    """依來源欄位產生固定的排序順序。"""
    locator = source["locator"]
    values = (
        source["material_id"],
        source["case_id"],
        source["artifact_ref"],
        locator["pdf_page"],
        source["block_id"],
        locator["source_ref"],
    )
    return tuple(_sortable(value) for value in values)


def _sortable(value: Any) -> tuple[int, Any]:
    """讓整數、字串與缺少的來源欄位都能固定排序。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, value)
    if isinstance(value, str):
        return (1, value)
    return (2, repr(value))


def _is_included_han(character: str) -> bool:
    code_point = ord(character)
    return any(start <= code_point <= end for start, end in HAN_RANGES)


def _is_ascii_alphanumeric(character: str) -> bool:
    return (
        "0" <= character <= "9"
        or "A" <= character <= "Z"
        or "a" <= character <= "z"
    )
