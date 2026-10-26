"""作答頁的短期 presence；只限制同帳號、教材及瀏覽器的提問。"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from .errors import SourceError
from .storage.database import database_session
from .storage.tables import AssessmentPresence, AssessmentSet


def _active_set():
    return (AssessmentSet.status.in_(("ready", "in_progress")),
            AssessmentSet.sealed_at.is_not(None))


def update(owner, material_id, browser_id, set_id, active, *, dsn=None):
    with database_session(dsn) as db:
        if not active:
            db.execute(delete(AssessmentPresence).where(
                AssessmentPresence.learner_id == owner,
                AssessmentPresence.material_id == material_id,
                AssessmentPresence.browser_id == browser_id,
                AssessmentPresence.set_id == set_id,
            ))
            return {"schema": "assessment-presence/v1", "active": False, "expires_at": None}
        group = db.scalar(select(AssessmentSet).where(
            AssessmentSet.set_id == set_id,
            AssessmentSet.learner_id == owner,
            AssessmentSet.material_id == material_id,
            *_active_set(),
        ))
        if group is None:
            raise SourceError("REQUEST_INVALID")
        expires = datetime.now(UTC) + timedelta(seconds=60)
        db.execute(insert(AssessmentPresence).values(
            learner_id=owner, material_id=material_id, browser_id=browser_id,
            set_id=set_id, expires_at=expires,
        ).on_conflict_do_update(
            index_elements=["learner_id", "material_id", "browser_id"],
            set_={"set_id": set_id, "expires_at": expires},
        ))
        return {"schema": "assessment-presence/v1", "active": True, "expires_at": expires}


def require_allowed(db, owner, material_id, browser_id):
    # 交卷的 completed 狀態與答案同交易保存，故不依賴前端解除心跳。
    active = db.scalar(select(AssessmentPresence.set_id).join(
        AssessmentSet, AssessmentSet.set_id == AssessmentPresence.set_id,
    ).where(
        AssessmentPresence.learner_id == owner,
        AssessmentPresence.material_id == material_id,
        AssessmentPresence.browser_id == browser_id,
        AssessmentPresence.expires_at > datetime.now(UTC),
        *_active_set(),
    ))
    if active is not None:
        raise SourceError("ASSESSMENT_IN_PROGRESS")
