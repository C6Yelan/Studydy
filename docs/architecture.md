# 系統架構

[文件入口](../README.md) · [教材處理](materials.md) · [學習與評量](learning.md)

## 資料流

~~~mermaid
flowchart LR
    UI[React] --> API[FastAPI]
    API --> DB[(PostgreSQL)]
    API --> Store[(Artifact Store)]
    DB --> Worker[串行 Worker]
    Worker --> Convert[隔離轉檔]
    Worker --> Evidence[原生文字／Unlimited-OCR]
    Evidence --> Model[Gemma HTTP 服務]
    Model --> Validate[來源與結構驗證]
    Validate --> DB
    Validate --> Store
~~~

Nginx 代理同源/v1 API。API 處理身分、讀寫邊界及建立工作；worker 執行轉檔、分析／檢核與題組準備。PostgreSQL 保存狀態與 metadata，檔案位於 STUDYDY_DATA_DIR/artifacts。

後端管理本機 OCR 子程序。外部語意模型獨立運行；模型離線時仍可登入及讀取已保存內容。

## 模組

| 位置 | 責任 |
| --- | --- |
| [runtime/api](../backend/src/runtime/api/) | HTTP 契約、認證、Origin、錯誤與公開投影 |
| [document_normalization](../backend/src/document_normalization/) | 沙箱轉檔、格式檢查與來源 mapping |
| [pdf_evidence](../backend/src/pdf_evidence/) | 擷取、Evidence 與分析流程 |
| [knowledge_map](../backend/src/knowledge_map/) | 概念、Claim、Relation、檢核與確定性建構 |
| [learning_adaptation](../backend/src/learning_adaptation/) | 題組、私密答案、評分、進度與導覽 |
| [runtime/storage](../backend/src/runtime/storage/) | 持久資料、migration、完整性與檔案恢復 |
| [workers.py](../backend/src/runtime/workers.py) | 工作領取、lease、執行與恢復 |
| [local_ai](../local_ai/) | OCR adapter、protocol 與模型執行設定 |

## 模型與驗證

模型提出觀念、重點、關係及題目候選；程式驗證來源、頁碼／區塊、字面值、schema、關係循環、授權與答案保密。Structured Output 只保證可解析，結果須完成來源綁定與驗證，再計算 revision 並發布。

只有 prerequisite 關係影響建議學習順序；其他型別保留語意與方向。定義見 [structure_rules.py](../backend/src/knowledge_map/structure_rules.py)。

模型、revision、套件、prompt 與 token 預算以 [runtime-lock.json](../local_ai/runtime-lock.json) 為準。部署位址與 token 由環境提供，工作與題組保存建立時的快照；讀取時核對該快照，不以現行設定替換生成身分。

一般讀取核對 metadata；發布及實際使用檔案時核對 bytes。來源檔損毀會阻止該檔案使用或新結果發布，不會自行刪除已保存地圖。

## 授權與一致性

- Email 正規化後唯一，密碼以隨機 salt 的 scrypt 保存；參數見 [learner_session.py](../backend/src/runtime/learner_session.py)。
- API 以 HttpOnly session cookie 與 owner scope 授權；寫入檢查 Origin，私人回應使用 private/no-store。前端身分提示不授予資料權限。
- 登出使 client 與私人畫面失效，延遲回應不能恢復另一個帳號資料。
- Material／Study 鎖、版本與唯一約束保護並行操作；lease／worker token 阻擋過期結果發布。
- Progress／resume 在同一唯讀 snapshot 投影，整組交卷在同一交易保存。
- DB 與檔案透過 staging、quarantine 及 reconciliation 保持一致。

Migration runner 核對 [SQL序列](../backend/migrations/) 與 checksum，每版 schema 及帳本同交易提交。API 完整定義以 [OpenAPI](http://127.0.0.1:4173/v1/openapi.json) 及 [models.py](../backend/src/runtime/api/models.py) 為準。
