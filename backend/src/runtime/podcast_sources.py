"""依固定教材來源安排分集，並保留必要引文與有界的鄰接脈絡。"""

from copy import deepcopy
import json

from . import retrieval
from .errors import PodcastError
from .podcast_script import (
    MAX_EPISODE_CLAIMS,
    MAX_SOURCE_TOKENS,
    MAX_SCRIPT_INPUT_TOKENS,
    script_input,
)
from .text_budget import estimate_o200k_tokens


def plan_episodes(view, concept_ids, source_pages):
    concepts = {c["concept_id"]: c for c in view["concepts"]}
    if not set(concept_ids) <= concepts.keys():
        raise PodcastError("REQUEST_INVALID")
    claims = []
    for identity in concept_ids:
        concept = concepts[identity]
        if not concept["claims"]:
            raise PodcastError("PODCAST_SOURCE_INSUFFICIENT")
        for claim in concept["claims"]:
            if not claim["evidence"]:
                raise PodcastError("PODCAST_SOURCE_INSUFFICIENT")
            claims.append(
                {**deepcopy(claim), "concept_id": identity, "label": concept["label"]}
            )
    if not claims:
        return []
    # 只使用本 revision 已有的概念／關係與 Evidence；選取順序和 claim 原文不變。
    # 在同一硬容量內，先少切概念、再少切關係，才考慮集數與剩餘容量。
    positions = {}
    for i, claim in enumerate(claims):
        positions.setdefault(claim["concept_id"], []).append(i)
    relations = [
        r
        for r in view.get("relations", [])
        if r["source_concept_id"] in positions and r["target_concept_id"] in positions
    ]
    # 每個邊界的關係代價只計算一次；千頁教材不在每個候選分集重掃整張關係圖。
    relation_cuts = [0] * (len(claims) + 1)
    for relation in relations:
        ends = (
            positions[relation["source_concept_id"]]
            + positions[relation["target_concept_id"]]
        )
        relation_cuts[min(ends) + 1] += 1
        relation_cuts[max(ends) + 1] -= 1
    for i in range(1, len(relation_cuts)):
        relation_cuts[i] += relation_cuts[i - 1]
    costs = [(0, 0, 0, 0)] + [None] * len(claims)
    previous = [None] * (len(claims) + 1)
    # 依 provider 實際送入模型的投影估算，不把僅供回填的 hash／locator 算成講稿輸入。
    projected = script_input(claims, source_pages)
    token_sizes = [
        estimate_o200k_tokens(json.dumps(source, ensure_ascii=False)) + 4
        for source in projected["sources"]
    ]
    # 引用片語可能只佔原區塊一小部分；分集必須另計索引原文及定位欄位，不能只算 claim.quote。
    block_costs = {}
    page_costs = {}
    for (page_ref, page), compact in zip(
        source_pages.items(), projected["page_context"]
    ):
        page_costs[page_ref] = (
            estimate_o200k_tokens(
                json.dumps({**compact, "blocks": []}, ensure_ascii=False)
            )
            + 4
        )
        for block, compact_block in zip(page["blocks"], compact["blocks"]):
            block_costs[block["evidence_id"]] = (
                page_ref,
                estimate_o200k_tokens(json.dumps(compact_block, ensure_ascii=False))
                + 4,
            )
    if any(
        e["evidence_id"] not in block_costs
        for claim in claims
        for e in claim["evidence"]
    ):
        raise PodcastError("PODCAST_SOURCE_INSUFFICIENT")
    for end in range(1, len(claims) + 1):
        tokens = 0
        context_tokens = 2
        seen_blocks = set()
        seen_pages = set()
        for start in range(end - 1, max(-1, end - MAX_EPISODE_CLAIMS - 1), -1):
            tokens += token_sizes[start]
            for evidence in claims[start]["evidence"]:
                identity = evidence["evidence_id"]
                if identity in seen_blocks:
                    continue
                page_ref, cost = block_costs[identity]
                context_tokens += cost
                seen_blocks.add(identity)
                if page_ref not in seen_pages:
                    context_tokens += page_costs[page_ref]
                    seen_pages.add(page_ref)
            if (
                context_tokens > MAX_SOURCE_TOKENS
                or tokens + context_tokens + 32 > MAX_SCRIPT_INPUT_TOKENS
            ):
                break
            if costs[start] is None:
                continue
            inside = int(
                start > 0
                and claims[start - 1]["concept_id"] == claims[start]["concept_id"]
            )
            crossed = 0
            if start:
                crossed = relation_cuts[start]
                # 同一引用區塊通常是連續定義／步驟；同分時避免從中間切開。
                crossed += bool(
                    {
                        e["evidence_id"]
                        for e in claims[start - 1]["evidence"]
                        if "evidence_id" in e
                    }
                    & {
                        e["evidence_id"]
                        for e in claims[start]["evidence"]
                        if "evidence_id" in e
                    }
                )
            delta = (inside, crossed, 1, (MAX_EPISODE_CLAIMS - (end - start)) ** 2)
            cost = tuple(a + b for a, b in zip(costs[start], delta))
            if costs[end] is None or cost < costs[end]:
                costs[end], previous[end] = cost, start
    if previous[-1] is None:
        raise PodcastError("PODCAST_SOURCE_TOO_LARGE")
    episodes = []
    end = len(claims)
    while end:
        start = previous[end]
        episodes.append({"claims": claims[start:end], "script": None, "audio": None})
        end = start
    return episodes[::-1]


def _context_pages(passages):
    """還原已取回的原文；不連續片段須標出缺口，不能假接成完整區塊。"""
    pages = {}
    ends = {}
    for passage in passages:
        evidence = passage["evidence"]
        page_ref = evidence["page_ref"]
        page = pages.setdefault(
            page_ref,
            {
                "source_id": evidence["source_id"],
                "source_name": evidence["source_name"],
                "normalized_page": evidence["normalized_page"],
                "blocks": [],
            },
        )
        existing = next(
            (
                block
                for block in page["blocks"]
                if block["evidence_id"] == evidence["evidence_id"]
            ),
            None,
        )
        if existing:
            if passage["start"] != ends[evidence["evidence_id"]]:
                existing["text"] += "\n[…]\n"
            existing["text"] += passage["text"]
            ends[evidence["evidence_id"]] = passage["end"]
            continue
        page["blocks"].append(
            {
                "evidence_id": evidence["evidence_id"],
                "block_order": evidence["block_order"],
                "text": passage["text"],
                "region": deepcopy(evidence["source_locator"]["region"]),
            }
        )
        ends[evidence["evidence_id"]] = passage["end"]
    return pages


def source_context(db, owner, material_id, revision, episode):
    """全部必要引用先保留，再以剩餘容量補同頁鄰接區塊。"""
    ids = {e["evidence_id"] for c in episode["claims"] for e in c["evidence"]}
    passages = retrieval.evidence_context(db, owner, material_id, revision, ids)
    selected = [p for p in passages if p["evidence_id"] in ids]
    pages = _context_pages(selected)

    def fits(context):
        projected = script_input(episode["claims"], context)
        return (
            estimate_o200k_tokens(
                json.dumps(projected["page_context"], ensure_ascii=False)
            )
            <= MAX_SOURCE_TOKENS
            and estimate_o200k_tokens(json.dumps(projected, ensure_ascii=False))
            <= MAX_SCRIPT_INPUT_TOKENS
        )

    if not fits(pages):
        raise PodcastError("PODCAST_SOURCE_TOO_LARGE")
    for passage in passages:
        if passage["evidence_id"] in ids:
            continue
        proposed = _context_pages(
            sorted([*selected, passage], key=lambda p: p["position"])
        )
        if fits(proposed):
            selected.append(passage)
            pages = proposed
    return pages
