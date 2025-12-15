````md
# Development（後端維護者必看）

本文件聚焦於後端維護與擴充時的結構原則、測試策略與常見陷阱。請在 `backend/` 目錄執行指令。

---

## 1) 架構原則（Architecture Principles）

### 單一職責（Single Responsibility）
- Router 層（`app/routers/`）：只做「路由定義、依賴注入（Depends）、HTTP 例外處理（HTTPException）」。
- Schema 層（`app/schemas/`）：只放 Pydantic request/response models（避免在 router 內 inline 定義）。
- Core 層（`app/core/`）：集中「可配置常數、env 讀取、安全工具（hash/JWT）」。
- Service 層（`app/services/`）：放可重用的業務流程（例如驗證碼發放/驗證），避免註冊與重設密碼流程在 router 重複實作。
- Model 層（`app/models/`）：SQLModel tables 與欄位約束。

此結構對應 FastAPI 對「Bigger Applications / Multiple Files」的建議方向：專案變大後以多檔案分層維持可讀性與可維護性。

---

## 2) 目錄結構（Project Layout）

- `app/main.py`
  - FastAPI app 初始化、include routers、lifespan（啟動建表）
- `app/db.py`
  - engine、`create_db_and_tables()`、`get_session()`（DB session 依賴）
- `app/routers/`
  - `root.py`、`health.py`、`auth.py`
- `app/models/`
  - `user.py`（User table）
  - `verification.py`（Email verification code table）
- `app/schemas/`
  - `auth.py`（Auth request/response models）
- `app/core/`
  - `config.py`（JWT/驗證碼/密碼規則等集中常數與 env）
  - `security.py`（hash/verify、JWT 建立）
- `app/services/`
  - `email.py`（EmailService，demo 為 console stub）
  - `verification_codes.py`（驗證碼：產生/存取/驗證/標記 used）
- `tests/`
  - pytest + TestClient（使用 dependency overrides 注入測試用 DB/email service）

---

## 3) 設定與常數（Configuration & Constants）

所有安全與流程相關常數應集中於 `app/core/config.py`（Single Source of Truth），避免散落多處造成 drift。
常見集中項：
- JWT：`JWT_SECRET_KEY`、`ALGORITHM`、`ACCESS_TOKEN_EXPIRE_MINUTES`
- 密碼：`MIN_PASSWORD_LENGTH`、PBKDF2 iterations（或其他 hash 成本參數）
- 驗證碼：`VERIFICATION_CODE_LENGTH`、`VERIFICATION_CODE_EXPIRE_MINUTES`

---

## 4) 測試策略（Testing Strategy）

### 核心概念：Dependency Overrides
測試不應觸碰正式/開發資料庫；應在測試中 override `get_session()`，注入測試用 session。
做法要點：
- 使用 `app.dependency_overrides` 覆寫依賴（key 為原 dependency function，value 為 override function）。
- 測試完成後務必清理 overrides，避免跨測試檔互相污染（特別是全量跑 `pytest` 時）。

### 建議的 fixture 形態（pytest）
- 使用 `yield` fixture：`yield` 前是 setup，`yield` 後是 teardown（例如關閉 session、清掉 overrides）。
- `TestClient` 多為同步使用；即使你的依賴是 async generator，也可由 FastAPI 正常處理（依專案現況為準）。

### 常用測試指令
在 `backend/`：
```bash
pytest
pytest -q
pytest -q -k auth
pytest -q tests/test_auth_login.py
pytest -x
pytest --pdb
````

---

## 5) 常見陷阱與排查（Pitfalls & Troubleshooting）

### (A) 全量跑會失敗、單檔跑正常

高機率原因：`app.dependency_overrides` 沒有在 fixture teardown 清掉，導致後續測試仍使用前一個測試的 override。

建議：在 `tests/conftest.py` 的 client fixture teardown：

* `app.dependency_overrides.clear()`

### (B) 測試 DB 資料「看起來沒有寫入」

若使用 SQLite in-memory 或不同 engine/session 來源，可能導致資料不在同一個連線或 scope。
建議：測試用 engine/session 統一在 fixture 建立，並透過 override 注入同一個 session 來源。

### (C) Router 變肥、修改牽一髮動全身

當你需要新增 refresh token、rate limit、或更換 email provider 時：

* 優先擴充 `services/` 或 `core/`，避免把邏輯塞回 router。
* 任何常數變更只改 `core/config.py`，不要在多個檔案同步手改。

---

## 6) 變更提交流程（Backend Contribution Workflow）

* 基準分支：`be-dev`
* 功能分支：`be/feature-*`
* 每次 PR：單一目的、小步 commit
* PR 前檢查：

  1. `pytest` 全綠
  2. Swagger 可開啟（`/docs`）
  3. 禁止提交 `.env`/secrets（含 JWT secret、DB 連線字串、私鑰/憑證）