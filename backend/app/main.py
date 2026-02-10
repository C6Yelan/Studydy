from contextlib import asynccontextmanager
import os
import sys
from pathlib import Path

# Keep a lightweight vendoring hook so the app can run in environments without
# external package downloads. If `python-multipart` is installed, it will still
# take precedence over this fallback because site-packages comes earlier on
# sys.path than the appended vendor directory.
_VENDOR_DIR = Path(__file__).resolve().parents[1] / "vendor"
if _VENDOR_DIR.is_dir():
    sys.path.append(str(_VENDOR_DIR))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.db import create_db_and_tables
from app.routers import auth, health, root


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: 建立 DB 與資料表（SQLite 開發用）
    create_db_and_tables()
    yield
    # Shutdown: 目前無需處理（如未來有連線/資源釋放再加）


SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "dev-session-secret-change-me")
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

app = FastAPI(title="Studydy Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    same_site="lax",
    https_only=False,
)

app.include_router(root.router)
app.include_router(health.router)
app.include_router(auth.router)
