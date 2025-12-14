# Studydy 後端 API 呼叫與部署指南（給組員/部署者）

本文件目的：
1) 讓前端/組員快速理解目前後端有哪些 API、怎麼呼叫、成功/失敗會回什麼。
2) 讓負責部署的人，能在 Ubuntu 伺服器上把後端跑起來（含環境變數、啟動方式、反向代理建議）。

> 目前後端技術：FastAPI + Uvicorn，DB：SQLModel + SQLite（預設自動建表/自動建立 `studydy.db`）。  
> Auth：`/auth/register` + `/auth/login`（JWT）。  
> 註：部署方式很多，本文件提供「最小可跑」與「較正式（systemd + Gunicorn + Nginx）」兩種。FastAPI 官方也建議在部署時考慮 HTTPS、重啟、複本（workers）等概念。


---

## 1. 快速確認：Swagger / OpenAPI 文件
後端啟動後：
- Swagger UI：`/docs`
- ReDoc：`/redoc`

前端同學可直接在 `/docs` 測試 request/response（含欄位與格式）。


---

## 2. API Base URL（前端要用的網址）
- 本機開發：`http://127.0.0.1:8000`
- 若部署在 Ubuntu 主機：`http(s)://<你的網域或IP>`（通常會透過 Nginx 反向代理處理 HTTPS 與對外 port）

建議前端用環境變數配置，例如（示意）：
- `VITE_API_BASE=http://127.0.0.1:8000`


---

## 3. API 清單與規格（目前已實作）

### 3.1 健康檢查
**GET** `/health`

成功：
```json
{"status":"ok"}
```

用途：部署後可以用來確認服務是否活著。


### 3.2 註冊（建立帳號）
**POST** `/auth/register`

Request body（JSON）：
```json
{
  "email": "user@example.com",
  "password": "supersecret",
  "learning_preference": "visual"
}
```
- `learning_preference`：可省略（optional）

成功（201）：回使用者資訊（不會回 `password_hash`）
```json
{
  "id": 1,
  "email": "user@example.com",
  "learning_preference": "visual",
  "created_at": "2025-12-14T07:30:00+00:00"
}
```

失敗：
- Email 已註冊（400）：
```json
{"detail":"Email already registered"}
```
- Email 格式錯誤（422）：FastAPI/Pydantic 會自動回驗證錯誤


### 3.3 登入（取得 JWT Token）
**POST** `/auth/login`

Request body（JSON）：
```json
{
  "email": "user@example.com",
  "password": "supersecret"
}
```

成功（200）：
```json
{
  "access_token": "<JWT字串>",
  "token_type": "bearer"
}
```

失敗（401）：（不透露到底是 email 錯或 password 錯）
```json
{"detail":"Incorrect email or password"}
```
並包含 `WWW-Authenticate: Bearer` header。


---

## 4. 前端呼叫範例

### 4.1 使用 fetch（瀏覽器原生）
```js
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export async function register(payload) {
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok) throw data;     // 例如 {detail: "Email already registered"}
  return data;
}

export async function login(payload) {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok) throw data;
  return data; // { access_token, token_type }
}
```

### 4.2 帶上 token 呼叫受保護 API（未來會用到）
目前只有 register/login 不需要 token；但未來若新增需要登入的 API，前端要在 header 帶：
```
Authorization: Bearer <access_token>
```

fetch 例：
```js
const res = await fetch(`${API_BASE}/some/protected`, {
  headers: {
    "Authorization": `Bearer ${token}`,
  },
});
```


---

## 5. CORS（前後端分開跑時必須處理）
情境：前端跑 `http://localhost:5173`，後端跑 `http://127.0.0.1:8000`，瀏覽器會因跨網域而擋請求。

做法：在後端加入 CORSMiddleware，允許前端網域（請只開必要的 origins，不要長期用 `*`）。

建議部署/開發時視需求調整。


---

## 6. 開發/測試（給組員）

在 `backend/` 內：
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pytest
```

啟動開發伺服器：
```bash
uvicorn app.main:app --reload
```


---

## 7. Ubuntu 伺服器部署（給部署者）

### 7.1 最小可跑（不含 systemd / Nginx）
適用：內網測試、或短期 demo。

1) 安裝系統套件
```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

2) 取得程式碼
```bash
cd ~
git clone <你的 repo SSH/HTTPS>
cd Studydy
git checkout be-dev   # 依團隊規範選擇要部署的分支
```

3) 安裝依賴
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

4) 設定環境變數（不要提交 .env）
- `JWT_SECRET_KEY`：JWT 簽章用的密鑰（正式環境務必更換）
- `DATABASE_URL`：覆蓋 DB 位置（不設則預設在 backend 目錄產生 `studydy.db`）

示例（當次 session）：
```bash
export JWT_SECRET_KEY="請換成長且不可猜的字串"
export DATABASE_URL="sqlite:////var/lib/studydy/studydy.db"
```

5) 啟動（對外 demo 建議用 `--host 0.0.0.0`）
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> 注意：這種方式沒有自動重啟/守護行程；關閉 SSH 或 session 可能就停了。


### 7.2 較正式部署（建議）：systemd + Gunicorn(Uvicorn worker) + Nginx
適用：需要重啟、自動常駐、可擴展 workers、可加 HTTPS。

概念：
- App server：Gunicorn 管理多個 Uvicorn worker（處理 ASGI）
- Reverse proxy：Nginx 對外，負責 HTTPS 終止與轉發到內網端口

#### A) 建議的目錄/帳號
- 建議用專用使用者：`studydy`
- 專案路徑：`/srv/studydy/Studydy`
- DB 路徑：`/var/lib/studydy/studydy.db`

#### B) 建立使用者與目錄
```bash
sudo adduser --system --group --home /srv/studydy studydy
sudo mkdir -p /var/lib/studydy
sudo chown -R studydy:studydy /var/lib/studydy
```

#### C) 取得程式碼（用 studydy 身份）
```bash
sudo -u studydy -H bash -lc '
cd /srv/studydy
git clone <你的 repo SSH/HTTPS> Studydy
cd Studydy
git checkout be-dev
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
'
```

#### D) 建立環境變數檔（不進 Git）
建立 `/etc/studydy/backend.env`：
```
JWT_SECRET_KEY=請換成長且不可猜的字串
DATABASE_URL=sqlite:////var/lib/studydy/studydy.db
```

並設定權限：
```bash
sudo mkdir -p /etc/studydy
sudo chown root:root /etc/studydy/backend.env
sudo chmod 600 /etc/studydy/backend.env
```

#### E) systemd service（示例）
建立 `/etc/systemd/system/studydy-backend.service`：
```ini
[Unit]
Description=Studydy Backend (FastAPI)
After=network.target

[Service]
User=studydy
Group=studydy
WorkingDirectory=/srv/studydy/Studydy/backend
EnvironmentFile=/etc/studydy/backend.env
ExecStart=/srv/studydy/Studydy/backend/.venv/bin/gunicorn   -k uvicorn.workers.UvicornWorker   -w 2   -b 127.0.0.1:8000   app.main:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

啟用並啟動：
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now studydy-backend
sudo systemctl status studydy-backend --no-pager
```

看 logs：
```bash
sudo journalctl -u studydy-backend -f
```

> workers 數量可依 CPU 調整（例如 2~4）。


#### F) Nginx 反向代理（示例）
1) 安裝 Nginx
```bash
sudo apt install -y nginx
```

2) 建立站台設定（示例 `/etc/nginx/sites-available/studydy`）
```nginx
server {
    listen 80;
    server_name your.domain.or.ip;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

啟用並重載：
```bash
sudo ln -s /etc/nginx/sites-available/studydy /etc/nginx/sites-enabled/studydy
sudo nginx -t
sudo systemctl reload nginx
```

3) HTTPS
若要正式對外，建議讓 Nginx 處理 TLS 憑證（例如 Certbot/Let’s Encrypt）。


---

## 8. 反向代理與 /docs 路徑注意事項
如果你的 Nginx 把後端掛在「子路徑」（例如 `/api`），可能需要設定 FastAPI 的 `root_path`，不然 `/docs` / `openapi.json` 可能會出現路徑不對。


---

## 9. 更新部署（部署者操作 SOP）
以 systemd 方式部署時，更新流程通常是：
```bash
sudo -u studydy -H bash -lc '
cd /srv/studydy/Studydy
git fetch --all
git checkout be-dev
git pull
cd backend
source .venv/bin/activate
pip install -r requirements.txt
'
sudo systemctl restart studydy-backend
sudo systemctl status studydy-backend --no-pager
```

---

## 10. 安全與注意事項（最小清單）
- `JWT_SECRET_KEY` 務必用環境變數設定，不要寫死在 repo。
- 對外服務建議走 HTTPS（由 Nginx/Proxy 處理）。
- DB 若用 SQLite，請確保 DB 檔案路徑可寫且有備份策略；若多人協作/規模變大，建議改用 Postgres/MySQL 並用 `DATABASE_URL` 切換。
- CORS 請只開必要 origins，避免 `*` 長期留在正式環境。

