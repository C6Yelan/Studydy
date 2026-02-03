import os
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlmodel import Session
from datetime import datetime
from ..db import get_session
from ..models.dashboard import UserDocument
from .auth import get_current_user
# Requirement 3: Import the service logic
from ..services.uploadmaterial import save_upload_file

router = APIRouter(prefix="/materials", tags=["Materials"])

# Requirement 4: Security whitelist
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx"}

@router.post("/upload")
async def upload_material(
    file: UploadFile = File(...),
    current_user = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    # 1. Validation (Requirement 4 - "防呆")
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Invalid format. Only PDF, DOCX, and PPTX.")

    try:
        # 2. Call Service (Requirement 3 - Professional structure)
        # Service handles the UUID naming and 'wb' saving
        file_path = save_upload_file(file)

        # 3. Database Entry (Requirement 5)
        new_doc = UserDocument(
            user_id=current_user.id,
            title=file.filename, # Original name for display
            file_path=file_path, # Path to the UUID renamed file
            progress=0,
            last_accessed=datetime.now()
        )
        
        session.add(new_doc)
        session.commit()
        session.refresh(new_doc)

        return {"message": "Upload successful!", "id": new_doc.id}

    except Exception as e:
        print(f"--- UPLOAD ERROR: {str(e)} ---")
        raise HTTPException(status_code=500, detail="Internal server error.")