# Studydy

Studydy 是以個人教材為基礎的 AI 學習應用。將文件整理成可回查來源的知識地圖，搭配觀念題組、錯題補強與學習紀錄，協助理解與練習教材內容。

上傳教材 → 確認來源 → 分析與檢核 → 瀏覽知識地圖 → 整組作答 → 補強錯誤重點。

## 主要功能

- **多來源教材**：支援 PDF、DOC／DOCX、PPT／PPTX、UTF-8 TXT 與 Markdown，可將多份文件整理為同一份教材。
- **來源可回查**：保留原檔與 PDF 預覽，從觀念、重點與關係回到對應的教材來源。
- **知識地圖**：以選定觀念為中心瀏覽關係，搭配搜尋、學習導覽與複習重點。
- **觀念題組**：依教材重點決定題數，整組交卷後查看答案、說明及來源。
- **錯題補強**：針對尚待改善的重點準備新題，閱讀來源後進行下一輪練習。
- **學習紀錄**：保存題組、作答結果與學習進度，重新整理或登入後可接續。
- **教材更新**：追加來源後建立新版本；更新失敗保留已發布內容與學習紀錄。

## 開始使用

Studydy 可自行部署，使用 Docker Compose 建置及啟動。完整部署需要支援 Linux 容器的 Docker 環境，以及可供容器使用的 NVIDIA GPU。

教材內容由原生文字擷取與本機 Unlimited-OCR 處理，分析與出題透過 HTTP／HTTPS 呼叫已部署的 Gemma 模型服務。模型服務可放在自有主機、容器或 GPU 服務平台；模型版本與請求設定見 [runtime-lock.json](local_ai/runtime-lock.json)。

首次部署請閱讀 [安裝與啟動](docs/getting-started.md)，完成環境設定、OCR 權重準備及模型服務連線。使用操作見 [使用指南](docs/usage.md)。尚未配置 GPU 或模型時，可執行不需要模型的 [容器測試](docs/testing.md)。

## 技術與專案結構

| 目錄 | 技術與用途 |
| --- | --- |
| [frontend/](frontend/) | React、TypeScript、Vite 與 React Flow；提供教材、地圖與學習介面 |
| [backend/](backend/) | Python、FastAPI 與 PostgreSQL；負責教材處理、帳號、題組及學習紀錄 |
| [local_ai/](local_ai/) | Unlimited-OCR 子程序、模型下載工具與模型執行契約 |
| [ops/docker/](ops/docker/) | Docker 映像、Nginx 前端服務與文件轉換沙箱設定 |

## 資料與目前限制

教材原檔與學習資料保存在部署主機。AI 分析與出題會將必要的教材內容傳至設定的模型服務，使用前請確認該服務的資料處理方式。

AI 生成的觀念、關係與題目仍需對照教材確認；OCR 與文件轉換也可能影響文字、圖表及版面。自動化測試提供程式回歸驗證，不代表所有教材或模型內容已通過品質驗收。完整說明見 [限制](docs/limitations.md)。

## 文件導覽

| 我想了解 | 文件 |
| --- | --- |
| 安裝、設定、啟停與排錯 | [安裝與啟動](docs/getting-started.md) |
| 上傳、地圖、練習與教材管理 | [使用指南](docs/usage.md) |
| 模組、資料流與信任邊界 | [系統架構](docs/architecture.md) |
| 轉檔、Evidence、分析、版本與刪除 | [教材處理](docs/materials.md) |
| 題組、評分、補強與學習恢復 | [學習與評量](docs/learning.md) |
| 測試選擇、隔離環境與驗證範圍 | [測試](docs/testing.md) |
| 格式、模型品質與部署限制 | [限制](docs/limitations.md) |

## 授權與素材

目前尚未提供專案 LICENSE。第三方內容、AI 生成介面素材的來源與授權狀態見 [素材說明](THIRD_PARTY_CONTENT.md)。
