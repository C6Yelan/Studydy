---

# Studydy 後端（FastAPI + SQLModel）

本目錄為 Studydy 專題後端（FastAPI + SQLModel）。
建議在 WSL 環境下，於 `backend/` 目錄執行所有指令。

---

## 0) 讀者導覽（你應該看哪一段）

* 你是「前端」：請看 **第 3、4 章（API 串接與範例）** + **第 5 章（CORS）**
* 你是「後端」：請看 **第 1、2、6、7、11 章（結構/環境變數/啟動/測試/協作）**
* 你是「部署人員」：請看 **第 2、8、9 章（環境變數/Ubuntu 最小可跑/反向代理注意事項）**

---

## 1) 專案結構（後端維護者用）

* `app/main.py`：FastAPI app（使用 lifespan 啟動流程）
* `app/db.py`：資料庫 engine、`get_session()` 依賴注入（支援 `DATABASE_URL` 覆蓋）
* `app/models/`

  * `user.py`：User 資料表（email unique、password_hash、learning_preference、created_at）
  * `verification.py`：Verification code（用於「註冊驗證碼」與「重設密碼驗證碼」）
* `app/routers/`

  * `auth.py`：註冊（兩段式）/ 登入（JWT）/ 忘記密碼（兩段式）/ 偏好清單
  * `health.py`：健康檢查
  * `root.py`：根路由訊息
* `app/services/email.py`：EmailService（目前為 stub/console demo，方便測試與未來替換）
* `tests/`：pytest 自動化測試（in-memory SQLite + dependency overrides）
* `.env.example`：環境變數範例（不可放真密碼/金鑰）

---

## 2) 環境變數與資料庫（後端/部署都要懂）

### 2.1 DATABASE_URL（資料庫連線）

* 不設定時：預設 SQLite 檔案 `sqlite:///./studydy.db`（會在 `backend/` 生成）
* 切換 MySQL（示例格式）：

  * `mysql+pymysql://user:password@localhost:3306/studydy`

部署時請用環境變數注入，不要把連線字串寫死在程式碼或提交到 Git。

### 2.2 JWT_SECRET_KEY（JWT 簽章密鑰）

* 未設定時：會使用開發用預設值（僅供本機/測試）
* 部署到 Ubuntu/正式環境：務必設定為長且不可猜的字串

### 2.3 .env 使用方式（本機方便）

* 可將 `.env.example` 複製為 `.env` 後修改
* `.env` 必須被 `.gitignore` 忽略，避免任何 secrets 被提交

---

## 3) API 規格總覽（前端最常用）

Base URL（本機）：

* `http://127.0.0.1:8000`

Swagger UI（自查欄位/回應）：

* `http://127.0.0.1:8000/docs`

### 3.1 規則

* 密碼最小長度：8
* learning_preference 允許值：`visual`、`text`、`ai_assisted`

### 3.2 Email verification code delivery（Demo mode）

* 目前不會真的寄信，驗證碼會以 `[EmailService] Sending code <code> to <email>` 的訊息印在後端 console/stdout。
* 前端測試流程：

  * 註冊：呼叫 `/auth/register/request-code` → 到後端 console 抓取驗證碼 → 呼叫 `/auth/register/confirm`
  * 忘記密碼：呼叫 `/auth/password-reset/request-code` → 到後端 console 抓取驗證碼 → 呼叫 `/auth/password-reset/confirm`
* 採用 demo mode 原因：專題展示階段且無網域，正式寄信常受 DNS 驗證或 sandbox 限制影響。
* 未來若要正式寄信，可在 `backend/app/services/email.py` 實作真實 provider，並透過環境變數注入相關設定（避免 secrets 進 Git）。

### 3.3 Endpoints（總覽）

* `POST /auth/register/request-code`

  * 驗證 email 格式、密碼長度、email 未註冊後產生 6 位數驗證碼（10 分鐘過期），並以 console stub 印出驗證碼
* `POST /auth/register/confirm`

  * 驗證驗證碼成功後建立帳號（回 201 user payload）
* `POST /auth/login`

  * 登入成功回 `{access_token, token_type}`（JWT）；失敗回 401（固定訊息）
* `POST /auth/password-reset/request-code`

  * 針對 email 產生 6 位數驗證碼（10 分鐘過期）
  * **回應一律 200（固定訊息）**，避免外界用此端點判斷 email 是否存在（帳號枚舉）
  * 若 email 存在，驗證碼會以 console stub 印出
* `POST /auth/password-reset/confirm`

  * 驗證驗證碼後更新密碼（回 200 固定訊息）；code 錯誤或過期回 400
* `GET /auth/learning-preferences`

  * 回傳可用偏好選項清單（給前端渲染）
* `GET /health`

  * 回 `{ "status": "ok" }`
* `GET /`

  * 回 `{ "message": "Studydy backend running" }`

---

## 4) 前端如何呼叫（完整流程 + 可直接複製）

### 4.1 建議前端用環境變數管理 Base URL

例如（Vite）：

* `VITE_API_BASE=http://127.0.0.1:8000`

### 4.2 兩段式註冊（必看）

#### Step 1：申請驗證碼（request-code）

`POST /auth/register/request-code`

Request body：

```json
{
  "email": "user@example.com",
  "password": "supersecret"
}
```

成功（200）示例：

```json
{"detail":"Verification code sent"}
```

常見失敗：

* Email 格式不合法、欄位缺漏、或 password 長度不足：422 Unprocessable Entity（Request body 驗證失敗）
* Email 已被註冊：400 Bad Request

備註：

* 目前 EmailService 為 stub（ConsoleEmailService），開發時驗證碼會出現在後端執行 console log。
* 未來若接 SMTP/第三方寄信服務，前端流程不需改。

#### Step 2：確認註冊（confirm）

`POST /auth/register/confirm`

Request body：

```json
{
  "email": "user@example.com",
  "password": "supersecret",
  "code": "123456",
  "learning_preference": "visual"
}
```

成功（201）示例：

```json
{
  "id": 1,
  "email": "user@example.com",
  "learning_preference": "visual",
  "created_at": "2025-12-14T07:30:00+00:00"
}
```

常見失敗：

* code 錯誤或過期（400）
* email 已註冊（400）

---

### 4.3 登入（JWT）

`POST /auth/login`

Request body：

```json
{
  "email": "user@example.com",
  "password": "supersecret"
}
```

成功（200）示例：

```json
{
  "access_token": "<JWT字串>",
  "token_type": "bearer"
}
```

失敗（401，固定訊息，不透露 email 是否存在）：

```json
{"detail":"Incorrect email or password"}
```

---

### 4.4 忘記密碼 / 重設密碼（兩段式）

#### Step 1：申請重設驗證碼（request-code）

`POST /auth/password-reset/request-code`

Request body：

```json
{
  "email": "user@example.com"
}
```

成功（永遠回 200，固定訊息）示例：

```json
{"detail":"If the email exists, a verification code has been sent"}
```

備註：

* 若該 email 存在，系統會產生 6 位數驗證碼（10 分鐘過期），並以 console stub 印出（demo mode）
* 之所以固定回 200，是為避免帳號枚舉（外界不能藉此判斷 email 是否存在）

#### Step 2：確認重設（confirm）

`POST /auth/password-reset/confirm`

Request body：

```json
{
  "email": "user@example.com",
  "code": "123456",
  "new_password": "newsecretpw"
}
```

成功（200）示例：

```json
{"detail":"Password updated"}
```

失敗（400）示例：

```json
{"detail":"Invalid or expired verification code"}
```

---

### 4.5 之後呼叫需要登入的 API（未來新增時會用）

請把 token 放到 Header：

```
Authorization: Bearer <access_token>
```

---

### 4.6 fetch 範例（可直接貼到前端）

```js
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

async function apiFetch(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw data;
  return data;
}

// 註冊：申請驗證碼
export function requestRegisterCode(email, password) {
  return apiFetch("/auth/register/request-code", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

// 註冊：確認
export function confirmRegister(email, password, code, learning_preference) {
  return apiFetch("/auth/register/confirm", {
    method: "POST",
    body: JSON.stringify({ email, password, code, learning_preference }),
  });
}

// 登入
export function login(email, password) {
  return apiFetch("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

// 忘記密碼：申請重設驗證碼（固定回應 200）
export function requestPasswordResetCode(email) {
  return apiFetch("/auth/password-reset/request-code", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

// 忘記密碼：確認重設
export function confirmPasswordReset(email, code, new_password) {
  return apiFetch("/auth/password-reset/confirm", {
    method: "POST",
    body: JSON.stringify({ email, code, new_password }),
  });
}

// 偏好清單
export function getLearningPreferences() {
  return apiFetch("/auth/learning-preferences", { method: "GET" });
}
```

---

## 5) CORS（前後端分開跑必看）

若前端（例如 `http://localhost:5173`）與後端（`http://127.0.0.1:8000`）不同來源，瀏覽器會因 CORS 阻擋請求。

後端可用 FastAPI 的 `CORSMiddleware` 設定允許的 origins（建議只允許必要來源，不要長期用 `*`）。
（此段僅說明整合需求；實作請由後端依現況新增/調整。）

---

## 6) 後端快速啟動（後端開發者）

以下全部在 `backend/` 執行：

### 6.1 建立 venv 與安裝

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### 6.2 啟動開發伺服器

```bash
uvicorn app.main:app --reload
```

---

## 7) 自動化測試（後端開發者必做）

### 7.1 跑全部測試

```bash
pytest
```

### 7.2 只跑單一測試檔（快速驗證）

```bash
pytest tests/test_auth_login.py -q
pytest tests/test_auth_register_verification.py -q
pytest tests/test_auth_password_reset.py -q
pytest tests/test_health.py -q
```

### 7.3 測試寫作規範（本專案慣例）

* 測試使用 in-memory SQLite + dependency overrides，避免污染 `backend/studydy.db`
* 外部服務（EmailService）用 fake/stub 注入，確保測試可重現
* 若測試需要觸發 lifespan/startup/shutdown，請用 client 的 context manager（以確保 lifecycle 被執行）

---

## 8) Ubuntu 部署（部署者用：最小可跑）

本段提供「最小可跑」流程，讓 Ubuntu 主機可啟動後端服務。是否採用 systemd / Nginx 由部署者決定。

### 8.1 最小可跑（demo/內網）

在 Ubuntu：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip

cd ~
git clone <你的 repo SSH/HTTPS 位址>
cd Studydy
git checkout be-dev
git pull

cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

設定環境變數（務必設定 JWT_SECRET_KEY；DATABASE_URL 視情況）：

```bash
export JWT_SECRET_KEY="請換成長且不可猜的字串"
# export DATABASE_URL="mysql+pymysql://user:password@127.0.0.1:3306/studydy"
```

啟動：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

驗證：

* `curl http://127.0.0.1:8000/health`

---

## 9) 反向代理與子路徑（部署者注意）

若你們把 API 掛在子路徑（例如對外是 `https://example.com/api`，但後端實際路由是 `/auth/...`），需依部署方式處理 `root_path`/proxy headers，避免 Swagger/OpenAPI 連結與 redirect URL 不正確。
（本 README 不直接提供完整 Nginx/systemd 範本，避免與實際環境差異造成誤用。）

---

## 10) 常見問題排查（前端/後端/部署共用）

1. 前端打不到 API

* 先確認後端是否在跑：`GET /health` 是否 200
* 確認 Base URL 是否正確（本機 vs 部署）

2. 瀏覽器出現 CORS 錯誤

* 後端需設定 CORSMiddleware，允許前端來源

3. 註冊/重設密碼拿不到驗證碼

* 目前是 stub：驗證碼在後端 console log
* 部署環境若要真的寄信，需更換 EmailService 實作

4. MySQL 連線失敗

* 確認 `DATABASE_URL` 格式、帳號權限、防火牆、以及是否已安裝/可連到 MySQL
* 建議部署者先用 CLI/工具確認 DB 可連

---

## 11) 開發協作（後端組員）

* 後端基準分支：`be-dev`
* 功能分支：`be/feature-*`
* PR 合回 `be-dev`
* 每次提交前：`pytest` 必須全綠
* 不可提交：`.env`、任何 API keys/連線字串/憑證/私鑰、以及 `docs_local/` 內容

---
