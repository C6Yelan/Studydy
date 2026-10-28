"""只補正尚未可讀的學習文字，保留技術片段與來源；結果仍須通過教材審查。"""

from copy import deepcopy
import re

from .claim_literals import mask_literals, restore_literals, EXCERPT_PROJECTIONS, LITERAL_REPAIRS, quoted_sources
from .semantic_projection import _expand_claim, _project_claim, spoken_handles

POLICY = "traditional-chinese-literals/v2"


def _chinese(text):
    return isinstance(text, str) and re.search(r"[\u3400-\u9fff]", text) is not None


def _needs_translation(text, sources=()):
    if not _chinese(text):
        return True
    prose, _ = mask_literals(text)
    if re.search(
        r"[\u0400-\u1cff\u3040-\u30ff\uac00-\ud7af]|[\u0370-\u03ff]{4,}", prose
    ):
        return True
    # PDF 的獨立圖解標籤可能被串成「un believ able」。只接受原引用中
    # 各自獨立的單字標題；一般英文敘述、未引用的標籤仍須翻譯。
    labels = {
        value.casefold()
        for source in sources
        if source.get("kind") == "heading"
        for value in [source.get("exact_text", source.get("quote", "")).strip()]
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value)
    }
    return any(
        not all(word in labels for word in match[0].split())
        for match in re.finditer(r"\b[a-z]+(?:[ -]+[a-z]+){2,}\b", prose)
    )


def localize_block(response, context, bundle, call, dropped=None):
    """dropped 傳入時，兩輪修正仍不合格的項目只移除該項（重點、關係）或保留原文（概念名稱），
    並累計在 dropped；未傳入時沿用整塊失敗。"""
    result = deepcopy(response)
    items, targets = {}, {}
    evidence = {e["evidence_id"]: e for e in bundle["evidence"]}
    excerpt_handles = spoken_handles(context)
    excerpt_evidence_ids = {context["evidence"][h]["evidence_id"] for h in excerpt_handles}

    def project(claim):
        expanded = _expand_claim(claim, context["evidence"], excerpt_handles=excerpt_handles)
        return _project_claim(
            expanded, evidence,
            excerpts=expanded is not None and any(isinstance(ref, dict) for ref in claim["s"]),
            excerpt_evidence_ids=excerpt_evidence_ids,
        )

    def sources_for(projected):
        return quoted_sources(
            projected["source_spans"], evidence,
            excerpts=projected["projection"] in EXCERPT_PROJECTIONS,
            excerpt_evidence_ids=excerpt_evidence_ids,
        )

    def add(key, text, target, field, *, maximum=4096, sources=None):
        if sources:
            parts, literals = [], {}
            for source in sources:
                part, protected = mask_literals(
                    source["exact_text"], source["kind"], token_start=len(literals)
                )
                # 連續純字面值（例如表格數值）不需逐格翻譯；保留原順序與分隔，
                # 合成一個必須完整還原的片段，避免模型抄回長串代號時漏格。
                if part in protected and parts and parts[-1] in literals:
                    literals[parts[-1]] += "\n\n" + protected[part]
                    continue
                parts.append(part)
                literals.update(protected)
            masked = "\n\n".join(parts)
        else:
            masked, literals = mask_literals(text)
        items[key] = {
            "text": masked,
            "literals": literals,
            "kind": "概念名稱"
            if field == "l"
            else "關係理由"
            if field == "r"
            else "學習重點",
        }
        targets[key] = (target, field, maximum)

    for index, concept in enumerate(result["concepts"]):
        if _needs_translation(concept["l"]):
            add(f"label_{index}", concept["l"], concept, "l", maximum=256)
        for number, claim in enumerate(concept["c"]):
            projected = project(claim)
            if projected is None:
                continue
            sources = sources_for(projected)
            if projected["projection"] in LITERAL_REPAIRS or _needs_translation(
                projected["text"], sources
            ):
                original = " ".join(source["exact_text"] for source in sources)
                literal_source = " ".join(projected["text"].split()) == " ".join(
                    original.split()
                )
                add(
                    f"claim_{index}_{number}",
                    projected["text"],
                    claim,
                    "m",
                    sources=sources if literal_source else None,
                )
    for index, relation in enumerate(result["relations"]):
        if _needs_translation(relation["r"]):
            add(f"relation_{index}", relation["r"], relation, "r", maximum=1024)
    keys = list(items)
    for start in range(0, len(keys), 4):
        batch = keys[start : start + 4]
        pending = {key: items[key] for key in batch}
        # 已通過的項目直接保留；只讓失敗項目依具體原因修正一次。
        for attempt in range(2):
            properties = {
                key: {"type": "string", "minLength": 1, "maxLength": targets[key][2]}
                for key in pending
            }
            schema = {
                "type": "object",
                "additionalProperties": False,
                "required": list(pending),
                "properties": properties,
            }
            translated = call({"localization": pending}, schema)
            if not isinstance(translated, dict) or set(translated) != set(pending):
                raise ValueError("SEMANTIC_OUTPUT_INVALID")
            failed = {}
            for key in pending:
                target, field, maximum = targets[key]
                reason = None
                try:
                    value = restore_literals(translated[key], items[key]["literals"])
                except ValueError:
                    reason = (
                        "每個保留代號必須原樣出現恰好一次，不可遺漏、重複或新增代號。"
                    )
                    if isinstance(translated[key], str):
                        missing = [
                            token
                            for token in items[key]["literals"]
                            if token not in translated[key]
                        ]
                        repeated = [
                            token
                            for token in items[key]["literals"]
                            if translated[key].count(token) > 1
                        ]
                        unknown = [
                            token
                            for token in re.findall(r"⟪保留值*\d+⟫", translated[key])
                            if token not in items[key]["literals"]
                        ]
                        reason += f" 本次遺漏：{missing}；重複：{repeated}；未知：{unknown}。請逐項對照原文補回，不直接刪除相關條件。"
                if reason is None:
                    if not _chinese(value):
                        reason = "須有繁體中文說明；若內容全為表格值、模型名稱或公式，加入簡短中文引介並保留所有代號。"
                    elif len(" ".join(value.split())) > maximum:
                        reason = (
                            f"還原字面值後不得超過 {maximum} 字元，請縮短中文敘述。"
                        )
                    elif field == "m":
                        projected = project({**target, "m": value})
                        if projected is not None:
                            sources = sources_for(projected)
                            if projected["projection"] in LITERAL_REPAIRS:
                                reason = "還原後仍改變來源技術值；只能翻譯散文，保留來源所有數字、單位、程式與公式，不新增技術值。"
                            elif _needs_translation(projected["text"], sources):
                                reason = "仍含未翻譯的外文敘述，須譯成繁體中文；只有保留代號內的原文不可改寫。"
                    elif _needs_translation(value):
                        reason = "仍含未翻譯的外文敘述，全部一般敘述須忠實譯成繁體中文；英文技術術語可保留原文。"
                if reason:
                    failed[key] = {
                        **items[key],
                        "previous_output": translated[key],
                        "correction": reason,
                    }
                    continue
                if field == "l" and target["l"] not in target["a"]:
                    target["a"].append(target["l"])
                target[field] = value
            if not failed:
                break
            if attempt == 1:
                if dropped is None:
                    raise ValueError("SEMANTIC_OUTPUT_INVALID")
                for key in failed:
                    target, field, _ = targets[key]
                    if field == "m":
                        target["dropped"] = True
                        dropped["claims"] = dropped.get("claims", 0) + 1
                    elif field == "r":
                        target["dropped"] = True
                        dropped["relations"] = dropped.get("relations", 0) + 1
                    # 概念名稱翻不好時保留原文名稱；名稱不是 Evidence，不影響重點與來源。
            pending = failed
    if dropped is not None:
        for concept in result["concepts"]:
            concept["c"] = [claim for claim in concept["c"] if not claim.get("dropped")]
        result["relations"] = [r for r in result["relations"] if not r.get("dropped")]
    # 不讓修正失敗又默默變成整段外文；只能保存完整通過的批次。
    for concept in result["concepts"]:
        for claim in concept["c"]:
            projected = project(claim)
            if projected is not None and (
                projected["projection"] in LITERAL_REPAIRS
                or _needs_translation(
                    projected["text"],
                    sources_for(projected),
                )
            ):
                raise ValueError("SEMANTIC_OUTPUT_INVALID")
    return result
