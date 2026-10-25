"""獨立來源子圖：模型只使用本地索引，全域身分及來源關係由程式維護。"""

from copy import deepcopy
from .semantic_projection import (
    SemanticState,
    semantic_request,
    semantic_response_schema,
    apply_semantic_response,
    _expand_claim,
    _project_claim,
    spoken_handles,
)
from content_identity import canonical_sha256

POLICY = "independent-evidence-blocks/v1"


def request_view(request):
    return {
        key: request[key]
        for key in ("sections", "source_pages", "source_policy", "learning_scopes", "spoken_sources")
        if key in request
    }


def block_request(context, bundle):
    return request_view(semantic_request(context, bundle, SemanticState()))


def block_schema(handles, *, excerpt_handles=frozenset()):
    excerpt_handles = set(handles) & set(excerpt_handles)
    schema = semantic_response_schema(handles)
    concept = schema["properties"]["concepts"]["items"]
    concept["required"].remove("k")
    concept["properties"].pop("k")
    concept["properties"]["l"]["maxLength"] = 256
    concept["properties"]["a"]["items"]["maxLength"] = 256
    concept["properties"]["c"]["minItems"] = 1
    concept["properties"]["c"]["items"]["properties"]["m"]["type"] = "string"
    concept["properties"]["c"]["items"]["properties"]["m"]["maxLength"] = 4096
    if excerpt_handles:
        excerpt = {
            "type": "object", "additionalProperties": False,
            "required": ["e", "q"],
            "properties": {
                "e": {"type": "integer", "enum": [h for h in handles if h in excerpt_handles]},
                "q": {"type": "string", "minLength": 1},
            },
        }
        whole = [h for h in handles if h not in excerpt_handles]
        concept["properties"]["c"]["items"]["properties"]["s"]["items"] = (
            {"anyOf": [{"type": "integer", "enum": whole}, excerpt]} if whole else excerpt
        )
    relation = schema["properties"]["relations"]["items"]
    for key in ("s", "t"):
        relation["properties"][key] = {"type": "integer", "minimum": 0}
    relation["properties"]["e"]["items"] = {"type": "integer", "enum": handles}
    relation["properties"]["r"]["maxLength"] = 1024
    return schema


def project_block(context, bundle, identity, response):
    if (
        not isinstance(response, dict)
        or set(response) != {"concepts", "relations"}
        or not isinstance(response["concepts"], list)
        or not isinstance(response["relations"], list)
    ):
        raise ValueError("SEMANTIC_OUTPUT_INVALID")

    def key(index):
        return canonical_sha256({"block": identity, "index": index})

    concepts, relations = [], []
    for index, concept in enumerate(response["concepts"]):
        if not isinstance(concept, dict) or set(concept) != {"l", "a", "c"}:
            raise ValueError("SEMANTIC_OUTPUT_INVALID")
        concepts.append({"k": key(index), **concept})
    for relation in response["relations"]:
        if (
            not isinstance(relation, dict)
            or set(relation) != {"s", "t", "k", "r", "e", "c"}
            or type(relation["s"]) is not int
            or type(relation["t"]) is not int
        ):
            raise ValueError("SEMANTIC_OUTPUT_INVALID")
        # 越界索引不會變成有效概念，仍由既有關係檢核拒絕。
        relations.append({**relation, "s": key(relation["s"]), "t": key(relation["t"])})
    result = SemanticState()
    apply_semantic_response(
        {"concepts": concepts, "relations": relations},
        context=context,
        bundle={**bundle, "catalog_keys": []},
        state=result,
    )
    return result


def repair_excerpt_quotes(response, context, bundle, call):
    """只請模型重選無法回查的口語引用一次，不猜字、不改寫已生成的主張。"""
    handles = spoken_handles(context)
    excerpt_evidence_ids = {context["evidence"][h]["evidence_id"] for h in handles}
    evidence = {e["evidence_id"]: e for e in bundle["evidence"]}
    pending, targets = {}, {}
    for ci, concept in enumerate(response["concepts"]):
        for qi, claim in enumerate(concept["c"]):
            if not isinstance(claim, dict) or set(claim) != {"m", "s"} or not isinstance(claim["s"], list):
                continue
            if not any(isinstance(ref, dict) or (type(ref) is int and ref in handles) for ref in claim["s"]):
                continue
            expanded = _expand_claim(claim, context["evidence"], excerpt_handles=handles)
            if _project_claim(expanded, evidence, excerpts=True, excerpt_evidence_ids=excerpt_evidence_ids) is not None:
                continue
            key = f"claim_{ci}_{qi}"
            pending[key] = {"meaning": claim["m"], "previous_sources": claim["s"]}
            targets[key] = (ci, qi)
    if not pending:
        return response
    request = block_request(context, bundle)
    rows = [row for section in request["sections"] for row in section["evidence"]]
    source_schema = deepcopy(block_schema(
        [row[0] for row in rows], excerpt_handles=handles
    )["properties"]["concepts"]["items"]["properties"]["c"]["items"]["properties"]["s"])
    # 找不到支持時回空陣列，仍由投影拒收並留下 needs_review，不能造引用湊成功。
    source_schema["minItems"] = 0
    corrected = call(
        {"quote_corrections": pending, "evidence": rows,
         "source_pages": request.get("source_pages", []),
         "spoken_sources": request.get("spoken_sources", {})},
        {"type": "object", "additionalProperties": False, "required": list(pending),
         "properties": {key: source_schema for key in pending}},
    )
    if not isinstance(corrected, dict) or set(corrected) != set(pending):
        raise ValueError("SEMANTIC_OUTPUT_INVALID")
    result = deepcopy(response)
    for key, (ci, qi) in targets.items():
        result["concepts"][ci]["c"][qi]["s"] = corrected[key]
    return result


def merge_block(state, block):
    if any(key in state.concepts for key in block.concepts):
        raise ValueError("ANALYSIS_CHECKPOINT_INVALID")
    state.concepts.update(block.concepts)
    state.relations.extend(block.relations)
    for field in ("rejected_claims", "rejected_relations", "literal_repairs"):
        setattr(state, field, getattr(state, field) + getattr(block, field))
    # 不覆寫原有概念或清除人工複核提示；跨塊同義名稱允許分開保留。
    state.source_review_required |= block.source_review_required
