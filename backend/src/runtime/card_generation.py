"""可續接的整卡內容與版面設計；Luna 暫用與 Gemma 正常通道共用語意入口。"""

from copy import deepcopy
from datetime import UTC, datetime
import json
import logging
import time
from uuid import uuid4

from sqlalchemy import or_, select

from content_identity import canonical_sha256
from . import card_design as design, luna_calls, luna_test
from .local_ai_config import configured_lock
from .generation_jobs import WORKER_LEASE_TTL, check_job, lease_is_current
from .semantic_service import request_semantics, semantic_client, SemanticServiceError
from .text_budget import estimate_o200k_tokens
from .storage import card_sources, knowledge_reads
from .storage.tables import CardSet, Material
from .storage.database import database_session


def provider_identity(lock):
    return {
        key: lock["semantic_service"][key]
        for key in ("model_id", "revision", "api_protocol")
    }


def initial(results=None, previous=None):
    provider = provider_identity(configured_lock())
    results = {
        identity: result
        for identity, result in (results or {}).items()
        if result.get("design", {}).get("schema") == design.POLICY
        and result["design"].get("provider") == provider
    }
    history = list((previous or {}).get("previous_usage", []))
    if previous and previous.get("calls"):
        history.append(
            {
                key: previous.get(key)
                for key in (
                    "policy",
                    "provider",
                    "calls",
                    "estimated_input_tokens",
                    "saved_output_tokens",
                    "elapsed_seconds",
                    "reported_usage",
                )
            }
        )
    return {
        "policy": design.POLICY,
        "provider": provider,
        "stage": "preparing",
        "luna_attempt": str(uuid4()),
        "results": results,
        "calls": 0,
        "estimated_input_tokens": 0,
        "saved_output_tokens": 0,
        "elapsed_seconds": 0,
        "error_code": None,
        "previous_usage": history,
    }


def progress(row):
    state = row.generation or {}
    results = state.get("results", {})
    return {
        "status": row.status,
        "stage": state.get("stage", "complete"),
        "completed": sum(identity in results or identity in state.get("skipped", {})
            for identity in row.concept_ids),
        "total": len(row.concept_ids),
        "error_code": state.get("error_code"),
        "retained_original": sum(item.get("rejected", 0) for item in results.values()),
        "cached": sum(bool(item.get("cached")) for item in results.values()),
    }


def _claim(dsn):
    now = datetime.now(UTC)
    with database_session(dsn) as db:
        row = db.scalar(
            select(CardSet)
            .join(Material, Material.material_id == CardSet.material_id)
            .where(
                CardSet.deleted_at.is_(None),
                Material.discard_requested_at.is_(None),
                or_(
                    CardSet.status == "pending",
                    (CardSet.status == "running") & (CardSet.lease_expires_at < now),
                ),
            )
            .order_by(CardSet.created_at)
            .with_for_update(of=CardSet, skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        row.status = "running"
        if "luna_attempt" not in row.generation:
            row.generation = {**row.generation, "luna_attempt": str(uuid4())}
        row.lease_token = uuid4()
        row.lease_expires_at = now + WORKER_LEASE_TTL
        return {
            "id": row.card_set_id,
            "owner": row.learner_id,
            "material_id": row.material_id,
            "revision": row.knowledge_structure_revision,
            "concept_ids": list(row.concept_ids),
            "token": row.lease_token,
            "state": deepcopy(row.generation),
        }


def _save(claim, state, status, dsn):
    with database_session(dsn) as db:
        row = db.scalar(
            select(CardSet).where(CardSet.card_set_id == claim["id"]).with_for_update()
        )
        material = db.get(Material, claim["material_id"])
        if (
            row is None
            or row.deleted_at is not None
            or row.status != "running"
            or not lease_is_current(row, claim["token"])
            or material is None
            or material.discard_requested_at is not None
        ):
            return
        if status == "ready" and state.get("skipped"):
            state["selected_concept_ids"] = list(row.concept_ids)
            row.concept_ids = [identity for identity in row.concept_ids
                if identity in state["results"]]
        row.generation = state
        row.status = status
        row.lease_token = None
        row.lease_expires_at = None
        if status == "ready":
            row.page_counts = {
                identity: len(state["results"][identity]["design"]["pages"])
                for identity in row.concept_ids
            }
            row.card_count = sum(row.page_counts.values())
    if status in {"ready", "failed"}:
        luna_calls.completed("failed" if status == "failed" else "succeeded", state.get("error_code"))


def _inputs(claim, identity, dsn):
    with database_session(dsn) as db:
        row = db.get(CardSet, claim["id"])
        meta = knowledge_reads.source_metadata(
            db, claim["owner"], claim["material_id"], claim["revision"]
        )
        batch = card_sources.batch(db, row, meta, row.concept_ids.index(identity), 1)
        card = batch["cards"][0]
        payload, bindings = design.source_input(card, batch["context"])
        return payload, bindings, len(card["claims"])


def _prepare(claim, state, dsn):
    with database_session(dsn) as db:
        cached = (
            []
            if state.get("force_regenerate")
            else db.scalars(
                select(CardSet.generation).where(
                    CardSet.learner_id == claim["owner"],
                    CardSet.material_id == claim["material_id"],
                    CardSet.knowledge_structure_revision == claim["revision"],
                    CardSet.status == "ready",
                    CardSet.deleted_at.is_(None),
                    CardSet.card_set_id != claim["id"],
                )
            ).all()
        )
        for old in cached:
            if (
                old.get("policy") != design.POLICY
                or old.get("provider") != state["provider"]
            ):
                continue
            for identity, result in old.get("results", {}).items():
                if (
                    identity in claim["concept_ids"]
                    and identity not in state["results"]
                    and result.get("design")
                ):
                    design.checked_design(result["design"])
                    state["results"][identity] = {**result, "cached": True}
    remaining = [
        identity
        for identity in claim["concept_ids"]
        if identity not in state["results"] and identity not in state.get("skipped", {})
    ]
    if not remaining:
        state["stage"] = "complete"
        return
    state["current"] = {"concept_id": remaining[0]}
    state["stage"] = "designing"


def skip_rejected(state, concept_ids):
    """有可發布成果或尚有其他概念時繼續；全數不符仍保留最後候選供重試。"""
    current = state["current"]
    identity = current["concept_id"]
    if not state["results"] and not any(
        candidate != identity and candidate not in state.get("skipped", {})
        for candidate in concept_ids
    ):
        return False
    state.setdefault("skipped", {})[identity] = {
        "error_code": "CARD_DESIGN_REVIEW_FAILED",
        "issues": list(current["feedback"]),
    }
    del state["current"]
    state["stage"] = "preparing"
    return True


def _request(claim, state, prompt, payload, schema, dsn, call):
    lock = configured_lock()
    if provider_identity(lock) != state["provider"]:
        raise design.CardDesignError("CARD_PROVIDER_CHANGED")
    estimate = estimate_o200k_tokens(
        prompt + "\nINPUT:\n" + json.dumps(payload, ensure_ascii=False)
    )
    estimate += estimate_o200k_tokens(json.dumps(schema, ensure_ascii=False))
    if estimate > 65536 - 8192:
        raise design.CardDesignError("CARD_CONTEXT_TOO_LARGE")
    started = time.monotonic()
    usage = {}
    check = lambda: check_job(CardSet, claim["id"], claim["token"], dsn=dsn)
    check()
    state["calls"] += 1
    state["estimated_input_tokens"] += estimate
    try:
        if call is not None:
            result = call(
                "/semantics",
                {
                    "task": "concept_card",
                    "prompt": prompt,
                    "request": payload,
                    "schema": schema,
                },
            )
        else:
            # 僅接共用語意邊界；Pod／SSH 與 endpoint 設定由既有外層處理。
            lock["concept_card"] = {
                "prompt": prompt,
                "max_tokens": 8192,
                "generation": deepcopy(lock["material_semantics"]["generation"]),
            }
            with semantic_client() as client:
                result = request_semantics(
                    client,
                    runtime_lock=lock,
                    task="concept_card",
                    request=payload,
                    response_schema=schema,
                    cancellation_check=check,
                    usage=usage,
                )
    finally:
        state["elapsed_seconds"] = round(
            state["elapsed_seconds"] + time.monotonic() - started, 3
        )
        if usage:
            recorded = state.setdefault("reported_usage", {"calls": 0})
            recorded["calls"] += 1
            for key, value in usage.items():
                recorded[key] = recorded.get(key, 0) + value
    check()
    state["saved_output_tokens"] += estimate_o200k_tokens(
        json.dumps(result, ensure_ascii=False)
    )
    return luna_calls.capture(result) if call is not None else result


def _advance(claim, state, dsn, call):
    if state["stage"] == "preparing":
        _prepare(claim, state, dsn)
        return
    current = state["current"]
    payload, bindings, primary_count = _inputs(claim, current["concept_id"], dsn)
    digest = canonical_sha256(payload)
    if state["stage"] == "designing":
        request = {
            **payload,
            **(
                {"previous_issues": current["feedback"]}
                if current.get("feedback")
                else {}
            ),
            **(
                {"previous_design": current["plan"]}
                if current.get("feedback") and current.get("plan")
                else {}
            ),
        }
        result = _request(
            claim, state, design.DESIGN_PROMPT, request, design.DESIGN_SCHEMA, dsn, call
        )
        # 修稿必須看得到實際失敗的候選；不可只留下上一次通過格式的舊稿。
        current["plan"] = result
        try:
            current["plan"] = design.validate_plan(result, len(bindings), primary_count)
        except design.CardDesignError as error:
            current["feedback"] = [error.detail or "請修正卡面格式與來源對應。"]
            if not current.get("repair_attempts"):
                current["repair_attempts"] = 1
                return
            raise
        current["source_digest"] = digest
        state["stage"] = "checking"
    elif state["stage"] == "checking":
        if digest != current["source_digest"]:
            raise design.CardDesignError("CARD_SOURCE_CHANGED")
        review = _request(
            claim,
            state,
            design.REVIEW_PROMPT,
            {**payload, "design": current["plan"]},
            design.REVIEW_SCHEMA,
            dsn,
            call,
        )
        if (
            not isinstance(review, dict)
            or set(review) != {"supported", "core_preserved", "issues"}
            or type(review["supported"]) is not bool
            or type(review["core_preserved"]) is not bool
            or not isinstance(review["issues"], list)
            or any(not isinstance(s, str) for s in review["issues"])
        ):
            raise design.CardDesignError("CARD_DESIGN_INVALID")
        if not review["supported"] or not review["core_preserved"] or review["issues"]:
            current["feedback"] = review["issues"] or [
                "請保留原文支持的核心機制與必要條件。"
            ]
            state["stage"] = "designing"
            if not current.get("repair_attempts"):
                current["repair_attempts"] = 1
                return
            if skip_rejected(state, claim["concept_ids"]):
                return
            raise design.CardDesignError("CARD_DESIGN_REVIEW_FAILED")
        state["results"][current["concept_id"]] = {
            "design": design.resolve_plan(current["plan"], bindings, state["provider"]),
            "review": review,
            "source_digest": digest,
            "cached": False,
            "repair_attempts": current.get("repair_attempts", 0),
            "repair_notes": current.get("feedback", []),
        }
        del state["current"]
        state["stage"] = "preparing"
    else:
        raise design.CardDesignError("CARD_DESIGN_INVALID")


def step(*, dsn=None, call=None):
    claim = _claim(dsn)
    if claim is None:
        return False
    state = deepcopy(claim["state"])
    if state.get("policy") != design.POLICY:
        previous_attempt = state["luna_attempt"]
        state = {**initial(previous=state), "luna_attempt": previous_attempt}
    trace = luna_calls.start(
        "cards", claim["id"], state["luna_attempt"], claim["material_id"], claim["owner"],
        enabled=luna_test.enabled(configured_lock()),
    )
    try:
        _advance(claim, state, dsn, call)
        _save(claim, state, "ready" if state["stage"] == "complete" else "pending", dsn)
    except Exception as error:
        code = (
            str(error)
            if isinstance(error, (design.CardDesignError, SemanticServiceError))
            else "CARD_GENERATION_FAILED"
        )
        state["error_code"] = code
        luna_calls.rejected({"reason": str(error), "current": state.get("current")})
        try:
            _save(claim, state, "failed", dsn)
        except Exception:
            failed = deepcopy(claim["state"])
            for key in (
                "calls",
                "estimated_input_tokens",
                "saved_output_tokens",
                "elapsed_seconds",
            ):
                failed[key] = state.get(key, 0)
            failed["error_code"] = code
            _save(claim, failed, "failed", dsn)
        logging.getLogger(__name__).warning(
            "CARD_GENERATION_FAILED type=%s", type(error).__name__
        )
    finally:
        luna_calls.close(trace)
    return True
