# 系統架構

[文件入口](../README.md) · [教材處理](materials.md) · [學習與評量](learning.md)

## 系統組成

~~~mermaid
flowchart LR
    UI["React 前端"] --> API["FastAPI"]
    API --> DB[("PostgreSQL")]
    API --> Store[("本機 Artifact Store")]
    DB --> Worker["串行 Runtime Worker"]
    Worker --> Convert["隔離文件轉檔"]
    Worker --> Evidence["Native Evidence／Unlimited-OCR"]
    Evidence --> Semantic["外部 HTTP 語意模型"]
    Semantic --> Validate["來源、結構與內容驗證"]
    Validate --> DB
    Validate --> Store
~~~

前端使用同源 /v1 API，部署時由 Nginx 代理到 backend 容器。API 處理身分、讀寫邊界與工作建立；worker 執行來源轉檔、教材分析／檢核與題組準備。PostgreSQL 保存工作狀態、帳號、學習與內容 metadata；檔案保存在 data/artifacts。

後端只啟停自己管理的 OCR 子程序，不管理外部語意模型服務的生命週期。API 啟動檢查設定，模型可用性由實際 AI 操作檢查；登入與已保存內容讀取可在模型離線時使用。

## 模組責任

| 程式位置 | 責任 |
| --- | --- |
| [runtime/api/](../backend/src/runtime/api/) | HTTP 契約、身分、Origin、錯誤與公開投影 |
| [document_normalization/](../backend/src/document_normalization/) | 隔離轉檔、格式檢查及來源 mapping |
| [pdf_evidence/](../backend/src/pdf_evidence/) | 原生文字、OCR、Evidence 與語意分析流程 |
| [knowledge_map/](../backend/src/knowledge_map/) | 觀念／重點／關係、確定性建構、檢核及結構驗證 |
| [learning_adaptation/](../backend/src/learning_adaptation/) | 題組、私密答案、作答、進度與下一步 |
| [runtime/storage/](../backend/src/runtime/storage/) | 持久資料、來源驗證、migration 與檔案恢復 |
| [runtime/workers.py](../backend/src/runtime/workers.py) | 工作領取、啟停、恢復與執行協調 |
| [local_ai/](../local_ai/) | OCR protocol、模型 adapter 與 runtime lock |

## 模型與程式的責任邊界

模型提出語意：觀念邊界、Claim 意思、關係理由、題目候選與盲解檢查。程式負責來源身分、頁碼／區塊、字面值、schema、關係循環、不可變性、權限、答案保密、評分、冪等與交易。

模型回傳符合 Structured Output，只代表資料可解析。正式 Knowledge Structure 必須完成來源集合綁定、內容驗證並計算 revision，才能發布。內部草稿沒有正式 schema／revision。

只有 prerequisite 關係能影響建議學習順序。part_of、application、example、contrast 保留其語意與方向；Document Tree 依教材結構建立。關係型別定義見 [structure_rules.py](../backend/src/knowledge_map/structure_rules.py)。

## 執行設定與來源身分

模型、revision、套件契約、token budgets 與 prompts 以 [runtime-lock.json](../local_ai/runtime-lock.json) 為單一設定來源。

實際模型 HTTP 位址由 STUDYDY_SEMANTIC_BASE_URL 提供，Bearer token 由 VLLM_API_KEY 提供。部署覆寫只影響連線，不改封存的 lock、binding 或內容 hash；快照中的預設位址不代表部署覆寫後的網路終點。

教材工作與題組各自封存執行快照。讀取已保存內容時，核對產物與生成當時的快照，不能以目前設定冒充其身分。修改模型設定不會改寫既有產物或答案。

一般地圖／題組／進度讀取驗證資料庫 metadata，不反覆掃描所有原檔；發布與實際使用來源檔案時核對對應 bytes。來源檔損毀會阻止該檔案使用或新結果發布，不等於已保存地圖自動消失。

## 帳號與資料隔離

帳密保存在 learners，登入 session 是 API 的授權依據。Email 正規化後唯一；密碼採隨機 salt 的 scrypt，實際參數以 [learner_session.py](../backend/src/runtime/learner_session.py) 為準。

Session token 使用 HttpOnly cookie。寫入請求檢查 Origin；資源以 owner scope 查詢，API 與教材回應使用 private/no-store。前端保存的 learner identity 提示只協助恢復頁框，不授予資料存取權。

登出或 session 失效時，前端撤除私有畫面並使既有 client 失效；延遲回應不能重新展示前一帳號資料，失敗寫入不自動換身分重播。

## 一致性與恢復

- Material／Study 鎖、唯一約束及版本欄位保護並行操作。
- 工作持有 lease／worker token；失效 worker 的晚到結果不得發布。
- Progress 與 resume 在同一個唯讀、repeatable-read snapshot 中投影。
- 整組交卷以同一交易保存答案與題組結果，中途失敗全部回滾。
- DB 與檔案寫入採 staging／quarantine 與 reconciliation，依實際 commit 結果保存、還原或清理。

Migration runner 逐份套用 [領域 SQL](../backend/migrations/)，核對 checksum、序列及併發鎖。不要靠改寫帳本跳過不一致。

## API 參考

產品路由以 /v1 為前綴。預設前端入口下的 [OpenAPI JSON](http://127.0.0.1:4173/v1/openapi.json) 提供實際 request／response 定義；repo 內由 [app.py](../backend/src/runtime/api/app.py) 與 [models.py](../backend/src/runtime/api/models.py) 定義。

功能文件解釋工作流程、交易與副作用，不另外維護一份完整欄位清單。
