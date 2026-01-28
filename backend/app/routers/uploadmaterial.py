import os
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlmodel import Session
from datetime import datetime
from ..db import get_session
from ..models.dashboard import UserDocument
from .auth import get_current_user

router = APIRouter(prefix="/materials", tags=["Materials"])

UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

@router.post("/upload")
async def upload_material(
    file: UploadFile = File(...),
    current_user = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    try:
        if not current_user:
            raise HTTPException(status_code=401, detail="Session expired")
        
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        new_doc = UserDocument(
            user_id=current_user.id,
            title=file.filename,
            file_path=file_path, 
            progress=0,
            last_accessed=datetime.now()
        )
        
        session.add(new_doc)
        session.commit()
        session.refresh(new_doc)

        return {"message": "上傳成功！", "filename": file.filename}

    except Exception as e:
        print(f"--- UPLOAD ERROR: {str(e)} ---")
        raise HTTPException(status_code=500, detail=f"Gagal: {str(e)}")