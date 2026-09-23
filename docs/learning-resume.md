# 學習進度與題組恢復

同一 learner/material/structure revision 對應持續保存的 StudySession。題目、AnswerEvent、no-safe concept navigation 與 assisted evidence 保持原樣。

## 讀取範圍

Knowledge Map／複習頁並行讀取教材索引、run 與 immutable Knowledge Structure，接著只取既有 `GET /v1/study-sessions/{sid}/progress`。不再為顯示 weak claims 重取整份 resume。等待中顯示讀取狀態，不把尚未取得 progress 當成沒有複習內容。

正式學習頁使用：

`GET /v1/materials/{material}/knowledge-structures/{revision}/study-sessions/{sid}/resume?run_id={run}&set_id={optional_set}`

回應 `study-resume/v1`：session、run/source identity、knowledge_structure、learner-progress/v1、assessment_sets 與 selected_set_id。明確題組 URL 恢復同一題組；預設選取當前觀念最新題組。唯讀操作不建立題目、重播生成或保存作答。

## 一致性與驗證

Progress 和 resume 共用一次 repeatable-read、read-only DB snapshot。教材結構、來源綁定與 owner scope 在邊界完成完整驗證；後續 context/view 投影共用已驗證輸入。作答正誤、題目私有答案、event watermark、assisted evidence 與 cycle 狀態仍核對。同次讀取不再完整驗證同一結構多次，也不以反覆重算來偵測並行提交。

並行交卷時，讀者得到提交前或後的一致 snapshot；不混用不同時點。操作仍由 guidance_revision／set_version 防止 stale 寫入。沒有跨請求私有資料快取。

## 繼續學習

初篩錯誤重點可直接補強；補強通過不增加獨立 mastery evidence。完成結果使用 backend next_action 的 advance／complete，套用 `guidance-apply/v1` 後清除上一題組 route，resume 同一 StudySession。

`study-session-focus/v1` 是明確選擇概念的操作，不能代替 guidance authority。第一次建立仍使用 `study-session-create/v1`；已有 state 時不重建或清空進度。完成狀態由 backend 保存，前端不自行宣告完成。
