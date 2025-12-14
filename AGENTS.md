# Studydy — AGENTS.md（給 Codex 的專案指引）

## 0) 目前專案現況（重要）
- main / be-dev / fe-dev 目前幾乎只有：.gitignore、README.md（後端程式碼尚未建立）。
- 前端雛形獨立存在於分支：fe/m3-prototype（M3 前不與後端整合）。
- 我目前只負責後端；請以「建立後端最小可運作骨架 → 完成 Auth 註冊/登入」為優先路線。
- 部署（Docker/systemd/Nginx/CI/CD）不在本分工範圍：除非我明確要求，請不要新增任何部署腳本與設定。

## 1) 範圍控管（你可以做 / 不可以做）
### 可以做
- 後端（Backend）程式碼、測試、README/操作文件（可提交到 GitHub）。

### 不可以做
- 不要修改前端雛形（fe/m3-prototype）或嘗試做前後端串接；除非我明確要求。
- 禁止提交或推送任何私有文件與敏感資訊：
  - docs_local/ 內所有檔案（PDF、講義、報告等）
  - .env、API keys、連線字串、私鑰/憑證、任何 secrets

## 2) Repo 目錄規劃（請遵守）
- backend/           後端主程式碼（若不存在，請建立）
- docs/              可公開的需求摘要、操作說明（可提交）
- docs_local/         本機私有文件（已 .gitignore；嚴禁提交）

備註：
- docs_local/ 的用途：我會從 Windows 複製文件到 WSL 放這裡「僅供本機參考」，不可進入 Git 追蹤。

## 3) 分支策略（請嚴格遵守）
- 後端開發基準分支：be-dev
- 後端功能分支命名：be/feature-<scope>（例如 be/feature-auth-login-register）
- PR 目標：一律發 PR 合回 be-dev（不要直接改 main）
- PR 原則：一次 PR 只做「單一目的」、小步快跑（便於審核與回滾）

## 4) 開發環境
- 開發在 Windows + WSL(Ubuntu)；在 WSL 的 Linux 檔案系統內工作（~/projects/...）。

## 5) 後端技術棧（已確定）
- 語言：Python 3.x
- Web Framework：FastAPI
- ASGI Server：Uvicorn（開發用可使用 --reload；以 import string 方式啟動，例如 main:app）  
- 測試：pytest + FastAPI TestClient（需要 httpx）

參考：
- Uvicorn 啟動形式與 main:app 說明：FastAPI 官方文件  
- TestClient / pytest 的使用：FastAPI 官方 Testing

## 6) 後端骨架（第一階段交付目標）
請先建立最小可啟動後端骨架（不需要任何部署設定）：
- 建立 backend/ 及 Python package 結構（例如 backend/main.py 或 backend/app/main.py）
- 需可啟動（以 uvicorn <module>:app 形式）
- 需有基本健康檢查（例如 GET /health 或 GET /）
- 需提供最小 README：如何安裝、啟動、跑測試

※ 專案結構建議使用 APIRouter 分檔（FastAPI Bigger Applications 的做法），方便後續拆 auth router。  

## 7) Auth（第二階段交付目標，最高優先功能）
完成以下兩支 API（使用 APIRouter 分檔）：

### POST /auth/register
- email 格式檢查、密碼長度/基本規則檢查
- email 已存在：回覆一致的錯誤
- 密碼只存雜湊（不可明文）
- 預留 learning_preference 欄位（optional/nullable）

### POST /auth/login
- 帳密正確：回傳 token（JWT 或等效 token）
- 帳密錯誤：回覆一致且清楚的錯誤（不要洩漏是哪一項錯）

## 8) 測試與驗證（每次 PR 最低要求）
- register：至少 1 成功 + 1 失敗測試
- login：至少 1 成功 + 1 失敗測試
- PR 描述必附：
  - 如何啟動後端（命令）
  - 如何跑測試（命令）
  - 如何用 curl/Postman 驗證 register/login

## 9) 工作方式（你開始改檔前必做）
- 先盤點 repo 現況並回報：目前有哪些檔案、是否已有 backend/、是否已有既定規範
- 若需新增依賴/檔案較多：
  - 先提出最小可行檔案樹（tree）與理由，再開始生成，避免一次產生過多樣板
