# 限制

[文件入口](../README.md) · [測試](testing.md)

## 部署

需要 Linux 容器、Docker 及 kernel namespace 能力；本機 OCR 另需 NVIDIA GPU、驅動與容器整合。資料檔案系統須支援 Linux 權限。Windows／macOS 沒有原生啟動流程，部署不綁定 SSH 或 RunPod。

預設網站只綁 loopback 並使用非 Secure cookie；公開服務需另配 TLS、public origin 與 Secure cookie。模型服務、認證及主機能力由部署者提供。

## 文件與擷取

單檔 100 MiB 上限不保證所有文件都可轉換。Office 字型、圖表、公式及版面可能改變；DOC／PPT 不提供推測的原始段落／投影片定位，DOCX mapping 可能有歧義。

Markdown 不載入圖片或執行 raw HTML。受保護、損毀、禁止主動內容或外部 relationships 的文件可能被拒絕。

原生文字可讀不代表整頁內容完整。OCR 可能漏字或誤辨；來源 hash 與定位正確也不保證辨識文字或語意正確。

子程序有時間及資源上限；NPROC 按 UID 計算，不是整個工作所有程序的合計配額。

## 模型與學習品質

Concept、Claim、Relation 及題目仍需人工對照來源，尤其是合併概念的適用範圍、關係方向與題目歧義。程式能檢查來源、字面值、結構、重複及答案一致性，不能保證所有語意正確。

教材語意審查的驗收要求包括可用率至少 85%、來源／版本綁定、完整 Path、如實回報失敗、私密答案保護、零觀察到的錯誤掌握判定及 runtime 可用性；此門檻不代表產品已全面達標。

partial／needs_review 結果需複核，地圖與學習頁的品質提示尚未完整延續。教材檢核不進行跨批次整合；先備與字面值保護可能保留片段或拒絕合理改寫。沒有合格候選時可能只發布部分題目。

練習答對、補強通過及長期掌握是不同指標。更換模型、revision 或 prompt 後須重新驗證，其他素材或環境的結果不能直接套用。

## 資料與功能

教材與持久資料保存在部署主機，AI 操作會傳送必要內容至設定服務。帳號沒有 Email 所有權驗證、重設密碼、OAuth 或 MFA。

教材重整只有 API 入口；關頁不會取消工作，取消也不保證停止在途推論。已刪除資源頁的操作入口及取消後文案仍有待改善。

## 測試覆蓋

自動化以合成來源、受控模型、隔離 DB 與瀏覽器為主，未窮舉所有裝置、文件或輸入。真 API／DB 的「追加來源後由 UI 接續原學習」尚無完整 browser fixture，不能把各層測試合稱同一端到端驗證。

測試通過提供程式回歸證據，不是全面安全、格式相容或模型品質保證。

## Podcast

Podcast 使用 Luna 腳本與本機 CosyVoice 3 RL／B 聲線生成，已依本次批准部署至正式站。桌機與手機尺寸的 Chromium 介面已用 Playwright MCP 驗證，實體手機及其他瀏覽器仍待確認。來源固定於建立時的 KS，原本的 `needs_review`／缺頁資料仍保留；自動來源核對與有效 WAV 不保證語意、術語發音或台灣口音全部正確，尚未完成人工音質驗收。

音訊完成所有分集後才開放播放。取消會阻止晚到結果發布，但不保證立即停止在途推論；程序中斷後以最多 15 分鐘的 lease 到期回收未確認步驟。已保存分集不重做。播放位置只在同一帳號／同一瀏覽器保存，不同步到其他裝置。


Teaching beat／精確 part 引用、雙 blocking review、Voice context、Assessment handoff、音訊 mastering 及有限 reveal 的工程回歸使用合成資料與受控 provider，與上述歷史部署驗收分開。此變更未執行真實 Luna／CosyVoice／Whisper／GPU 教學品質驗收，也未部署到產品。自動 review 不能替代人工核對來源、比喻、對話品質、發音或 trim 是否切掉弱音。30fps 雖有 CPU 收益，半速播放的 trace 更新頻率降低，仍保留 60fps；原教材圖片素材 deferred。
