from sqlalchemy import select

from .tables import CardSetMove, CardSet


def moved_to(session, owner, card_set_ids):
    """{舊卡組: 新版卡組}，只列仍存在的新版卡組。"""
    if not card_set_ids:
        return {}
    rows = session.execute(
        select(CardSetMove.source_card_set_id, CardSetMove.target_card_set_id)
        .join(CardSet, CardSet.card_set_id == CardSetMove.target_card_set_id)
        .where(
            CardSetMove.learner_id == owner,
            CardSetMove.source_card_set_id.in_(card_set_ids),
            CardSet.deleted_at.is_(None),
        )
    ).all()
    return {source: target for source, target in rows}


def card_origins(session, owner):
    """{(新卡組, 新概念): (最初的卡組, 最初的概念)}；承接後的卡片沿用同一張卡的身分。

    XP 與今日任務的「同一張卡每天只算一次」以此辨識，在新舊卡組各標一次仍只算一張。
    """
    parents = {}
    for target, source, concept_map in session.execute(
        select(
            CardSetMove.target_card_set_id,
            CardSetMove.source_card_set_id,
            CardSetMove.concept_map,
        ).where(CardSetMove.learner_id == owner)
    ).all():
        for new_concept, old_concept in concept_map.items():
            parents[(target, new_concept)] = (source, old_concept)
    roots = {}
    for key in parents:
        root, seen = key, set()
        while root in parents and root not in seen:
            seen.add(root)
            root = parents[root]
        roots[key] = root
    return roots
