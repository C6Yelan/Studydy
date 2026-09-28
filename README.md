# Studydy

將個人教材整理成可回查來源的知識地圖，搭配觀念題組、錯題補強與持久學習紀錄。

**上傳教材 → 確認來源 → 分析與檢核 → 知識地圖 → 題組練習 → 錯題補強**

支援 PDF、DOC／DOCX、PPT／PPTX、UTF-8 TXT 與 Markdown，可追加來源建立新版本，並承接符合條件的作答證據。

## 部署與使用

以 Docker Compose 提供 React 前端、FastAPI、PostgreSQL 與隔離轉檔。教材擷取使用原生文字與 Unlimited-OCR；分析與出題呼叫已部署的 Gemma HTTP／HTTPS 服務。文字不足或重要圖片區域由本機 Unlimited-OCR 處理。

- [安裝與啟動](docs/getting-started.md)：主機需求、設定、服務管理與備份。預設入口 http://127.0.0.1:4173。
- [使用指南](docs/usage.md)：教材、地圖、練習與進度恢復。
- [測試](docs/testing.md)：不需模型的容器測試及隔離 API／瀏覽器回歸。

## 開發文件

| 主題 | 文件 |
| --- | --- |
| 模組、資料流與信任邊界 | [系統架構](docs/architecture.md) |
| 轉檔、Evidence、來源與版本 | [教材處理](docs/materials.md) |
| 題組、評分、補強與學習狀態 | [學習與評量](docs/learning.md) |
| 格式、模型品質與覆蓋範圍 | [限制](docs/limitations.md) |

原檔與持久資料保存在部署主機；AI 操作會傳送必要內容至設定的模型服務。模型結果須對照教材確認，程式測試不代表內容品質已全面驗收。

除另有標示外，Studydy 專案原創程式碼採 [MIT License](LICENSE) 授權。第三方元件、模型與介面素材可能適用不同條款，詳見 [第三方內容](THIRD_PARTY_CONTENT.md)。
