"""難度評估 v2：短編號綁定完整概念，依學習順序分段。"""
from __future__ import annotations

import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from runtime.semantic_service import request_semantics, semantic_client
from runtime.text_budget import estimate_o200k_tokens, estimate_prompt_tokens


class DifficultyValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    level: Literal["standard", "hard"]
    reason: str = Field(max_length=48)


def response_schema(ids):
    return {"type": "object", "properties": {identity: {"$ref": "#/$defs/difficulty"} for identity in ids},
            "required": list(ids), "additionalProperties": False,
            "$defs": {"difficulty": DifficultyValue.model_json_schema()}}


def difficulty_batches(document, runtime_lock, *, max_calls=5):
    """先規劃完整請求才推論；容量不足不送出、不截原文、不只挑有作答的概念。"""
    contract = runtime_lock["concept_difficulty"]
    context = min(65536, runtime_lock["semantic_service"]["max_model_len"])
    concepts = {c["concept_id"]: c for c in document["concepts"]}
    order = [step["concept_id"] for step in document["initial_learning_path"]]
    local_ids = {identity: f"c{index + 1}" for index, identity in enumerate(order)}
    prerequisites = {identity: [] for identity in order}
    for relation in document["relations"]:
        if relation["type"] == "prerequisite":
            prerequisites[relation["target_concept_id"]].append(concepts[relation["source_concept_id"]]["label"])
    prompt_tokens = estimate_prompt_tokens(contract["prompt"], {"concepts": []}) + 256
    chunks, current, current_tokens = [], [], prompt_tokens
    for identity in order:
        concept = concepts[identity]
        item = {"id": local_ids[identity], "label": concept["label"],
                "points": [claim["text"] for claim in concept["claims"]],
                "prerequisites": prerequisites[identity]}
        size = estimate_o200k_tokens(json.dumps(item, ensure_ascii=False)) + 24
        # 每概念最壞輸出約 96 tokens（48 字理由＋JSON）；最低仍留 4K。
        output = max(4096, (len(current) + 1) * 96)
        if current and (current_tokens + size + output > context or output > contract["max_tokens"]):
            chunks.append(current)
            current, current_tokens = [], prompt_tokens
            output = 4096
        if current_tokens + size + output > context:
            raise ValueError("DIFFICULTY_INPUT_TOO_LARGE")
        current.append(item)
        current_tokens += size
    if current:
        chunks.append(current)
    if len(chunks) > max_calls:
        raise ValueError("DIFFICULTY_CALL_BUDGET_EXCEEDED")
    reverse = {short: identity for identity, short in local_ids.items()}
    result = []
    for items in chunks:
        payload = {"concepts": items}
        schema = response_schema([item["id"] for item in items])
        input_tokens = (estimate_prompt_tokens(contract["prompt"], payload)
                        + estimate_o200k_tokens(json.dumps(schema, ensure_ascii=False)))
        reserve = max(4096, len(items) * 96)
        if input_tokens + reserve > context or reserve > contract["max_tokens"]:
            raise ValueError("DIFFICULTY_INPUT_TOO_LARGE")
        result.append({"request": payload, "response_schema": schema, "mapping": {item["id"]: reverse[item["id"]] for item in items},
                       "input_tokens": input_tokens, "output_reserve": reserve})
    return result


def evaluate_batch(batch, runtime_lock, *, semantic_call=request_semantics, cancellation_check=None):
    with semantic_client() as client:
        raw = semantic_call(client, runtime_lock=runtime_lock, task="concept_difficulty",
            request=batch["request"], response_schema=batch["response_schema"],
            cancellation_check=cancellation_check)
    if not isinstance(raw, dict) or set(raw) != set(batch["mapping"]):
        raise ValueError("DIFFICULTY_OUTPUT_INVALID")
    concepts = []
    for short, identity in batch["mapping"].items():
        value = DifficultyValue.model_validate(raw[short])
        if value.level == "hard" and not value.reason.strip():
            raise ValueError("DIFFICULTY_OUTPUT_INVALID")
        concepts.append({"concept_id": identity, "level": value.level,
                         "reason": value.reason or "本次評估未列為偏難。"})
    return concepts


def generate_difficulty(document, runtime_lock, *, semantic_call=request_semantics,
                        cancellation_check=None, max_calls=5):
    batches = difficulty_batches(document, runtime_lock, max_calls=max_calls)
    concepts = []
    for batch in batches:
        concepts.extend(evaluate_batch(batch, runtime_lock, semantic_call=semantic_call,
                                       cancellation_check=cancellation_check))
    return {"concepts": concepts}
