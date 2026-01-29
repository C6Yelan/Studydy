from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from datetime import datetime
from ..db import get_session
from ..models.user import User
from ..models.dashboard import UserDocument, UserStats
from .auth import get_current_user 

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

def get_greeting():
    hour = datetime.now().hour
    if 5 <= hour < 12: return "早安!"
    elif 12 <= hour < 18: return "午安!"
    else: return "晚安！"

@router.get("/me")
async def get_dashboard_info(
    current_user_res = Depends(get_current_user), 
    session: Session = Depends(get_session)
):
    
    try:
        greeting = get_greeting()
        user_id = current_user_res.id

        user = session.get(User, user_id)
        email_name = user.email.split('@')[0] if user and user.email else "User"

        active_doc = None
        try:
            doc_stmt = select(UserDocument).where(UserDocument.user_id == user_id).order_by(UserDocument.last_accessed.desc())
            active_doc = session.exec(doc_stmt).first()
        except Exception as e:
            print(f"Error query doc: {e}")

        stats = None
        try:
            stats_stmt = select(UserStats).where(UserStats.user_id == user_id)
            stats = session.exec(stats_stmt).first()
        except Exception as e:
            print(f"Error query stats: {e}")

        return {
            "greeting_header": f"{greeting}，{email_name}！",
            "user_profile": {
                "display_name": email_name,
                "role": "會員"
            },
            "current_reading": {
                "title": active_doc.title if active_doc else "沒有材料",
                "progress": f"{active_doc.progress}%" if active_doc else "0%"
            },
            "stats": {
                "hours": f"{stats.study_hours}h" if stats else "0h",
                "count": stats.stories_completed if stats else 0
            }
        }
    except Exception as final_e:
        print(f"CRITICAL DASHBOARD ERROR: {final_e}")
        return {
            "greeting_header": "你好！",
            "user_profile": {"display_name": "User", "role": "會員"},
            "current_reading": {"title": "Belum ada materi", "progress": "0%"},
            "stats": {"hours": "0h", "count": 0}
        }