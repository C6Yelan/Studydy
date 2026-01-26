````md
# Studydy Backend（FastAPI + SQLModel）

本目錄為 Studydy 專題後端服務（FastAPI + SQLModel）。建議在 WSL（Ubuntu）環境下於 `backend/` 目錄執行所有指令。

---

## 文件索引（必看）
- 前端串接：`docs/API.md`
- 後端維護：`docs/DEVELOPMENT.md`
- 最小部署：`docs/DEPLOYMENT.md`
- 安全約束：`docs/SECURITY.md`

---

## 快速啟動（本機 / WSL）

在 `backend/` 目錄執行：

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt

uvicorn app.main:app --reload
````

* Swagger（OpenAPI）：`http://127.0.0.1:8000/docs`
* Health check：`curl http://127.0.0.1:8000/health`

---

## 測試（pytest）

在 `backend/` 目錄執行：

```bash
pytest
```

常用指令：

```bash
pytest -q
pytest -q -k auth
pytest -q tests/test_auth_login.py
```

---

## 環境變數（Environment Variables）

### `DATABASE_URL`

* 未設定時：使用 SQLite（開發用）
* 可覆寫為 MySQL（或其他 SQLAlchemy/SQLModel 支援的 DB）

範例：

```bash
export DATABASE_URL="sqlite:///./studydy.db"
# export DATABASE_URL="mysql+pymysql://user:password@127.0.0.1:3306/studydy"
```

### `SESSION_SECRET_KEY`

* 開發環境可使用預設值（僅限開發）
* 正式環境務必設定為高強度、不可猜測的值（用於 session cookie 簽章）

範例：

```bash
export SESSION_SECRET_KEY="replace-me-with-a-strong-secret"
```

### `CORS_ORIGINS`

* 允許跨來源呼叫並攜帶 cookie 的來源清單（逗號分隔）
* 預設：`http://localhost:5173,http://127.0.0.1:5173`

範例：

```bash
export CORS_ORIGINS="http://localhost:5173,http://127.0.0.1:5173"
```

### `.env`（注意）

* 可參考 `.env.example`
* **禁止提交 `.env`、任何 API keys、連線字串、私鑰/憑證等 secrets**

---

## 專案結構（Overview）

* `app/main.py`：FastAPI app、routers 註冊、lifespan（啟動建表）
* `app/db.py`：DB engine、`create_db_and_tables()`、`get_session()`
* `app/routers/`：路由層（root/health/auth）
* `app/models/`：SQLModel tables（User、EmailVerificationCode）
* `app/schemas/`：Pydantic request/response models（auth schemas）
* `app/core/`：集中 config/security（hash/verify、常數）
* `app/services/`：可重用服務（EmailService stub、verification code workflow）
* `tests/`：pytest 測試（包含 dependency overrides）

---

## Git / 協作規範（Backend）

* 基準分支：`be-dev`
* 功能分支：`be/feature-*`
* 一律以 PR 合回 `be-dev`
* PR 原則：單一目的、小步 commit、提交前先 `pytest`
* `docs_local/` 為私有資料夾（已在 `.gitignore`），嚴禁 commit/push
