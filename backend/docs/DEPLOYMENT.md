````md
# Deployment（最小部署）

本文件提供「最小可跑」的部署流程（Ubuntu + venv + Uvicorn），以及在反向代理（Nginx/Traefik/Cloudflare 等）後方的必要注意事項。  
本專題階段不提供 systemd / Docker / CI 設定範本（避免環境差異造成誤用）。

---

## 1) 前置需求（Ubuntu）

### 系統套件
```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
````

### 連線與埠

* 預設服務埠：`8000`
* 若有防火牆，請開放對外或僅允許反向代理連入（依你的架構）

---

## 2) 最小可跑（單機 / VM）

以下以 `be-dev` 分支為例。請在專案根目錄操作：

```bash
git clone <YOUR_REPO_URL>
cd Studydy
git checkout be-dev
git pull
```

進入後端目錄：

```bash
cd backend
```

建立虛擬環境並安裝依賴：

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt
```

設定環境變數（至少要設 session secret；DB 視情況）：

```bash
export SESSION_SECRET_KEY="replace-me-with-a-strong-secret"
# 若需要跨來源呼叫並帶 cookie，設定可允許的前端來源：
export CORS_ORIGINS="http://localhost:5173,http://127.0.0.1:5173"
# 開發用預設多為 SQLite；若要指定可用：
# export DATABASE_URL="sqlite:///./studydy.db"
# MySQL 範例（如專案有支援）：
# export DATABASE_URL="mysql+pymysql://user:password@127.0.0.1:3306/studydy"
```

啟動服務：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

驗證：

* Swagger：`http://<server-ip>:8000/docs`
* Health：`curl http://127.0.0.1:8000/health`

---

## 3) 多進程（可選，用於較多流量/多核心）

若你需要利用多核心提升吞吐，可用 workers（示例）：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
```

注意：workers 數量通常依 CPU 核心與負載調整；專題階段建議先用 1–2 作為最小可用。

---

## 4) 反向代理注意事項（非常重要）

若你的 FastAPI 部署在反向代理後方（TLS 終結、host/scheme 由代理決定），請注意兩個重點：

### (A) 轉發標頭（Forwarded Headers）與 Uvicorn 信任設定

反向代理通常會帶入：

* `X-Forwarded-For`
* `X-Forwarded-Proto`
* `X-Forwarded-Host`

Uvicorn 需要啟用 proxy headers，並「只信任」你的代理來源 IP（或在你確定只有代理能連到後端時才用 `*`）：

```bash
uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips="127.0.0.1"
```

若你的架構確保「外部完全無法直連後端，只能透過受信任代理」，可使用：

```bash
uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips="*"
```

安全提醒：

* 一旦使用 `*`，代表信任所有來源的 forwarded headers；若有人能直接打到後端，可能偽造 scheme/host 等資訊。

### (B) 子路徑掛載（Path Prefix，例如對外是 /api）

常見情境：

* 對外：`https://example.com/api/...`
* 對內（後端實際）：`http://127.0.0.1:8000/...`
* 代理會「剝掉 /api」後再轉發給後端

這時你通常需要設定 `root_path`，讓 Swagger/OpenAPI 生成的 URL（以及部分 redirect URL）帶上外部看到的 prefix。可用兩種方式：

1. 程式碼設定（較一致）

* 在 `FastAPI(...)` 初始化時設定 `root_path="/api"`

2. 啟動參數設定（依你部署習慣）

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --root-path /api
```

注意：

* `root_path` 主要影響「外部可見 URL 的組合」（如 Swagger/OpenAPI/redirect），不會把你的路由真的加上 `/api` 前綴；前綴路由是否存在取決於你的 proxy 是否 strip prefix、或你的 router 是否真的有 prefix。

---

## 5) 最小 Nginx 範例（可選）

此段僅供參考（請依你的網域、TLS、與安全需求調整）：

```nginx
location /api/ {
  proxy_pass http://127.0.0.1:8000/;

  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;

  # 若你用 /api prefix 且 proxy 有 strip prefix，通常還要搭配 root_path
}
```
