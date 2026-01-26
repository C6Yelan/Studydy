from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
import os

from app.db import create_db_and_tables
from app.routers import auth, health, root


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: 建立 DB 與資料表（SQLite 開發用）
    create_db_and_tables()
    yield
    # Shutdown: 目前無需處理（如未來有連線/資源釋放再加）
# 讓前端可帶 cookie：allow_credentials=True 時，origins 不能用 "*"，要明確列出
# 否則瀏覽器會擋掉「帶 credentials 的跨域請求」:contentReference[oaicite:3]{index=3}
app = FastAPI(title="Studydy Backend", lifespan=lifespan)
cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SESSION_SECRET_KEY"],
    max_age=int(os.getenv("SESSION_MAX_AGE_SECONDS", "1209600")),
    same_site=os.getenv("SESSION_SAME_SITE", "lax"),
    https_only=os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true",
)


app.include_router(root.router)
app.include_router(health.router)
app.include_router(auth.router)
