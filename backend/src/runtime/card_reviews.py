"""概念卡間隔重複：依「忘了／模糊／記得」排定下次複習，以台灣日期為界。

排程只用三個評分與固定階梯，學生看得懂下次何時再見；每次評分另存事件，
供之後的學習活動統計使用。卡片內容仍讀自卡組固定版本的已驗證來源。
"""

from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from content_identity import canonical_sha256
from .card_sets import CardSetError, _owned_card
from .storage.artifacts import _key_digest
from .storage.database import database_session
from .storage.knowledge_reads import read_concepts, source_metadata
from .storage.tables import CardReviewEvent, CardReviewState

RULE_VERSION = "card-review/v1"
RATINGS = ("forgot", "fuzzy", "remembered")
# 連續記得的次數決定下一次間隔（天）。
LADDER = (1, 3, 7, 14, 30, 60, 120, 240)
RELEARN = timedelta(minutes=10)
QUEUE_LIMIT = 20
TIMEZONE = ZoneInfo("Asia/Taipei")


def _due_on(now, days):
    """到期時間取台灣日期 days 天後的 00:00，學生在那天任何時候打開都看得到。"""
    day = now.astimezone(TIMEZONE).date() + timedelta(days=days)
    return datetime.combine(day, time(), TIMEZONE)


def schedule(state, rating, now):
    """回傳 (streak, interval_days, due_at)；state 為 None 表示第一次複習。"""
    streak = state.streak if state else 0
    interval = state.interval_days if state else 0
    if rating == "forgot":
        return 0, 0, now + RELEARN
    if rating == "fuzzy":
        interval = max(1, interval // 2)
        return streak, interval, _due_on(now, interval)
    interval = LADDER[min(streak, len(LADDER) - 1)]
    return streak + 1, interval, _due_on(now, interval)


def _event_view(event):
    return {
        "schema": "card-review/v1",
        "card_set_id": event.card_set_id,
        "concept_id": event.concept_id,
        "rating": event.rating,
        "review_count": event.review_count,
        "interval_days": event.interval_days,
        # 一律以 UTC 輸出，首次回應與重送讀回的結果完全相同。
        "due_at": event.due_at.astimezone(UTC),
        "reviewed_at": event.created_at.astimezone(UTC),
    }


def submit_review(owner, card_set_id, concept_id, rating, expected_review_count, key, *, dsn=None, now=None):
    if rating not in RATINGS:
        raise CardSetError("REQUEST_INVALID")
    digest = _key_digest(key)
    fingerprint = bytes.fromhex(canonical_sha256({
        "card_set_id": str(card_set_id), "concept_id": concept_id,
        "rating": rating, "expected_review_count": expected_review_count,
    }))
    with database_session(dsn) as session:
        # 先鎖教材與卡組，與卡組編輯、刪除及教材清除使用同一順序。
        row, _ = _owned_card(session, owner, card_set_id)
        existing = session.scalar(select(CardReviewEvent).where(
            CardReviewEvent.learner_id == owner,
            CardReviewEvent.idempotency_key_sha256 == digest,
        ))
        if existing is not None:
            if bytes(existing.request_fingerprint) != fingerprint:
                raise CardSetError("IDEMPOTENCY_CONFLICT")
            return _event_view(existing)
        if row.status != "ready" or concept_id not in row.concept_ids:
            raise CardSetError("RESOURCE_NOT_FOUND")
        state = session.get(CardReviewState, (card_set_id, concept_id), with_for_update=True)
        # 另一個分頁已評過這張卡時拒絕，避免同一次複習被算兩次。
        if (state.review_count if state else 0) != expected_review_count:
            raise CardSetError("CARD_REVIEW_CONFLICT")
        now = now or datetime.now(TIMEZONE)
        streak, interval, due_at = schedule(state, rating, now)
        if state is None:
            state = CardReviewState(card_set_id=card_set_id, concept_id=concept_id, learner_id=owner, review_count=0)
            session.add(state)
        state.streak, state.interval_days, state.due_at = streak, interval, due_at
        state.review_count += 1
        state.last_rating, state.last_reviewed_at = rating, now
        event = CardReviewEvent(
            review_event_id=uuid4(), learner_id=owner, card_set_id=card_set_id,
            concept_id=concept_id, rating=rating, rule_version=RULE_VERSION,
            review_count=state.review_count, interval_days=interval, due_at=due_at,
            idempotency_key_sha256=digest, request_fingerprint=fingerprint, created_at=now,
        )
        session.add(event)
        session.flush()
        return _event_view(event)


_CARDS = """
    WITH cards AS (
        SELECT s.card_set_id, s.name, s.version, s.material_id, m.display_name,
               s.knowledge_structure_revision AS revision, s.created_at,
               c.concept_id, c.position
        FROM card_sets s
        JOIN materials m ON m.learner_id = s.learner_id AND m.material_id = s.material_id
        CROSS JOIN LATERAL unnest(s.concept_ids) WITH ORDINALITY AS c(concept_id, position)
        WHERE s.learner_id = :owner AND s.deleted_at IS NULL AND s.status = 'ready'
          AND m.discard_requested_at IS NULL
          AND (CAST(:card_set AS uuid) IS NULL OR s.card_set_id = CAST(:card_set AS uuid))
    )
"""


def review_queue(owner, *, card_set_id=None, limit=QUEUE_LIMIT, dsn=None, now=None):
    """到期卡依到期先後，其後是從未複習的新卡；另回傳每個卡組的張數。"""
    now = now or datetime.now(TIMEZONE)
    values = {"owner": owner, "card_set": str(card_set_id) if card_set_id else None, "now": now}
    with database_session(dsn) as session:
        if card_set_id is not None:
            _owned_card(session, owner, card_set_id, read=True)
        counts = session.execute(text(_CARDS + """
            SELECT cards.card_set_id,
                   count(*) FILTER (WHERE r.due_at <= :now),
                   count(*) FILTER (WHERE r.card_set_id IS NULL),
                   count(r.card_set_id),
                   min(r.due_at) FILTER (WHERE r.due_at > :now)
            FROM cards
            LEFT JOIN card_review_states r ON r.card_set_id = cards.card_set_id AND r.concept_id = cards.concept_id
            GROUP BY cards.card_set_id
        """), values).all()
        picked = session.execute(text(_CARDS + """
            SELECT cards.card_set_id, cards.name, cards.version, cards.material_id, cards.display_name,
                   cards.revision, cards.concept_id, r.review_count, r.last_rating, r.due_at
            FROM cards
            LEFT JOIN card_review_states r ON r.card_set_id = cards.card_set_id AND r.concept_id = cards.concept_id
            WHERE r.card_set_id IS NULL OR r.due_at <= :now
            ORDER BY (r.card_set_id IS NULL), r.due_at, cards.created_at DESC, cards.card_set_id, cards.position
            LIMIT :limit
        """), {**values, "limit": limit}).all()
        groups = {}
        for item in picked:
            groups.setdefault((item.material_id, item.revision), []).append(item.concept_id)
        content, resolvers = {}, {}
        for (material_id, revision), identities in groups.items():
            resolvers[(material_id, revision)] = source_metadata(session, owner, material_id, revision)["source_resolver"]
            for row in read_concepts(session, owner, material_id, revision, list(dict.fromkeys(identities))):
                concept = row.payload["concept"]
                content[(material_id, revision, row.concept_id)] = {
                    key: concept[key] for key in ("concept_id", "label", "claims")
                }
        upcoming = [row[4] for row in counts if row[4] is not None]
        return {
            "schema": "card-review-queue/v1",
            "rule_version": RULE_VERSION,
            "due_count": sum(row[1] for row in counts),
            "new_count": sum(row[2] for row in counts),
            "next_due_at": min(upcoming) if upcoming else None,
            "sets": [
                {"card_set_id": row[0], "due_count": row[1], "new_count": row[2], "reviewed_count": row[3]}
                for row in counts
            ],
            "cards": [
                {
                    "card_set_id": item.card_set_id,
                    "card_set_name": item.name,
                    "material_id": item.material_id,
                    "material_name": item.display_name,
                    "knowledge_structure_revision": item.revision,
                    "source_resolver": resolvers[(item.material_id, item.revision)],
                    "state": "new" if item.review_count is None else "due",
                    "review_count": item.review_count or 0,
                    "last_rating": item.last_rating,
                    "due_at": item.due_at,
                    "card": content[(item.material_id, item.revision, item.concept_id)],
                }
                for item in picked
            ],
        }
