# Studydy Agent 開發規則

## 專案與範圍

Studydy V1 以 Knowledge Map 與四個 Agent 的真實產品流程為核心。採 greenfield、Agent-first、Version／Slice／Task 與 Completion Gate。

禁止：

- 先建立完整基底、全 Agent stub 或 Walking Skeleton Slice。
- 為未發生需求建立通用 Agent framework、workflow engine、service／repository／adapter 或大型 schema。
- 自行擴張到下一個 Slice。
- 為通過 Demo 硬編碼結果或降低 critical Gate。

## 最小 Context

每次只讀：

1. 本 `AGENTS.md`
2. 當前 Slice 文件
3. `current_task.md`
4. 已批准 `plan.md`
5. 前一 Slice `handoff.md`
6. Task 明確引用的契約或決策

不得預設載入整份規劃包、全部 Slice、所有研究或歷史 handoff。

## PLAN

非簡單任務必須先有 PLAN，至少包含：

- 目標與可觀察行為
- 包含／排除範圍
- affected files
- bounded tasks
- 契約變更
- tests／Evaluation
- 安全與 dependency 影響
- 風險、回復、Prune 候選
- Completion Gate

PLAN 未經使用者批准不得實作。

## 角色

- Explorer：只讀分析與 PLAN，不改程式。
- Implementer：只做批准內容，不自行 commit／push。
- Evaluator：執行測試、Golden、Regression，不改 production code。
- Reviewer／Pruner：找 bug、安全、缺測試與過度實作，不直接改寫。
- Doc Curator：只做必要的最小文件更新。
- Supervisor：維持 Slice、整合報告；所有重要決策由使用者批准。

同一時刻只能有一個 production writer。

## 實作

- 優先最小、局部、可替換的實作。
- 只有至少兩個真實情境重複同一問題，才考慮共用抽象。
- 新 dependency、資料表、服務層、queue、狀態機或通用介面都需 PLAN 明確批准。
- Structured Output 只保證格式；仍需 deterministic checks、Golden Evaluation 與人工複核。
- 所有重要輸出保留 ID、schema version、locator、provenance、狀態與原因。

## Evaluation／Review／Prune

Slice 完成前必須：

- 執行相關 unit、integration、contract、Golden 與 Regression。
- critical cases 全通過。
- 小樣本顯示 `n/N`。
- 產生 evaluation report。
- Reviewer 確認 PLAN 符合性、安全、缺測試與資料錯誤。
- Pruner 移除未使用程式、重複邏輯、推測性欄位、無必要 helper／service／adapter／dependency 與過度 defensive code。

## Git

- 後端：`be/feature-* → PR → be-dev`
- 前端：`fe/feature-* → PR → fe-dev`
- 不直接在 `be-dev`、`fe-dev` 或 release target 實作。
- 跨端先批准 contract／fixture，再後端 PR，最後前端 PR。
- commit、push、merge、branch reset／delete、default branch 或 tag 變更都需使用者批准。
- 不 force push，除非使用者明確批准風險。

Commit／PR 前檢查：

```text
git status
git diff
git diff --cached
```

確認沒有 secret、私人教材、`docs_local/`、runtime artifact、無關變更或未批准文件膨脹。

## 私有資料與 Provider

- `.env`、API key、token、連線字串、私鑰與憑證不得提交或輸出。
- `docs_local/`、私人教材、Golden 人工標註與 runtime artifact 不得提交。
- `backend/.env.example` 只可放 placeholder。
- E16 未批准前，不得把私人教材送往外部 Provider。
- 只傳送完成當前判斷所需的最小內容；log／trace 不保存教材全文或 chain-of-thought。

## 文件

- 穩定規則放 `AGENTS.md`。
- 長期產品與 Roadmap 放 planning context。
- 當前工作只放 workflow files。
- 不保存聊天紀錄、研究過程或未採用方案，除非它仍是待決策項目。
- 行為、契約、命令或架構未變更時，不更新正式文件。

## 每次工作結束

回報：

1. Files changed
2. Behavior changed
3. Tests／Evaluation run
4. Risks or limitations
5. Intentionally not done
6. Required user decisions
