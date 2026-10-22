"""以既有 Luna 整理已保存的地圖，包含跨來源等義與翻譯後重複。"""

from copy import deepcopy
import time

from pydantic import ValidationError

from knowledge_map import consolidation as merge
from knowledge_map.material_review import ReviewError, _name
from knowledge_map.structure_rules import _revision
from content_identity import canonical_sha256
from .semantic_service import SemanticServiceError, request_semantics, semantic_client


def _catalog(document):
    return [
        {
            "h": h,
            "label": c["label"],
            "aliases": c["aliases"],
            # 摘錄只供候選路由；套用時必須重新看完整 Claim 與原引用。
            "preview": " ".join(q["text"] for q in c["claims"])[:240],
        }
        for h, c in enumerate(document["concepts"])
    ]


def _candidate_components(count, candidates):
    parents = list(range(count))

    def root(h):
        while parents[h] != h:
            parents[h] = parents[parents[h]]
            h = parents[h]
        return h

    for candidate in candidates:
        for h in candidate[1:]:
            parents[root(h)] = root(candidate[0])
    groups = {}
    for h in range(count):
        groups.setdefault(root(h), []).append(h)
    return list(groups.values())


def _same_name_candidates(catalog):
    """同名／別名只保證一起比較，不代表可直接合併不同研究的定義。"""
    names = {}
    for concept in catalog:
        for name in {
            _name(n) for n in [concept["label"], *concept["aliases"]] if n.strip()
        }:
            names.setdefault(name, []).append(concept["h"])
    return [group for group in names.values() if len(group) > 1]


def _response_schema(operation, payload, model):
    """將本批 handle 範圍寫入 schema，請求、修正與保存另由 runner 負責。"""
    schema = model.model_json_schema()
    if operation == "consolidation_rewrite":
        schema["$defs"]["Concept"]["properties"]["concepts"]["items"]["enum"] = [
            c["h"] for c in payload["concepts"]
        ]
        schema["$defs"]["Concept"]["properties"]["title_from"]["enum"] = [
            c["h"] for c in payload["concepts"]
        ]
        schema["$defs"]["Claim"]["properties"]["claims"]["items"]["enum"] = [
            q["h"] for q in payload["claims"]
        ]
        untouched = deepcopy(schema["$defs"]["Claim"])
        untouched["properties"]["text"] = {"type": "null"}
        untouched["properties"]["claims"]["maxItems"] = 1
        rewritten = deepcopy(schema["$defs"]["Claim"])
        rewritten["properties"]["text"] = {"type": "string", "minLength": 1}
        schema["$defs"]["Claim"] = {"anyOf": [untouched, rewritten]}
    elif operation == "consolidation_check":
        schema["$defs"]["Check"]["properties"]["concept"]["enum"] = payload[
            "check_concepts"
        ]
    return schema


def _rewrite_payload(document, handles, components):
    """去重請求保留完整 Claim 與其歸屬；目錄摘錄不可用來套用修改。"""
    payload, _ = merge.pack(document, handles)
    payload["candidate_groups"] = [group for group in components if len(group) > 1]
    payload["editing_policy"] = (
        "這是保守去重，不是摘要、事實修正或重新翻譯。text 必須以原 Claim 文字為底稿，"
        "只刪去等義的重複句段；其他原句、數值、文獻年份、章節／表號、條件、否定、公式與程式碼逐字保留。"
        "合併多個 Claim 時先保留其完整原文，再移除其中重複的部分，不另寫一份縮短的摘要。"
        "若一筆 Claim 已完整涵蓋另一筆，可沿用完整那筆；仍要保留後者獨有的資訊及技術字面值。"
        "去重範圍是每個輸出概念自身：只要沒有合併成同一個輸出概念，各自原 Claim 的獨有事實就必須留在各自輸出中。"
        "不能因另一個獨立概念提到某事，就刪掉本概念的實驗設定、例子或背景；也不能搬入別的概念才有的資訊。"
        "不要換成新的數字寫法，例如「六」不可改成「6」。沒有重複的單一 Claim 用 text=null 原樣保留。"
    )
    payload["claim_ownership_rule"] = (
        "每個輸出概念的 Claim h 聯集必須恰好等於該概念 concepts 成員各自原 claims 的聯集。"
        "不能把另一個仍獨立保留之概念的 Claim 搬過來，也不能將一個原 Claim 同時放在兩個輸出項。"
        "concept h 是全圖編號，claim h 是本次請求編號，兩者不得混用。"
    )
    payload["duplication_scope"] = (
        "去重的對象是同一獨立觀念／命題的重複表述。同名候選必須比較完整定義，可分開保留不同定義。"
        "兩個本質不同的概念可共用必要的背景、前提或對比：例如生成流程與解碼各自說明候選分布，"
        "推論與微調的對比各自提及參數是否更新。這種不同教學對象的必要上下文不是應刪的重複，"
        "不得為跨概念去重刪掉各自定義，亦不得將僅有相關性的概念硬合併。"
        "同一概念內重複句段／Claim、等義節點及翻譯造成的雙份表述則必須整理；"
        "獨立覆核 duplicates_removed 也依此範圍判斷，不提出與保留獨有命題相衝突的要求。"
    )
    return payload


def _check_payload(payload, proposal, changed):
    """獨立核對本次去重的覆蓋與來源支持，不重新判分既有教材。"""
    return {
        **payload,
        "proposal": proposal,
        "verification_rules": (
            "duplicates_removed 表示輸出沒有仍需去除的重複：原本就沒有重複時也必須為 true；"
            "不能在理由寫「未見重複」卻填 false。此欄不是「是否發生刪除」的操作紀錄。"
            "除每個節點內部，也必須比較 proposal 中不同輸出節點是否仍表示同一獨立觀念，"
            "尤其逐一核對 candidate_groups 的完整定義。若等義節點仍分開，兩者 duplicates_removed 都為 false，"
            "理由指出其原 concept h 及相同定義，交由改寫合併後聯集全部資訊。"
            "概念身分看核心規則／機制與學習問題，不要求成員 Claim 全部相同。"
            "同一規則的概述、實驗條件與執行細節是互補資訊，不能僅因詳略或條件不同而判為不同概念；"
            "例如同一研究的同一停止規則分別介紹預算、觸發條件與具體執行，應合併節點並保留每個獨有條件。"
            "只有核心定義、操作或學習問題不同才分開，不能把「各有獨有 Claim」誤當成不等義。"
            "只共享背景、相關但不同的觀念仍分開保留；不同研究的同名概念須依完整定義判斷。"
            "本次只驗收去重造成的變更，不重新評分舊教材的事實正確性。"
            "sources_support_text 檢查本次是否新增無來源主張或改換原有事實歸屬；"
            "原樣保留的原 Claim 不因既有事實／翻譯問題而判 false，也不可要求在去重時順便修正。"
            "對有改寫的 Claim 比較改寫前後，僅刪除等義重複且原有事實歸屬保留時為 true；"
            "不能將原本既有的問題冒稱本次合併引入的錯誤。"
            "preserves_unique_information 僅在確實漏掉非重複的命題或條件時為 false，"
            "不得僅因重複還沒刪就填 false，也不得要求重新加入已由其他原句完整表達的同一命題。"
        ),
        "check_concepts": changed,
    }


def consolidate_structure(
    document, lock, archive, check_cancel, progress, *, wait_cancellation_check=None
):
    if lock.get("material_review", {}).get("consolidation_policy") != merge.POLICY:
        return document
    started = time.monotonic()
    calls, responses, new_proposals = 0, 0, 0
    audit = {
        "policy": merge.POLICY,
        "source_revision": document["revision"],
        "changes": [],
        "blocked_changes": [],
        "split_batches": [],
    }

    def request(operation, payload, model, validator=None):
        nonlocal calls, responses, new_proposals
        check_cancel()
        request_payload = {"operation": operation, "policy": merge.POLICY, **payload}
        key = canonical_sha256(
            {"request": request_payload, "policy": lock["material_review"]}
        )
        rejected = None

        def validate(value):
            nonlocal rejected
            try:
                parsed = model.model_validate(value)
            except ValidationError:
                raise ReviewError("CONSOLIDATION_RESPONSE_INVALID") from None
            if validator:
                try:
                    validator(parsed, value)
                except ReviewError as error:
                    error.rejected_proposal = value
                    rejected = rejected or error
                    raise
            return parsed

        value = archive.load_review(key, validate_response=validate)
        if value is not None:
            responses += 1
            return validate(value), value
        if rejected is not None:
            # 已保存的不合格原稿只用來產生精確補正，不重送同一請求，也不冒充通過。
            raise rejected
        # 與一般 review 批次使用分開的名稱；完整 request／response 與快取保存在私人 archive。
        name = f"consolidation/call-{calls + 1:06d}"
        archive.save_review(
            f"{name}/request", {"cache_key": key, "request": request_payload}
        )
        schema = _response_schema(operation, payload, model)
        with semantic_client() as client:
            try:
                value = request_semantics(
                    client,
                    runtime_lock=lock,
                    task="material_review",
                    request=request_payload,
                    response_schema=schema,
                    cancellation_check=wait_cancellation_check or check_cancel,
                )
            except SemanticServiceError as error:
                if error.reason_code != "SEMANTIC_INPUT_TOO_LARGE":
                    calls += 1
                raise
        calls += 1
        responses += 1
        if operation == "consolidation_rewrite":
            new_proposals += 1
        archive.save_review(f"{name}/response", value)
        try:
            parsed = validate(value)
        except ReviewError as error:
            error.rejected_proposal = value
            raise
        archive.save_review(f"cache-{key}", value)
        return parsed, value

    catalog = _catalog(document)
    candidates = _same_name_candidates(catalog)

    def plan(left, right=None):
        rows = left + (right or [])
        allowed = {row["h"] for row in rows}

        def validate(parsed, _):
            for group in parsed.groups:
                if (
                    len(set(group.concepts)) != len(group.concepts)
                    or not set(group.concepts) <= allowed
                ):
                    raise ReviewError("CONSOLIDATION_CANDIDATE_INVALID")

        try:
            parsed, _ = request(
                "consolidation_candidates",
                {"catalog": rows},
                merge.Candidates,
                validate,
            )
            candidates.extend(group.concepts for group in parsed.groups)
        except SemanticServiceError as error:
            if error.reason_code != "SEMANTIC_INPUT_TOO_LARGE" or len(rows) < 3:
                raise
            # 容量不足時仍比較跨批候選，不讓批次／來源邊界漏掉等義概念。
            if right is None:
                middle = len(left) // 2
                plan(left[:middle])
                plan(left[middle:])
                plan(left[:middle], left[middle:])
            elif len(left) >= len(right):
                middle = len(left) // 2
                plan(left[:middle], right)
                plan(left[middle:], right)
            else:
                middle = len(right) // 2
                plan(left, right[:middle])
                plan(left, right[middle:])

    progress("review", 0, 1)
    if len(catalog) > 1:
        plan(catalog)
    components = _candidate_components(len(catalog), candidates)
    # 候選等義群不能在一般分批時拆散；其餘概念也要檢查內部重複。
    batches, current = [], []
    for component in components:
        if current and sum(map(len, current)) + len(component) > 12:
            batches.append(current)
            current = []
        current.append(component)
    if current:
        batches.append(current)
    accepted = []
    completed = 0
    progress("review", completed, len(batches))
    while batches:
        components = batches.pop(0)
        handles = [h for component in components for h in component]
        payload = _rewrite_payload(document, handles, components)
        corrections = None
        projected = None
        rescheduled = False
        proposal_start = new_proposals
        seen_corrections = set()
        # 語意補正後仍可能觸發字面值補正；保留兩者的修正空間，重複同一失敗則立即停止。
        # 明確接續時，重播舊拒絕理由不耗掉新稿額度。
        while new_proposals - proposal_start < 4:
            correction_key = canonical_sha256(corrections)
            if correction_key in seen_corrections:
                break
            seen_corrections.add(correction_key)

            def validate_proposal(_, value):
                merge.project(document, handles, value)

            try:
                _, proposal = request(
                    "consolidation_rewrite",
                    {**payload, **({"correction": corrections} if corrections else {})},
                    merge.Proposal,
                    validate_proposal,
                )
                projected = merge.project(document, handles, proposal)
                # 未改寫也須檢查是否漏掉重複，避免全部保留便繞過去重驗收。
                changed = list(range(len(projected)))
                if changed:

                    def validate_checks(parsed, _):
                        merge._partition(
                            [c.concept for c in parsed.checks],
                            changed,
                            "CONSOLIDATION_CHECK_COVERAGE_INVALID",
                        )

                    checked, _ = request(
                        "consolidation_check",
                        _check_payload(payload, proposal, changed),
                        merge.Checks,
                        validate_checks,
                    )
                    rejected = [
                        c.model_dump()
                        for c in checked.checks
                        if not all(
                            (
                                # 單一原節點沒有「合併非等義節點」這個待判斷事項。
                                len(projected[c.concept]["members"]) == 1
                                or c.equivalent_concepts,
                                c.preserves_unique_information,
                                c.sources_support_text,
                                c.duplicates_removed,
                            )
                        )
                    ]
                    if rejected:
                        corrections = {
                            "rejected_proposal": proposal,
                            "findings": rejected,
                        }
                        projected = None
                        continue
                # 包含先前已接受的合併，檢查邊的方向衝突與必要先備循環。
                merge.apply(document, [*accepted, *projected])
                break
            except SemanticServiceError as error:
                if error.reason_code != "SEMANTIC_INPUT_TOO_LARGE":
                    raise
                if len(components) == 1:
                    # 單一等義群（或單一概念）本身就超過容量：比照修正不合格的批次，
                    # 這組維持原樣不合併並記錄原因，不截內容，也不讓整份分析失敗。
                    audit["blocked_changes"].append(
                        {
                            "concepts": handles,
                            "findings": {"reason_code": error.reason_code},
                        }
                    )
                    completed += 1
                    rescheduled = True
                    projected = None
                    break
                middle = len(components) // 2
                batches[0:0] = [components[:middle], components[middle:]]
                rescheduled = True
                projected = None
                break
            except ReviewError as error:
                corrections = {
                    "reason_code": str(error),
                    **({"details": error.details} if hasattr(error, "details") else {}),
                    **(
                        {"rejected_proposal": error.rejected_proposal}
                        if hasattr(error, "rejected_proposal")
                        else {}
                    ),
                    "instruction": "保留所有原概念與 Claim 的歸屬、來源、數字、公式及不同條件；修正不合格的合併。",
                }
                projected = None
        if projected is not None:
            accepted.extend(projected)
            completed += 1
        elif corrections and not rescheduled and len(components) > 1:
            # 長內容即使放得進 context，也可能在整批補正時顧此失彼。
            # 候選等義群維持完整；縮小工作單位後繼續同一套驗證，不降低門檻。
            batches[0:0] = [[component] for component in components]
            audit["split_batches"].append(
                {"concepts": handles, "findings": corrections}
            )
        elif corrections and not rescheduled:
            audit["blocked_changes"].append(
                {"concepts": handles, "findings": corrections}
            )
            completed += 1
        progress("review", completed, completed + len(batches))
    result, mapping = merge.apply(document, accepted)
    audit.update(mapping)
    audit["changes"] = [
        {
            "original_concept_ids": [
                document["concepts"][h]["concept_id"] for h in group["members"]
            ],
            "concept_id": group["concept"]["concept_id"],
            "claim_mapping": group["claim_mapping"],
            "reason": group["reason"],
        }
        for group in accepted
        if len(group["members"]) > 1
        or group["concept"] != document["concepts"][group["members"][0]]
    ]
    if audit["blocked_changes"]:
        result["source_review_required"] = True
        if "SOURCE_REVIEW_SUGGESTED" not in result["status"]["reason_codes"]:
            result["status"]["reason_codes"].append("SOURCE_REVIEW_SUGGESTED")
        result["status"].update(
            processing="partial", quality="needs_review", decision="review"
        )
    result["metrics"]["semantic_calls"] += responses
    result["metrics"]["semantic_duration_ms"] += round(
        (time.monotonic() - started) * 1000
    )
    result["revision"] = _revision(result)
    audit.update(
        applied_revision=result["revision"],
        model_calls=calls,
        responses=responses,
        concept_count_before=len(document["concepts"]),
        concept_count_after=len(result["concepts"]),
    )
    archive.save_review("consolidation/result", audit)
    return result
