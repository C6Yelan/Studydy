from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlmodel import Session, select

from app.core.config import ALLOWED_UPLOAD_EXTS, UPLOAD_DIR
from app.db import get_session
from app.models import UserDocument, UserStats
from app.routers.auth import get_current_user
from app.schemas.auth import UserResponse
from app.schemas.materials import DashboardMeResponse, MaterialUploadResponse
from app.services.uploadmaterial import save_upload_file

router = APIRouter(prefix="/materials", tags=["materials"])


def _get_greeting(now: datetime) -> str:
    hour = now.hour
    if 5 <= hour < 12:
        return "早安"
    if 12 <= hour < 18:
        return "午安"
    return "晚安"


@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
    response_model=MaterialUploadResponse,
)
async def upload_material(
    file: UploadFile = File(...),
    user: UserResponse = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MaterialUploadResponse:
    stored_path, original_name, stored_name, ext, size = save_upload_file(
        file,
        upload_dir=UPLOAD_DIR,
        allowed_exts=set(ALLOWED_UPLOAD_EXTS),
    )

    doc = UserDocument(
        user_id=user.id,
        original_filename=original_name,
        stored_filename=stored_name,
        stored_path=str(stored_path),
        ext=ext,
        size_bytes=size,
        progress=0.0,
        last_accessed=datetime.now(timezone.utc),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)

    return MaterialUploadResponse(
        message="Upload successful",
        document_id=doc.id,
        original_filename=doc.original_filename,
        stored_filename=doc.stored_filename,
    )


@router.get("/me", response_model=DashboardMeResponse)
async def get_dashboard_me(
    user: UserResponse = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> DashboardMeResponse:
    display_name = user.email.split("@", 1)[0]
    greeting = _get_greeting(datetime.now())

    active_doc = session.exec(
        select(UserDocument)
        .where(UserDocument.user_id == user.id)
        .order_by(UserDocument.last_accessed.desc())
    ).first()

    stats = session.exec(select(UserStats).where(UserStats.user_id == user.id)).first()

    return DashboardMeResponse(
        greeting=f"{greeting}，{display_name}！",
        profile={"display_name": display_name, "role": "會員"},
        active_doc={
            "document_id": active_doc.id if active_doc else None,
            "title": active_doc.original_filename if active_doc else None,
            "progress": float(active_doc.progress) if active_doc else 0.0,
        },
        stats={
            "study_hours": float(stats.study_hours) if stats else 0.0,
            "stories_completed": int(stats.stories_completed) if stats else 0,
        },
    )
