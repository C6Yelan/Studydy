"""刪除前保留不含內容的每日計量；成就與慶祝由帳號持久保存。"""
import json
from collections import defaultdict
from zoneinfo import ZoneInfo

from sqlalchemy import text


def retained_days(db, owner):
    return db.execute(text(
        'SELECT source_kind, source_id, day, metrics FROM learning_retained_days WHERE learner_id=:owner'
    ), {'owner': owner}).all()


def preserve_learning(db, owner, kind, identity):
    from . import learning_activity as activity
    from .storage.card_moves import card_origins
    zone = ZoneInfo(activity.TIMEZONE)
    # 先記下仍可從內容判定的已取得徽章；之後只保存無內容的計分資料。
    activity.learning_activity(db, owner)
    values = {'owner': owner, 'identity': identity, 'zone': activity.TIMEZONE}
    daily = defaultdict(dict)
    if kind == 'material':
        for concept, at in activity._concept_completions(db, owner).items():
            if concept[0] == identity:
                data = daily[at.astimezone(zone).date()]
                data['concepts'] = data.get('concepts', 0) + 1
        events = db.execute(text('''
            SELECT e.created_at, e.is_correct, e.target_concept_id, e.semantic_identity
            FROM answer_events e JOIN study_sessions s ON s.study_session_id=e.study_session_id
            WHERE s.learner_id=:owner AND e.material_id=:identity
            ORDER BY e.created_at, e.event_number
        '''), values).all()
        seen = set()
        for at, correct, _, semantic in events:
            data = daily[at.astimezone(zone).date()]
            data['answers'] = data.get('answers', 0) + 1
            data['correct'] = data.get('correct', 0) + int(correct)
            if semantic not in seen:
                data['answer_xp'] = data.get('answer_xp', 0) + (activity.XP_CORRECT if correct else activity.XP_WRONG)
                seen.add(semantic)
        for day, _ in activity.overcome_difficulties([
            (at.astimezone(zone), correct, identity, concept, semantic)
            for at, correct, concept, semantic in events
        ]):
            daily[day]['difficulty_xp'] = daily[day].get('difficulty_xp', 0) + activity.XP_DIFFICULTY
        for milestone, day in db.execute(text('''
            SELECT kind, day FROM goal_milestones WHERE learner_id=:owner AND material_id=:identity
        '''), values):
            key = 'stages' if milestone == 'stage' else 'goal_xp'
            daily[day][key] = daily[day].get(key, 0) + (1 if milestone == 'stage' else activity.XP_GOAL_ON_TIME)
        for card_id in db.execute(text('SELECT card_set_id FROM card_sets WHERE learner_id=:owner AND material_id=:identity'), values).scalars():
            preserve_learning(db, owner, 'cards', card_id)
        for podcast_id in db.execute(text('SELECT podcast_id FROM podcasts WHERE learner_id=:owner AND material_id=:identity'), values).scalars():
            preserve_learning(db, owner, 'podcast', podcast_id)
    elif kind == 'cards':
        origins = card_origins(db, owner)
        for at, event_id, concept in db.execute(text('''
            SELECT created_at, review_event_id, concept_id FROM card_review_events
            WHERE learner_id=:owner AND card_set_id=:identity ORDER BY created_at, review_event_id
        '''), values):
            root_card, root_concept = origins.get((identity, concept), (identity, concept))
            daily[at.astimezone(zone).date()].setdefault('marks', []).append(
                [at.isoformat(), str(event_id), str(root_card), root_concept]
            )
    else:
        for day, seconds in db.execute(text('''
            SELECT listened_on, sum(listened_seconds) FROM podcast_listening_days
            WHERE learner_id=:owner AND podcast_id=:identity GROUP BY listened_on
        '''), values):
            daily[day]['seconds'] = int(seconds)
    for day, metrics in daily.items():
        db.execute(text('''
            INSERT INTO learning_retained_days (learner_id,source_kind,source_id,day,metrics)
            VALUES (:owner,:kind,:identity,:day,CAST(:metrics AS jsonb))
            ON CONFLICT DO NOTHING
        '''), {**values, 'kind': kind, 'day': day, 'metrics': json.dumps(metrics)})


def persist_awards(db, owner, badges, level):
    # 同帳號的初始化、領取和取得成就共用鎖，避免兩台各領一次。
    db.execute(text('SELECT learner_id FROM learners WHERE learner_id=:owner FOR UPDATE'), {'owner': owner})
    db.execute(text('''INSERT INTO learning_celebrations (learner_id) VALUES (:owner) ON CONFLICT DO NOTHING'''), {'owner': owner})
    baseline = db.execute(text('SELECT baseline_pending FROM learning_celebrations WHERE learner_id=:owner'), {'owner': owner}).scalar()
    for badge in badges['items']:
        if badge['earned']:
            db.execute(text('''
                INSERT INTO learning_awards (learner_id,badge_id,earned_on,seen)
                VALUES (:owner,:badge,:day,:seen) ON CONFLICT DO NOTHING
            '''), {'owner': owner, 'badge': badge['badge_id'], 'day': badge['earned_on'], 'seen': baseline})
    earned = dict(db.execute(text('SELECT badge_id,earned_on FROM learning_awards WHERE learner_id=:owner'), {'owner': owner}).all())
    for badge in badges['items']:
        if badge['badge_id'] in earned:
            badge['earned'] = True
            badge['earned_on'] = earned[badge['badge_id']]
            badge['progress'] = badge['target']
    if baseline:
        db.execute(text('UPDATE learning_awards SET seen=true WHERE learner_id=:owner'), {'owner': owner})
        db.execute(text('UPDATE learning_celebrations SET seen_level=:level,baseline_pending=false WHERE learner_id=:owner'), {'owner': owner, 'level': level})


def claim_celebrations(db, owner):
    from .learning_activity import learning_activity
    db.execute(text('SELECT learner_id FROM learners WHERE learner_id=:owner FOR UPDATE'), {'owner': owner})
    activity = learning_activity(db, owner)
    previous = db.execute(text('SELECT seen_level FROM learning_celebrations WHERE learner_id=:owner'), {'owner': owner}).scalar()
    current = activity['xp']['level']
    if current > previous:
        db.execute(text('UPDATE learning_celebrations SET seen_level=:level WHERE learner_id=:owner'), {'owner': owner, 'level': current})
    badges = db.execute(text('UPDATE learning_awards SET seen=true WHERE learner_id=:owner AND NOT seen RETURNING badge_id'), {'owner': owner}).scalars().all()
    return {'schema': 'celebration-claim/v1', 'level': current if current > previous else None, 'badge_ids': sorted(badges)}
