from fastapi import FastAPI

from app.read_api import router as read_api_router

app = FastAPI(title="Studydy Backend")
app.include_router(read_api_router)
