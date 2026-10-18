"""Teaching beats 與舊 segment 共用的來源投影；不改寫已保存文件。"""

from copy import deepcopy
from hashlib import sha256
import json
from .errors import PodcastError

SCHEMA = "podcast-script/v2"
MAX_EPISODE_CLAIMS = 32
MAX_SOURCE_TOKENS = 8000
MAX_SCRIPT_INPUT_TOKENS = 10000


def prompt_sources(claims, pages):
    """原文只放在檢索 context 一次；重點與來源的對應仍逐項保留。"""
    blocks = {b["evidence_id"]: b["text"] for p in pages.values() for b in p["blocks"]}
    result = []
    for index, claim in enumerate(claims):
        ids = list(dict.fromkeys(e["evidence_id"] for e in claim["evidence"]))
        source = {"source_index": index, "concept": claim["label"], "evidence_ids": ids}
        original = [blocks[i] for i in ids if i in blocks]
        # 只省略完全相同的原文，不做截斷或語意猜測。獨立的摘要仍須提供給講稿模型。
        normalized = " ".join(claim["text"].split())
        if normalized not in {
            " ".join(t.split()) for t in [*original, "\n".join(original)]
        }:
            source["claim"] = claim["text"]
        missing = [e for e in claim["evidence"] if e["evidence_id"] not in blocks]
        if missing:
            source["evidence"] = [
                {k: e[k] for k in ("page_ref", "quote") if k in e} for e in missing
            ]
        result.append(source)
    return result


def script_input(claims, pages):
    """模型只讀局部編號與原文；長 hash／座標留在程式端回填來源。"""
    indices = {}
    context = []
    for page in pages.values():
        blocks = []
        for block in page["blocks"]:
            index = len(indices)
            indices[block["evidence_id"]] = index
            blocks.append({"index": index, "text": block["text"]})
        context.append(
            {
                "source": page.get("source_name", ""),
                "page": page.get("normalized_page"),
                "blocks": blocks,
            }
        )
    sources = prompt_sources(claims, pages)
    for source in sources:
        source["block_indices"] = [
            indices[i] for i in source.pop("evidence_ids") if i in indices
        ]
    return {"sources": sources, "page_context": context}


def digest(script):
    return sha256(
        json.dumps(
            script, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def validate(script, episode):
    try:
        if not (
            {"schema", "segments", "provider"}
            <= set(script)
            <= {"schema", "segments", "provider", "review"}
            and script["schema"] == SCHEMA
        ):
            raise ValueError()
        if not (isinstance(script["provider"], str) and script["provider"]):
            raise ValueError()
        beats = script["segments"]
        claims = episode["claims"]
        if not (isinstance(beats, list) and 1 <= len(beats) <= 12):
            raise ValueError()
        total_size = 0
        for i, beat in enumerate(beats):
            if not (
                set(beat) == {"beat_id", "title", "turns"}
                and beat["beat_id"] == f"beat-{i}"
            ):
                raise ValueError()
            if not (isinstance(beat["title"], str) and 1 <= len(beat["title"]) <= 80):
                raise ValueError()
            if not (isinstance(beat["turns"], list) and beat["turns"]):
                raise ValueError()
            size = 0
            for turn in beat["turns"]:
                if not (set(turn) == {"speaker", "text", "parts"}):
                    raise ValueError()
                if not (
                    turn["speaker"]
                    in (
                        {"host", "guest"}
                        if episode["delivery"] == "dialogue"
                        else {"host"}
                    )
                ):
                    raise ValueError()
                if not (isinstance(turn["parts"], list) and turn["parts"]):
                    raise ValueError()
                for part in turn["parts"]:
                    if not (
                        set(part) == {"text", "source_refs"}
                        and isinstance(part["text"], str)
                        and part["text"]
                    ):
                        raise ValueError()
                    if not (isinstance(part["source_refs"], list)):
                        raise ValueError()
                    seen = set()
                    for ref in part["source_refs"]:
                        if not (set(ref) == {"source_index", "evidence_ids"}):
                            raise ValueError()
                        index = ref["source_index"]
                        if not (
                            type(index) is int
                            and 0 <= index < len(claims)
                            and index not in seen
                        ):
                            raise ValueError()
                        seen.add(index)
                        ids = ref["evidence_ids"]
                        if not (
                            isinstance(ids, list)
                            and ids
                            and all(isinstance(e, str) for e in ids)
                        ):
                            raise ValueError()
                        if not (
                            len(set(ids)) == len(ids)
                            and set(ids)
                            <= {e["evidence_id"] for e in claims[index]["evidence"]}
                        ):
                            raise ValueError()
                if not (turn["text"] == "".join(p["text"] for p in turn["parts"])):
                    raise ValueError()
                if not (1 <= len(turn["text"].strip()) <= 1600):
                    raise ValueError()
                size += len(turn["text"])
            total_size += size
        # 沿用原六來源 × 1600 字的全集預算，分段自由不擴大 TTS／cue 容量。
        if total_size > 9600:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("PODCAST_SCRIPT_INVALID") from None
    return deepcopy(script)


def references(episode, segment_index, turn_index=None, start=0, end=None):
    """字元範圍只取真正交集的引用；同 claim ID 不合併不同來源位置。"""
    segment = episode["script"]["segments"][segment_index]
    if episode["script"].get("schema") != SCHEMA:
        claim = episode["claims"][segment_index]
        if segment.get("claim_id") != claim["claim_id"]:
            raise ValueError("VIDEO_SOURCE_CHANGED")
        return [
            {
                "source_index": segment_index,
                "evidence_ids": [e["evidence_id"] for e in claim["evidence"]],
            }
        ]
    turns = segment["turns"] if turn_index is None else [segment["turns"][turn_index]]
    refs = {}
    for turn in turns:
        offset = 0
        for part in turn["parts"]:
            stop = offset + len(part["text"])
            if stop > start and (end is None or offset < end):
                for ref in part["source_refs"]:
                    values = refs.setdefault(ref["source_index"], [])
                    values.extend(e for e in ref["evidence_ids"] if e not in values)
            offset = stop
    return [{"source_index": i, "evidence_ids": ids} for i, ids in refs.items()]


def sources(episode, refs):
    claims = []
    evidence = {}
    for ref in refs:
        claim = episode["claims"][ref["source_index"]]
        claims.append(
            {
                "source_index": ref["source_index"],
                "concept_id": claim.get("concept_id"),
                "claim_id": claim["claim_id"],
            }
        )
        for e in claim["evidence"]:
            if e["evidence_id"] in ref["evidence_ids"]:
                evidence[e["evidence_id"]] = deepcopy(e)
    return claims, list(evidence.values())


def validate_script(script, episode):
    if isinstance(script, dict) and script.get("schema") == SCHEMA:
        try:
            return validate(script, episode)
        except ValueError:
            raise PodcastError("PODCAST_SCRIPT_INVALID") from None
    claims = episode["claims"]
    if not isinstance(script, dict) or set(script) != {"segments", "provider"}:
        raise PodcastError("PODCAST_SCRIPT_INVALID")
    segments = script["segments"]
    if not isinstance(segments, list) or len(segments) != len(claims):
        raise PodcastError("PODCAST_SCRIPT_INVALID")
    for segment, claim in zip(segments, claims):
        if (
            not isinstance(segment, dict)
            or segment.get("claim_id") != claim["claim_id"]
        ):
            raise PodcastError("PODCAST_SCRIPT_INVALID")
        dialogue = episode["delivery"] == "dialogue"
        turns = segment.get("turns")
        speakers = {"host", "guest"} if dialogue else {"host"}
        if (
            set(segment) != {"claim_id", "turns"}
            or not isinstance(turns, list)
            or not 1 <= len(turns) <= (8 if dialogue else 3)
            or any(
                not isinstance(t, dict)
                or set(t) != {"speaker", "text"}
                or t["speaker"] not in speakers
                or not isinstance(t["text"], str)
                or not 5 <= len(t["text"].strip()) <= (400 if dialogue else 1600)
                for t in turns
            )
            or sum(len(t["text"]) for t in turns) > 1600
        ):
            raise PodcastError("PODCAST_SCRIPT_INVALID")
    if episode["delivery"] == "dialogue" and {
        t["speaker"] for s in segments for t in s["turns"]
    } != {"host", "guest"}:
        raise PodcastError("PODCAST_SCRIPT_INVALID")
    if not isinstance(script["provider"], str) or not script["provider"]:
        raise PodcastError("PODCAST_SCRIPT_INVALID")
    return deepcopy(script)
