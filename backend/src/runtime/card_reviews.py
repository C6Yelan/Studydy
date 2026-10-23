"""概念卡學習標記：學生自己把卡片標成不熟或熟悉（forgot／remembered）。

不排程、不設到期日；標成不熟的卡片列入「需要加強」。每次標記另存事件，
供經驗值、今日任務與徽章統計。卡片內容仍讀自卡組固定版本的已驗證來源。
"""

from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from content_identity import canonical_sha256
from .card_sets import CardSetError, _owned_card
from .storage.artifacts import _key_digest
from .storage.database import database_session
from .storage.knowledge_reads import read_concepts, source_metadata
from .storage.tables import CardReviewEvent, CardReviewState

RATINGS = ("forgot", "remembered")
WEAK_LIMIT = 20
TIMEZONE = ZoneInfo("Asia/Taipei")


def _event_view(event):
    return {
        "schema": "card-review/v2",
        "card_set_id": event.card_set_id,
        "concept_id": event.concept_id,
        "rating": event.rating,
        "review_count": event.review_count,
        # 一律以 UTC 輸出，首次回應與重送讀回的結果完全相同。
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
        # 另一個分頁已標記過這張卡時拒絕，避免以過期畫面覆蓋較新的標記。
        if (state.review_count if state else 0) != expected_review_count:
            raise CardSetError("CARD_REVIEW_CONFLICT")
        now = now or datetime.now(TIMEZONE)
        if state is None:
            state = CardReviewState(card_set_id=card_set_id, concept_id=concept_id, learner_id=owner, review_count=0)
            session.add(state)
        state.review_count += 1
        state.last_rating, state.last_reviewed_at = rating, now
        event = CardReviewEvent(
            review_event_id=uuid4(), learner_id=owner, card_set_id=card_set_id,
            concept_id=concept_id, rating=rating, review_count=state.review_count,
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
          -- 已移到新版的舊卡組不再列出，改由新版卡組沿用標記（runtime.version_moves）。
          AND NOT EXISTS (
              SELECT 1 FROM card_set_moves v
              JOIN card_sets t ON t.card_set_id = v.target_card_set_id
              WHERE v.source_card_set_id = s.card_set_id AND t.deleted_at IS NULL
          )
          AND (CAST(:card_set AS uuid) IS NULL OR s.card_set_id = CAST(:card_set AS uuid))
          AND (CAST(:material AS uuid) IS NULL OR s.material_id = CAST(:material AS uuid))
    )
"""


def review_summary(owner, *, card_set_id=None, material_id=None, limit=WEAK_LIMIT, dsn=None):
    """各卡組的標記張數與需要加強（標成不熟）的卡片，較久沒標記的在前。

    指定卡組時另回傳該卡組每張已標記卡片的標記，供瀏覽時顯示與修改。
    指定教材時只計這份教材的卡組（需要加強的張數上限套用在這份教材內）。
    """
    values = {
        "owner": owner,
        "card_set": str(card_set_id) if card_set_id else None,
        "material": str(material_id) if material_id else None,
    }
    with database_session(dsn) as session:
        if card_set_id is not None:
            _owned_card(session, owner, card_set_id, read=True)
        counts = session.execute(text(_CARDS + """
            SELECT cards.card_set_id, count(*),
                   count(*) FILTER (WHERE r.last_rating = 'forgot'),
                   count(*) FILTER (WHERE r.last_rating = 'remembered')
            FROM cards
            LEFT JOIN card_review_states r ON r.card_set_id = cards.card_set_id AND r.concept_id = cards.concept_id
            GROUP BY cards.card_set_id
        """), values).all()
        picked = session.execute(text(_CARDS + """
            SELECT cards.card_set_id, cards.name, cards.material_id, cards.display_name,
                   cards.revision, cards.concept_id, r.review_count
            FROM cards
            JOIN card_review_states r ON r.card_set_id = cards.card_set_id AND r.concept_id = cards.concept_id
            WHERE r.last_rating = 'forgot'
            ORDER BY r.last_reviewed_at, cards.card_set_id, cards.position
            LIMIT :limit
        """), {**values, "limit": limit}).all()
        marks = session.execute(text(_CARDS + """
            SELECT cards.card_set_id, cards.concept_id, r.last_rating, r.review_count
            FROM cards
            JOIN card_review_states r ON r.card_set_id = cards.card_set_id AND r.concept_id = cards.concept_id
            ORDER BY cards.position
        """), values).all() if card_set_id is not None else []
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
        return {
            "schema": "card-reviews/v2",
            "forgot_count": sum(row[2] for row in counts),
            "sets": [
                {"card_set_id": row[0], "card_count": row[1], "forgot_count": row[2], "remembered_count": row[3]}
                for row in counts
            ],
            "marks": [
                {"card_set_id": row[0], "concept_id": row[1], "rating": row[2], "review_count": row[3]}
                for row in marks
            ],
            "cards": [
                {
                    "card_set_id": item.card_set_id,
                    "card_set_name": item.name,
                    "material_id": item.material_id,
                    "material_name": item.display_name,
                    "knowledge_structure_revision": item.revision,
                    "source_resolver": resolvers[(item.material_id, item.revision)],
                    "review_count": item.review_count,
                    "card": content[(item.material_id, item.revision, item.concept_id)],
                }
                for item in picked
            ],
        }
