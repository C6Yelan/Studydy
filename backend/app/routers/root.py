from fastapi import APIRouter

router = APIRouter()


@router.get("/", summary="Root endpoint")
def read_root() -> dict:
    return {"message": "Studydy backend running"}
