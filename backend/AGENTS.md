```md
# AGENTS.md — Studydy Backend（給 Codex 的工作指引 / 無 Git 權限版）

本檔案提供 Codex 在此 repo 內工作的必要背景、架構規範、測試方式與硬性限制。
重要：本專案 **不授權 Codex 進行任何 Git 操作**（包含讀取/修改 git 狀態或執行 git 指令）。

---

## 0) 專案現況（Current Status）

- Studydy：使用者上傳文件（筆記/講義/文章等），系統將靜態內容轉換成互動故事/教學內容。
- 我目前只負責後端；前端雛形僅在 `fe/m3-prototype`，M3 前不串接後端。
- 後端 Auth 已完成（非 stub），包含：
  - 兩段式註冊（/auth/register/request-code → /auth/register/confirm）
  - JWT 登入（/auth/login；失敗固定錯誤訊息避免帳號枚舉）
  - 兩段式忘記密碼/重設密碼（/auth/password-reset/request-code 固定回 200 + 通用訊息；/confirm 更新密碼）
  - EmailService 為 demo stub（不真寄信；驗證碼可印在 console；測試用 fake email service，不依賴 console）

---

## 1) 開發環境（Environment）

- Windows + WSL(Ubuntu)
- Repo 在 WSL：`~/projects/Studydy`
- 後端所有指令在 `backend/` 執行

---

## 2) 絕對限制：Codex 不得處理 Git（Hard Constraint）

### 2.1 禁止執行任何 git 指令
你（Codex）不得執行下列任何操作（包含但不限於）：
- `git status`, `git diff`, `git add`, `git commit`, `git push`, `git pull`
- `git fetch`, `git rebase`, `git merge`, `git checkout`, `git switch`, `git branch`
- 修改 `.git/` 目錄或任何 git config
- 變更 remote / branch / tags / submodules

### 2.2 你必須改用以下交付方式
當你完成修改後，請在回覆中輸出：
1) What changed（條列）
2) Why（理由）
3) How to test（至少 `pytest`）
4) Files changed（檔案清單 + 用途）
5) **Manual Git steps for human**（提供「人類要執行」的 git 指令，但你自己不得執行）

---

## 3) 安全與敏感資訊（Security Rules）

- 禁止提交/產生任何 secrets：
  - `.env`、API keys、DB 連線字串、私鑰/憑證、JWT secret 等
- `docs_local/` 為本機私有資料（已在 `.gitignore`），不得碰、不得引用、不得提交
- 不要把驗證碼、token、密碼等敏感資訊寫入 log 或文件範例中

---

## 4) 不在範圍（Out of Scope）

除非使用者明確要求，否則不要新增：
- Docker / systemd / CI（GitHub Actions 等）
- 雲端部署自動化

---

## 5) 既有架構與分層原則（Architecture Contract）

維持既有分層；若要新增/修改功能，優先擴充對應層級，不要讓 router 變肥。

- `backend/app/routers/`：路由層（保持瘦：路由/Depends/HTTPException）
- `backend/app/schemas/`：Pydantic request/response models
- `backend/app/core/`：
  - `config.py`：集中常數與 env（JWT、驗證碼、密碼規則）
  - `security.py`：hash/verify + JWT 建立
- `backend/app/services/`：可重用 workflow（例如 verification code）
- `backend/app/models/`：SQLModel tables（User、VerificationCode）
- DB 建表：使用 FastAPI lifespan（不要用 `on_event`）

---

## 6) 目前目錄結構（Repo Layout Snapshot）

```

Studydy/
├─ backend/
│  ├─ .env.example
│  ├─ README.md
│  ├─ pytest.ini
│  ├─ requirements.txt
│  ├─ studydy.db
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ db.py
│  │  ├─ core/ (config.py, security.py)
│  │  ├─ models/ (user.py, verification.py)
│  │  ├─ routers/ (auth.py, health.py, root.py)
│  │  ├─ schemas/ (auth.py)
│  │  └─ services/ (email.py, verification_codes.py)
│  ├─ docs/ (API.md, DEVELOPMENT.md, DEPLOYMENT.md, SECURITY.md)
│  └─ tests/ (conftest.py + auth/health tests)
└─ docs_local/ (ignored, private)

````

---

## 7) 本機啟動（Dev Runbook）

在 `backend/`：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
uvicorn app.main:app --reload
````

* Swagger：`http://127.0.0.1:8000/docs`
* Health：`curl http://127.0.0.1:8000/health`

---

## 8) 測試（Testing Contract）

你可以執行測試指令（允許跑 pytest），但不得執行 git。

在 `backend/`：

```bash
pytest
```

常用：

```bash
pytest -q
pytest -q -k auth
```

測試策略要求：

* 測試需使用 dependency overrides 隔離 DB / EmailService
* 測試不得依賴 console 輸出取得驗證碼（使用 fake email service/hook）

---

## 9) 文件更新規範（Docs Contract）

若修改任何 API 行為或回應格式，必須同步更新：

* `backend/docs/API.md`

若修改架構/測試策略/部署注意事項，對應更新：

* `backend/docs/DEVELOPMENT.md`
* `backend/docs/DEPLOYMENT.md`
* `backend/docs/SECURITY.md`

---

## 10) 變更風格（Diff Hygiene）

* 不要做大範圍格式化或無意義改名（避免 diff 爆炸）
* 優先小範圍改動、保持行為不變（除非明確修 bug）
* 安全相關行為（避免帳號枚舉、固定錯誤訊息）必須維持既有策略

```