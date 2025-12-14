from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import create_db_and_tables
from app.routers import auth, health, root


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: 建立 DB 與資料表（SQLite 開發用）
    create_db_and_tables()
    yield
    # Shutdown: 目前無需處理（如未來有連線/資源釋放再加）


app = FastAPI(title="Studydy Backend", lifespan=lifespan)

app.include_router(root.router)
app.include_router(health.router)
app.include_router(auth.router)
