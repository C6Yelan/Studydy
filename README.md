# Studydy

Studydy 將自己的教材整理成可回查來源的知識地圖，再透過觀念題組與錯題補強協助學習。

上傳教材 → 確認來源 → 分析與檢核 → 瀏覽知識地圖 → 整組作答 → 補強錯誤重點。

## 主要功能

- **多來源教材**：支援 PDF、DOC／DOCX、PPT／PPTX、UTF-8 TXT 與 Markdown；逐檔保存原文與 PDF 預覽。
- **來源可回查**：觀念、重點與關係保留 Evidence，能回到對應教材、頁碼及區塊。
- **知識地圖**：以選定觀念為中心瀏覽關係，搭配搜尋、學習導覽與複習重點。
- **觀念題組**：依教材重點決定題數，整組交卷後查看答案、說明及來源。
- **錯題補強**：針對尚待改善的重點準備新題，保存題組與作答，重新登入後可接續。
- **教材更新**：追加來源後建立新版本；更新失敗保留已發布內容與學習紀錄。

## 執行需求

部署使用 Docker Compose。Python 3.12、前端、PostgreSQL 18、LibreOffice、bubblewrap 與中文字型由容器提供；主機需支援 Linux 容器，並提供可供容器使用的 NVIDIA GPU。

完整 AI 流程需要 Unlimited-OCR 權重，以及可透過 HTTP／HTTPS 存取的 Gemma 服務。RunPod 是選用供應商，後端不管理語意模型的啟停。模型、套件版本與請求設定以 [runtime lock](local_ai/runtime-lock.json) 為準。

**首次使用請從 [安裝與啟動](docs/getting-started.md) 開始。** 從 .env.example 建立設定，compose.yaml 提供完整部署、OCR 下載工具與選用 SSH 通道，資料統一保存在專案內 data/。尚未配置 GPU 或模型時，可先執行獨立的 [容器測試](docs/testing.md)。

教材原檔與持久資料保存在本機；分析與出題會將所需教材內容傳至設定的語意模型服務。生成內容仍需對照來源，詳見 [限制](docs/limitations.md)。

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

## 專案目錄

| 目錄 | 用途 |
| --- | --- |
| [backend/](backend/) | FastAPI、教材處理、知識結構、學習狀態與 PostgreSQL 儲存 |
| [frontend/](frontend/) | React／TypeScript 介面與 Playwright 測試 |
| [local_ai/](local_ai/) | OCR 子程序與模型執行契約 |
| [ops/docker/](ops/docker/) | 容器映像、前端代理與轉檔 sandbox 規則 |
| [prototypes/document_normalization/](prototypes/document_normalization/) | 合成文件工具與獨立轉檔探查 |

Repo 尚未提供專案授權文件；素材來源與授權待確認範圍見 [素材說明](THIRD_PARTY_CONTENT.md)。
