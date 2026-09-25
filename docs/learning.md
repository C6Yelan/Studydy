# 學習與評量

[文件入口](../README.md) · [使用指南](usage.md) · [教材處理](materials.md)

## 學習身分與觀念範圍

同一 learner、教材與 Knowledge Structure revision 對應持續保存的 StudySession。重複建立意圖接續同一份狀態；讀取不建立 session 或重算答案。

不同觀念可各自保留進行中的題組，同一 StudySession／Concept 不重複建立 active set。題組的 owner、KS、Concept 與成員關係決定作答授權，不依目前導覽焦點判定。

## 題組準備與發布

計畫從單一觀念的可出題重點決定動態題數。題組保存計畫、執行快照、逐題狀態與工作 lease。Worker 在 DB 交易外生成、檢查候選，再保存驗證結果；正式發布後的成員與來源不可改寫。

生成器提出候選，checker 只接收來源、題幹與重排選項，不接收生成器的答案鍵。發布要求唯一答案與候選正解一致、來源有效且不重複既有題目。

正確性與品質排序分開：只有通過安全檢查的候選才比較品質提示。較弱的干擾選項等提示不等於無安全題；結構或語意安全失敗不得當成通過。

準備中的題目與私密答案不由公開 API 暴露。失敗可明確重試未完成題，或在有已驗證題目時選擇部分發布；未發布重點維持未檢測。

## 整組交卷

整組提交包含已發布題目的選取答案、expected_set_version 與 Idempotency-Key。少答、重複成員、錯誤題目／選項或不屬於本題組的成員，均拒絕整次提交。

答案、事件序號及題組完成狀態在同一交易保存；中途儲存失敗全部回滾。評分依後端私密答案執行，公開題目不帶正解欄位。

同意圖重播回同一結果，併行提交不能重複計分。已保存答案不被新提交覆寫。版本衝突需要先同步，回應遺失可查回已保存結果，不自動當成未交卷。

## 錯題補強

補強題組保存其初篩歸屬，只包含當前仍待改善的重點。每輪建立都需要明確操作；有同觀念 active set 時先接續該組。

| 重點結果 | 意義 |
| --- | --- |
| diagnostic_pass | 初篩答對 |
| needs_review | 初篩答錯，且最新補強尚未答對 |
| remediation_pass | 新補強題已答對 |
| unanswered | 尚未作答，不視為答錯 |
| unavailable | 未發布題目，不視為答錯或通過 |

本輪 outcome 依 active set、待改善及未檢測狀態投影為 in_progress、needs_review、passed 或 incomplete。題組生成失敗不能變成學習者答錯。

補強作答標為 assisted，計入本輪改善，但不增加或恢復獨立掌握證據。掌握要求每個 Claim 具備不同的合格正確題與最新正確作答，實際規則由 [learning_states.py](../backend/src/learning_adaptation/learning_states.py) 定義。

## 恢復與下一步

Progress 與 resume 共用一致的 DB snapshot，核對 session、run、KS revision、題組成員與 event watermark。題組 URL 可明確選取原題組；重新登入、GET 與 reload 不新增題目或 AnswerEvent。

初篩與補強結果共同決定下一步：接續 active set、開始補強、前往下一觀念或完成。前端透過 guidance/apply 提交後端給出的 guidance_revision，不能以自己的路徑索引代替授權。

## 教材更新後的學習

來源、文字及區塊位置皆一致且可唯一對應的 Claims，可以投影承接先前作答證據。變更、拆分、合併或匹配有歧義時不自動承接。

承接不複製 AnswerEvent，不改題目、正誤、時間或來源 revision。原版本學習仍可讀，新的學習狀態由使用者明確建立或接續。

功能測試使用受控模型回應，不能作為真實出題品質驗收；見 [測試](testing.md) 與 [限制](limitations.md)。
