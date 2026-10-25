# 測試

[文件入口](../README.md) · [安裝與啟動](getting-started.md) · [限制](limitations.md)

## 動態語音停頓

`test_podcast_audio_repair.py` 驗證短回應、長句、問句、換人與換段的優先順序、原文字數門檻，以及保留的片段靜音不被重複加算。`test_podcast_spoken_input.py` 核對朗讀展開仍保留原句、句尾引號與小數不破壞句界。DSP 與來源測試不代替實際聽評；新生成節目另核對新音訊的字幕對齊與講稿內容。

`test_podcast_align.py` 驗證字幕漏辨句首時，只採用局部音訊重新辨識到的句首時間；未辨識到開頭、時間重疊或無關音訊都不會產生猜測的修正。字幕文字與教學段落時鐘保持不變。

`test_podcast_audio_repair.py` 檢查孤立脈衝、子音接母音、短有聲字詞、淡化邊緣時保留內部樣本、複數與字母音節，以及不依句子身分的調速；另涵蓋整句英文冠詞、中英縮寫、數字／單位、短回覆、換人停頓與變速失敗。正式處理使用模型原始片段與講稿／speaker 邊界，再統一正規化音量。合成訊號只驗證保護條件；不同講稿、弱音、換人、英文與單位的效果仍需真人聽評。

長音訊的細字幕對齊失敗時，會以含前後文的短音訊視窗重辨識長段 ASR 空缺；詞時間仍來自實際音訊。補入結果須通過完整原稿覆蓋與時間錨點驗證，失敗仍保留已驗證的段落時序。`test_podcast_align.py` 驗證重疊視窗不重複收取核心詞時間；實際語音與畫面另行核對，不以合成測試宣稱聽感通過。

接點淡化與孤立句首脈衝是不同問題，刪除脈衝時須保護正常子音、短有聲字詞與後續語音。停頓須扣除兩側既有安靜區；語速依實際發音量及足夠長片段估計，短語氣詞與未知英文發音不自動調速。

語速估計會受強調與句中停頓影響，數值一致不能代替自然度，短樣本音色偏好也不能當作長對談驗收。接點修正不能代替縮寫與單位發音的獨立核對，仍須以不同講稿、弱音與換人情境作真人聽評。

## Podcast 現行驗證範圍

講稿（`source-script/v14`）生成後、發布前逐段（beat）對照引用原文核對，沿用筆記逐段核對的請求形式：每段只送本段台詞、引用重點的原文區塊（含原檔、頁碼、區塊順序與位置）及同頁補充區塊（只供讀表頭、欄名與符號定義），核對數值、條件、否定、等號邊界、標籤、方法與結果；重點在最後講解它的正文段落合併前段判斷是否實際講解（只提名稱、只念數字或只在收尾回顧一句帶過都不算；表格只需講支持結論的關鍵數字）；收尾段不判斷涵蓋。整集涵蓋由程式檢查：本集應講、卻沒有任何正文段落引用的重點，分到最後引用同一概念的正文段落（概念都沒講到就分到最後一個正文段落），列為該段缺漏並沿用同一段的重寫與重核；全部有引用時不增加呼叫。沒有引用的開場、串場與收尾只能有打招呼、路線、串場、提問與下集主題名稱，出現教材事實即不符。主持人自稱由程式逐句寬鬆檢查，不另呼叫模型：說話者台詞出現自己的名字就列為該句不符，併入同一段的重寫與重核。只排除自我介紹與節目介紹（「我是／我們是／這裡是／歡迎收聽…」，以及「這是／謝謝收聽」後接「某某和某某的」節目名稱）、叫對方的名字，以及名字只是詞語的一部分：前面緊接非指人的中文字（「大小安排」「最小安全」；「我們、由、讓、請、跟著、陪你的、謝謝收聽」等指人語境仍算），或名字加後一字是原文的詞、名字末字與後一字在原文或講稿別處也當詞出現（原文有「小型語言模型」時的「小語言模型」）。重寫後若只剩自稱、事實與涵蓋都通過，照常發布，工作日誌 `script_review` 只記 `self_naming_after_rewrite` 的台詞位置；事實或涵蓋仍不符才整集失敗。不符只重生該段並重核一次，重寫不得刪掉須在本段講解的重點引用，分配來的缺漏重點也必須被引用並講解；仍不符整集以 `PODCAST_SCRIPT_NEEDS_REVIEW` 失敗、不發布，使用者可接續生成重試。核對回應缺項或位置不合法時以 `LUNA_GENERATION_FAILED` 失敗，不當成通過。核對提示由 runtime lock 的 `podcast_script_review`（`podcast-script-review/v1`）封存，provider 程式與 lock 不一致時不生成。分鏡仍生成一次，不執行獨立品質評分、風格相似度／輪替／附和檢查或動態字數預算。

模型講稿只輸出角色、台詞與來源編號；程式將合法編號連回原 claim 的 Evidence，保存格式沿用 v2。核對結果不寫入講稿（不加 review 欄位），只在 provider 工作日誌記模型呼叫次數與被退回的台詞位置／來源編號，不記台詞或理由；v12／v13 等舊稿原文與 hash 保留，照常播放。基本格式、來源索引／Evidence 不越界、實際語音容量、媒體完整性與時序身分仍檢查。音量正規化照常執行，量測值僅記錄，不因偏離目標的小幅差異否決整集。

分鏡模型只輸出每頁標題、圖解類型、節點文字與訊息方向，以及每個節點逐字取自講稿的 `anchor` 與 0～2 個逐字取自節點文字的 `key_terms`，仍是一次呼叫。每段講稿可拆成 1～3 頁（全集最多 24 頁），第 2 頁起由逐字頁 anchor 決定換頁，對不到時改用前後換頁點之間居中的實測起點；實測起點不足時多出的頁併入前一頁，不丟內容。exchange 至少兩則訊息。分頁依原稿段落與 ASR cue 歸屬建立；秒數、cue／字幕索引由程式以逐字 anchor 換算成實測起點，對不到就退回頁首或下一個實測起點，不整份失敗；不符的重點詞直接丟掉。concept／flow 以聚焦版面一次只亮正在講的那張卡，comparison 點亮正在講的那欄，exchange 依 anchor 出現訊息；內容停在底部字幕帶之上。沒有依特定教材、標題或句子設例外，也不以截字或補造內容修復結果。

對應測試：`test_podcast_provider.py`、`test_podcast_beats.py`、`test_podcast_episode_plan.py`、`test_podcast_audio.py`、`test_podcast_video_service.py` 及 runtime 的 Podcast／video 案例。測試以受控模型回應覆蓋核對通過、不符只重生該段、仍不符不發布、無引用段落含事實主張、沒被引用的重點依概念分段與重寫後仍未引用、核對回應不完整及 lock 不一致；這些不代表核對模型的判斷正確，實際品質以真實生成與審稿回報。

Podcast、音訊或影片驗證通過，不會把原教材的 `partial`／`needs_review` 改成已驗收。影片驗證失敗時不得發布成品，也不能因此抹去已保存且可播放的原音訊。

## Podcast 取消與刪除

`backend/tests/test_podcast_process.py` 以本機 HTTP 與真實測試子程序驗證取消、等待資源鎖時取消、提早／遲到的取消、不同 lease 隔離、有明定期限的操作逾時及子程序群組回收，另以推進單調時鐘確認無期限的音訊程序不受原時限影響、長時間工作仍可取消。`backend/tests/runtime/materials/test_podcast_cancellation.py` 使用隔離 DB，驗證正在產稿／製作音訊時取消或刪除會停止子程序，且下一筆節目能完成；另核對取消會刪除已生成的音訊與所有子工作、保留原教材，以及音訊已完成時仍可取消整份節目。音訊租約使用縮短到期時間的隔離案例，驗證正常續期可跨過原到期點、失聯租約不會復活。這些測試不呼叫真實模型、不使用產品 DB，也不宣稱生成內容品質已驗收。

## Podcast 影片選項、FLAC 與帳號空間

`runtime/materials/test_podcast_storage.py` 使用隔離 DB 與合成音訊（含 FFmpeg 形式的 LIST 區塊），不呼叫模型：FLAC 解碼還原原 WAV、損毀拒絕；沒勾影片不建立影片工作、播放與 Range 回 `audio/flac`、MP3 匯出與收聽進度；勾選影片時 provider 收到原 WAV、刪除單集影片只移除影片 artifact 與檔案且時間軸、音訊不變、不被接續生成重建；串流 Range 只在首次整檔核對、檔案改寫後重新核對並拒絕；帳號空間計入 artifacts／分析目錄、分段上傳以宣告大小保留（上限比已用多 1000 bytes 時，兩份宣告 800 bytes 的分段上傳只接受一份，後續段不得超過宣告大小）、慢速寫入不持有帳號鎖（另一份教材的上傳不被拖過 5 秒）、帳號鎖被占用時回可重試的 `STORAGE_BUSY`、同 key 同時重送只建立一份來源、保留額度在接受時固定（全站平均變動不重新定價）且在成品寫入的同一交易刪除、冪等重送不因額滿失敗、failed／cancelled 節目接續生成補建缺少的影片並先核對保留、cancelled 影片恢復前核對額度，上傳、建立 Podcast、生成／重試／補建影片超過上限時回 `STORAGE_QUOTA_EXCEEDED`，閱讀、播放與刪除照常；勾選影片的節目按接續生成補建缺少的影片工作、沒勾選的不補；WAV→FLAC 維護腳本的 dry-run 不改任何資料、正式轉換備份與逐位元核對、損毀檔保留原狀並回報、DB 更新失敗還原 WAV、中斷後下次執行收尾、另一程序持有執行鎖時不動任何暫存、fsync 失敗不留 `.tmp`、峰值空間預檢（同盤合併、分盤各自核對）、每檔落地前依實際 FLAC 大小核對剩餘空間並停在不足的那一檔，且影片、分鏡對齊、時間軸與進度前後相同。`test_podcast_storage_migration.py` 在 0019 資料上套用 0020，核對既有列不變、既有節目 `video_enabled=true`、FLAC 約束與空的保留表。`e2e/mock/podcasts.spec.ts` 覆蓋建立表單的影片選項、純音訊播放、刪除單集影片（含其他分集仍在製作時）、只計預期影片的進度分母，以及列表對待補影片的舊節目顯示「影片待補」；`e2e/api/podcast-interaction.spec.ts` 由真 API 確認 Chromium 載入 FLAC。

第三次複查的回歸案例另覆蓋：同 key 跨普通／分段入口與不同大小意圖不能沿用舊保留；同意圖並行重送只有一個寫入者、另一個回可重試的 `STORAGE_BUSY`；上傳、建立 Podcast、prepare 交錯時遵循帳號鎖在前並完成；分段重新宣告超額後舊檔與保留不變；failed／cancelled 節目接續時同時恢復 failed 影片；同來源 cancelled 影片 prepare 不排隊也不占額度；不存在教材的普通／分段上傳回 `RESOURCE_NOT_FOUND`。新增租約接手回歸：A 過期、B 接手後，A 遲到失敗或嘗試發布不刪 B 保留，C 仍因額滿被拒，B 可完成；維護清除後的舊單次請求不能發布、舊分段須重新開始。鎖序案例驗證新順序可完成，不宣稱測試本身重現舊版死鎖。

## 媒體來源

`test_media_sources.py` 不連網，驗證 YouTube 網址（Shorts、帶清單參數的單支影片、純播放清單拒絕）、直播／會員／私人／年齡限制／超過 2 小時的拒絕原因、上傳者字幕的語言順序與不用自動字幕、WebVTT 解析、2 分鐘時間段分頁、長度上限、圖片組順序與格式檢查、EXIF 轉正與嵌入壓縮影像（不以未壓縮像素嵌入），以及逐字稿 PDF 一段一頁與時間 mapping。`runtime/sources/test_media_source_flow.py` 使用隔離 DB 與真實沙箱，語音辨識及 YouTube 讀取以替身取代：分段上傳的位移檢查、最後一段重送不重複建立、音訊轉成時間段頁面並由媒體端點以 Range 播放、影片只保存音軌、圖片組一張一頁、YouTube 只保存參照與逐字稿、無字幕時辨識後刪除音軌、拒絕原因不建立來源、辨識失敗可重試、過期的分段上傳與中斷轉換暫存清除，以及 0016 的 artifact 型別與大小限制；音訊與 YouTube 另沿正式分析流程發布後核對來源回查的時間標籤與播放位置。`e2e/mock/media-sources.spec.ts` 驗證圖片組排序與打包順序、音訊分段上傳、YouTube 輸入與播放清單提示，以及來源視窗從引用時間播放原音或 YouTube。這些替身測試不代表真實辨識、字幕或 YouTube 可用性；真實驗收另以公開素材執行。

## 文字筆記

`test_material_notes.py` 核對合法來源、逐段核對不能遺漏正文／公式／表格位置、否定結果必須重生該段並重核、其他已通過段落不重寫，仍失敗不得發布、完整筆記全部重點涵蓋、重點整理 A4 一頁及固定可讀字級、按實測稿高縮減（段數上限向上取整）與首稿或重排後有空間時至多增補一次、內容修正後超頁必須重排再核對、註記精確對應與一次補正；引用補正回應少了目標段落時須回報 NOTE_FORMAT_INVALID，並由正式 step 保存同一錯誤碼。包含同 Evidence 的不同節錄完整保留，重點排序、ID 重建及程序重啟不改變註記對應。`runtime/study/test_note_queue.py` 驗證自動重點整理／按需完整筆記、擁有者與 Origin、重送不重複排程、失敗保留發布內容、租約恢復、過期 worker 不能覆寫，以及新版的原段落快照與來源保留。另驗證同頁補充表頭以正式引用發布且可回查來源、不改已發布知識結構；逐段核對包含每個列入重點的實質涵蓋，漏回覆、虛構筆記位置或以來源編號冒充解說位置都失敗；同點跨段在最後出現處合併已核對前文，不增加固定核對輪次。重點整理由程式移除多段各自未解說的重點（全稿所有出現處及只屬於它的來源），只重核引用改變的段落、沒有重點的段落刪除、全稿刪光才失敗；與事實問題混合時先移除再局部重寫；局部重寫一次仍不符就刪段、其他段落發布；程式整理引用（含局部修正回應）後交給核對；重排四次仍超頁從末段整段刪除、不裁切文字；完整筆記的局部修正回應可做引用補正，且仍不得遺漏或刪除重點。另核對完整筆記依地圖章節顯示真實分區進度，相同契約失敗重試只呼叫尚未完成的批次、契約變更時清掉未發布計畫與批次後能重新完成；內容核對拒絕時不保存為完成批次，既有發布內容保留。正式 step 的請求分派也驗證內容核對提示與回應 schema，發布後清除暫存批次內容。`test_notes_migration.py` 在含真實合成教材與作答的 0016 升級 0017（再接 0018），核對所有原資料表逐列相同；另由含筆記列的 0017 升級 0018，核對所有資料表除新欄位外逐列相同且新欄位為 NULL。`test_note_queue.py` 另驗證重點整理刪段成功與完整筆記失敗都把與日誌相同的摘要碼寫入 `generation_diagnostics`、不含教材文字或核對理由、API 不回傳且他人讀不到，重新生成沒有刪減時清為 NULL；中斷超過恢復次數的工作也以同格式寫入 DB 與日誌。

`test_notes_browser.py`／`e2e/api/notes.spec.ts` 使用隔離 DB 與受控模型走真 UI：儲存註記、重載、查來源、按需生成完整筆記及重生後註記保留。`e2e/mock/notes.spec.ts` 涵蓋失敗／先前段落、手機及鍵盤；共用教材分頁列修改須包含 global-layout／shell-layout 與完整 mock 套件。上述合成案例不代表模型內容品質；真模型驗收另以三份已授權教材，核對每段合法來源、完整涵蓋與 A4 正文，再對照原文審稿。

## 選擇範圍

`test_goal_metrics.py` 與 `test_goal_planning.py` 驗證可檢測 target 計量、真實完成、原排期落後量、固定今日配額、完整概念超量與唯讀預覽。`runtime/study/test_goals_stages.py` 驗證沒有 Study 也能讀取真實承接證據、概念完成不補造點數、目標權限、教材新版重排與提示關閉、同值儲存不重排，以及過期目標保留原日期調整比例。

目標的日基準測試涵蓋 NO_SAFE／部分發布／恢復可測與作答等實際寫入；日內配額不漂移，恢復可測後真正完成的點仍計今日成果。真 API 瀏覽器從設定目標、選程度到受控作答驗證目標達成不會把整份教材標完成；mock 另核對多教材首頁任務、原島嶼概念環、手機與更新提示。

`runtime/study/test_starting_level.py` 透過真 API 與隔離 DB 驗證必須由學生選程度、擁有者／Origin 邊界、切換不改現有題組或答案、開始請求重送、跨概念取材與作答、未測重點保留，以及後續只準備剩餘重點。`test_starting_levels.py` 驗證每概念一題、15 題上限、難點優先與路線排序；原交卷、補強、來源及進度測試保留。

Migration 驗證先在 0013 建立原題組、補強及答案，再升級至 0014，核對既有資料與 API 投影不變、單概念成員約束仍有效。這些驗證使用受控出題替身，不呼叫真實模型或寫入產品資料。前端涵蓋首次選擇、初學閱讀、直接作答、跨概念題組、深連結與原版本恢復，並依 TASK 核對 global-layout／shell-layout。

`runtime/study/test_learning_insights.py` 使用隔離資料庫驗證教材／題組的即時診斷、交卷時間邊界、補強完成規則、真實統計與來源、版本授權，以及個人難點的 3 題／50% 門檻。`test_learning_insights_rules.py` 驗證短標題與結構難度門檻、複習候選排序及最多 3 個的上限。

前端以 `learning-diagnosis.spec.ts` 驗證直接讀取、空區塊省略、精簡建議與結構標記；`concept-cards.spec.ts` 核對卡組原版本。相關版面檢查沿用 `global-layout` 與 `shell-layout`。這些功能不呼叫模型，也不以合成測試宣稱學習完成。

成就與經驗值：`test_learning_badge_rules.py`／`test_learning_xp_rules.py` 驗證各系列層級、特殊成就、克服難點與宿敵的重播門檻（重複題只採計第一次作答，不能刷）、關卡每日上限與一週闖關。`runtime/study/test_learning_badges.py` 使用隔離 DB：交卷時記錄分天關卡與準時達成目標（同日不重複、設定目標時已達標不算）、由既有診斷／筆記／註記／上傳／Podcast 收聽紀錄回溯推導、刪除教材後重新判定，0019 升級回填目標建立時間且不改其他欄位，以及關卡紀錄失敗只回滾自己的 savepoint、交卷仍成功；`test_export_routes.py` 核對只有匯出成功才寫入 `learner_exports`，`test_export_uploads.py` 與 `test_learning_badges.py` 核對紀錄寫入失敗不影響下載。前端以 `dashboard.spec.ts` 驗證經驗值來源列、成就格與隱藏彩蛋，版面沿用 `global-layout`／`shell-layout`。

| 變更 | 驗證入口 |
| --- | --- |
| Evidence、知識結構、題目與純邏輯 | backend/tests 對應單元測試 |
| OCR adapter 與 protocol | local_ai/tests |
| API、資料庫、worker、來源與交易 | backend/tests/runtime 對應領域 |
| 前端 API、路由與資料投影 | Node tests、TypeScript |
| 介面與互動 | production build、對應 Playwright spec |
| 文件與註解 | 連結、命令與語法／行為不變核對 |

測試通過後，只有新變更、失敗或未解疑慮才擴大或重跑。

## GitHub Actions CI

CI 使用 Python 3.12.14、Node 24 與 lock file；uv、npm、Docker build cache 只加速依賴取得，不略過測試。工作權限限於讀取 repository，不使用私人設定、secret、產品資料或外部生成模型；Compose／Nginx 邊界使用合成設定與隔離 echo upstream。quick runner 較舊的 kernel 不允許在 Docker 遮蔽的 `/proc` 上掛載新的 proc，正式主機的 6.18 kernel 則允許；因此八個真實 Office 轉檔案例與一個沙箱隔離案例由 full 主機執行，quick 容器不增加 AppArmor 或 systempaths 例外。full 主機維持 runner 的預設 user namespace 設定；兩份 Compose、seccomp、cap_drop、no-new-privileges、唯讀及無網路設定均不變。

| Job | 觸發 | 實際範圍 |
| --- | --- | --- |
| quick | 每次 push、pull_request，以及手動與排程執行 | 無網路 test 容器執行 backend 頂層、local_ai 與 ops/research 測試，明列排除的九項移至 full 主機，沒有略過覆蓋；另跑 npm ci／test／typecheck／build，以及 Compose、Nginx 邊界腳本 |
| full | 對 dev／main 的 pull_request、workflow_dispatch、每日台北時間 02:17（排程由預設分支執行） | 先跑 quick 移出的九項並檢查 JUnit 無 skipped，再跑 runtime 全部及其真 API browser fixtures；另包含四個 runtime Office 案例，主機使用 Ubuntu 26.04 與 Dockerfile 相同 snapshot／固定 LibreOffice 26.2.5.2 |
| mock-browser | 與 full 相同，needs quick 後與 full 並行 | 獨立 checkout／build／Chromium 執行全部 mock specs；外層整套程序預算 1800 秒、job 上限 40 分鐘，Playwright 每案例 30 秒、expect 10 秒、零 retry 均不變 |

本機可用 `docker compose -f compose.test.yaml build unit` 與 `docker compose -f compose.test.yaml run --rm unit` 執行完整單元套件；重現 CI 分工時使用 workflow 明列九個 node ID 的 quick／host 命令，前端執行下節列出的 Node／型別命令及 `npm --prefix frontend run build`。Compose 邊界須以 `STUDYDY_TEST_ENV_FILE` 指定合成設定，Nginx 邊界須提供 `STUDYDY_TEST_FRONTEND_IMAGE`／`STUDYDY_TEST_BACKEND_IMAGE`；不能沿用產品設定。Full 先依下節準備獨立前端 build、安裝 Chromium，再使用 workflow 的 runtime 命令；API specs 必須經 Python fixture 執行。Full 的 Ubuntu 26.04 主機依 workflow 安裝 `20261005T060000Z` snapshot 的 `4:26.2.5.2-0ubuntu0.26.04.1` LibreOffice 套件，不能使用 apt 的預設版本；主機不符時沿用下節 Office 容器入口驗證，不能將環境不符記為通過。

`test_office_external_resources.py` 以隔離 HTTP server 與固定的合成公網 DNS 驗證圖片取得、逐次 redirect／目的 IP 核對、TLS hostname、大小／時間上限及原檔保留，測試不連到真實內部服務。真實 Office 案例另核對內網圖片不送出請求、下載後的圖片仍可離線轉檔，以及只有圖片準備階段能連網。`test_normalization_cancellation.py` 驗證多執行緒取消、啟動失敗與限額；由既有 `prlimit` 在 exec 後設定 AS／FSIZE／NOFILE／NPROC，沒有 Python `preexec_fn`。

## 環境與執行入口

以下命令均從 checkout 根目錄執行。先備妥 `backend/.venv` 與 `frontend/node_modules`；新建的隔離測試環境以 `uv sync --project backend --locked --extra test` 安裝 Python 依賴，不重建其他 checkout 共用的環境。`test` extra 包含 CPU 音訊、字詞切分與影片繪製依賴，不安裝 GPU／模型環境。

| 範圍 | 必要環境 |
| --- | --- |
| 一般 Python 測試 | lock file 對應的 Python 3.12 與 test extra |
| `backend/tests/runtime` | Docker 與 PostgreSQL 18.6（與 Compose 相同固定映像）；fixture 新建一次性容器、每案例獨立 DB 與 artifact root |
| `frontend/e2e/api` | 前端 build、Playwright 瀏覽器、獨立 API／前端 ports，以及 runtime fixture |
| Office 真轉檔十二項 | **內含 LibreOffice 26.2.5.2 的容器**，以及契約對應的 Python 套件、字型、Bubblewrap、prlimit；執行方式見下節 |
| 音訊／影片測試 | test extra 及具執行權限、足夠空間的暫存目錄 |

所有 DB 測試禁止使用產品 DB。`STUDYDY_TEST_POSTGRES_DSN` 若有設定，只接受本機、名稱以 `studydy_test` 開頭的專用 control database，並核對 PostgreSQL 18 與 superuser 權限；未設定時 fixture 自建一次性容器。測試入口拒絕 `hostaddr`、未解析的 service、多主機地址及繼承的 PG 連線環境設定。

## 完整後端套件與瀏覽器

先建立獨立前端 build，再執行完整 `backend/tests`：

~~~bash
export STUDYDY_E2E_FRONTEND_DIST="$PWD/.studydy-runtime/test-frontend"
export STUDYDY_E2E_FRONTEND_PORT=4183 STUDYDY_E2E_API_PORT=8002
npm --prefix frontend run build -- --outDir "$STUDYDY_E2E_FRONTEND_DIST" --emptyOutDir

env -u STUDYDY_TEST_POSTGRES_DSN -u STUDYDY_DATABASE_DSN \
  -u PGHOST -u PGHOSTADDR -u PGPORT -u PGSERVICE -u PGSERVICEFILE \
  -u PGSYSCONFDIR -u PGDATABASE -u PGUSER -u PGPASSWORD -u PGPASSFILE \
  PYTHONPATH=backend/src:backend/tests:backend/tests/runtime/materials:local_ai/src \
  backend/.venv/bin/python -m pytest -q backend/tests --durations=10
~~~

這個命令會收集 Office 案例；主機 LibreOffice 版本不同時，這些案例失敗是預期的環境不符，不能放寬版本檢查或改成 skip。依下節容器指令完成十二項 Office 驗證，分別保留主機整套與 Office 容器的摘要；其他失敗仍須調查。主機命令有失敗時仍回傳非零，不應記成整套通過。

- `frontend/e2e/mock` 攔截 API，驗證公開契約與介面；`frontend/e2e/api` 由 Python fixture 啟動真 API、隔離 DB 與必要 worker，模型回應受控。
- **前端 UI 改版時，同步更新 `frontend/e2e/api` 對應 spec。** 導覽角色、可存取名稱或流程改變時，定位要跟著現行 UI 調整，原有內容、權限、持久化與呼叫次數斷言仍須保留。
- 同一 checkout 的 Playwright 案例串行執行；多個 runner 會共用並清理 `frontend/test-results`，可能造成 trace／network 檔案 `ENOENT`，不同 ports 不會隔離這個目錄。
- fixture 負責啟用旗標；不要手動設旗標或把 skip 當作通過。

headless 開新分頁若未進入 hidden 狀態，不能當成背景凍結／恢復證據。手機鎖屏與跨瀏覽器的背景恢復仍須實機驗證。

`runtime/study/test_progress_snapshot.py` 以 1／10／100 題與 1／10／100 個題組驗證讀取查詢數不逐筆成長，並保留來源設定、私有答案、事件順序及一致 snapshot 的檢查。`runtime/materials/test_podcasts.py` 以 1／10／100 筆節目驗證清單固定三次 SELECT，並核對摘要、順序與影片／分鏡狀態。這些隔離 DB 回歸不代表正式環境延遲或回傳資料量已有上限。

## Office 固定版本容器

從 checkout 根目錄以已安裝 test extra 的 `backend/.venv` 執行；本機須已有內含 LibreOffice 26.2.5.2 的 `studydy-backend-ocr:latest`（已驗證映像 ID `40172a3c484f`）與 `postgres:18.4-bookworm`，固定完整身分見 [office_container.py](../ops/tests/office_container.py)。
先用 `df -h /tmp /mnt/e` 檢查空間，E 槽預期超過 90% 時停止；主機版本不同造成 Office 失敗是預期的環境不符，應使用此容器驗證。
腳本不 build／pull、不碰產品 DB 或 volume，並在結束時刪除自己建立的容器與暫存依賴。

~~~bash
backend/.venv/bin/python -B ops/tests/office_container.py
~~~

後端映像的轉檔套件維持已驗證的舊快照，CA／libxml2／libcurl 則從獨立安全快照安裝明確版本，避免安全更新連帶更換 LibreOffice／Bubblewrap。兩個快照日期與套件版本見 `ops/docker/backend.Dockerfile`；調整後須重新驗證實際版本、轉檔輸出及來源 mapping。

後端使用 CPython 3.12.15。正式 lxml 6.1.3 wheel 仍內含舊 libxml2，因此暫時以 lock 中的 sdist／SHA-256 自行建置，動態連結 Ubuntu 的 `libxml2-16`／`libxslt1.1`（後者也提供 libexslt）。編譯用的 `libxml2-dev`／`libxslt1-dev` 與執行階段套件固定於同一安全快照版本；安全修補由 Ubuntu 套件的 backport 提供，之後更新安全快照與套件定版並重建映像即可套用，不能只用上游版號判斷是否已修補。

生成的 wheel 保留於 `/opt/studydy/lxml`，完整、test 與 app-only 建置都使用同一份。`backend-app.Dockerfile` 必須以包含此 wheel 與對應系統庫的新完整 runtime 為基底。驗證時核對 `lxml.etree.LIBXML_VERSION`、`LIBXSLT_VERSION`、`LIBXML_COMPILED_VERSION` 與 `ldd`，確認 extension 動態連結系統庫。上游正式 wheel 內含 libxml2 ≥2.15.4 時，刪除 `lxml-build` 階段、自建 wheel 複製，以及 dependencies、test、app-only 三處 `--no-install-package lxml`／wheel 安裝，恢復一般 locked sync。

CA 驗收依 [USN-8944-1](https://ubuntu.com/security/notices/USN-8944-1) 與 certifi 公告的官方修補範圍，確認 `emSign ECC Root CA - G3` 已移除；其他 G1／C1／C3 根仍可能保留，不額外變更上游信任政策。OCR 的 certifi 在 Dockerfile 明確固定為 2026.10.19。cloudflared 2026.10.3 使用 Go 1.27.3 的受損憑證拒絕修補，不能以此宣稱其系統信任庫已移除全部 emSign 根。

## 其他測試入口

前端 Node／TypeScript 與 OCR adapter／protocol：

~~~bash
npm --prefix frontend test
npm --prefix frontend run typecheck
PYTHONPATH=local_ai/src backend/.venv/bin/python -m pytest -q local_ai/tests
~~~

mock browser 沿用前述 build 與 ports；可指定單一 spec 或 regex：

~~~bash
PYTHONPATH=backend/tests/runtime backend/.venv/bin/python - <<'PYTEST'
from browser_e2e_runner import main
raise SystemExit(main("e2e/mock/", timeout_seconds=1800))
PYTEST
~~~

真 API 案例應執行其 backend/tests/runtime fixture，例如 `sources/test_source_revisions_browser.py`；不要直接跳過 fixture 執行 API spec。

## 必須保護的行為

- owner／session 隔離、私密答案及來源綁定。
- 整組交卷的原子性、冪等、版本衝突與回應遺失恢復。
- 刪除、quarantine、checkpoint 清理與晚到 worker 的隔離。
- migration checksum、序列、併發安裝、升級與失敗回滾。
- 真轉檔、頁碼／區塊定位，以及瀏覽器恢復。

記錄實際命令、範圍、結果與限制。合成測試提供程式回歸證據；模型品質須另以指定素材及標準，透過產品流程生成並人工核對。

`runtime/infrastructure/test_migration_baseline.py` 驗證新初始化標記、拒絕初始化非空資料庫，以及初始化中斷後續跑。`test_migrations.py` 驗證現行 schema、帳本 checksum／序列、並行安裝及失敗回滾；全部使用隔離 PostgreSQL。

`runtime/infrastructure/test_api_lifespan.py` 在各 worker 啟動位置注入失敗，核對已啟動工作的清理；單一 stop 失敗時仍處理其他 worker。正常啟停順序維持原契約，測試不啟動模型或連線產品 DB。

`runtime/materials/test_generation_fencing.py` 使用隔離 DB，驗證研究、主題及分鏡在 token 未變但租約已到期時，不得保存中間結果、成功或失敗，也不得建立來源 Artifact 或交接新的 Revision 工作。有效租約與被重新領取的租約分別作為正反對照；各功能仍在自己的交易核對狀態與父資料，續租 watcher 不能代替保存前的檢查。

Podcast 的可重跑入口為 `backend/tests/runtime/materials/test_podcasts.py`（隔離 DB／API／音訊保存與取消）及 `backend/tests/test_podcast_provider.py`（無模型的生成、逐段核對與單段重生、格式與合法來源綁定）。前端 client 測試包含錯來源腳本與不完整音訊 manifest 的拒絕。provider 的容器測試使用 `compose.test.yaml`，不需安裝 TTS 或呼叫模型。

語音問答與研究：`backend/tests/runtime/materials/test_voice.py`、`test_research.py` 驗證真 DB、權限、重送、取消晚到結果、來源追加與版本衝突；`backend/tests/test_research_sources.py` 驗證下載目標與授權邊界；`test_luna_test_provider.py` 確認替代身分及不呼叫 Gemma。介面入口為 `frontend/e2e/mock/material-tools.spec.ts`，涵蓋文字問答、回查來源、錄音權限晚到取消，以及超過三份來源的選取與不可逆確認。這些合成測試不代表實體麥克風、跨瀏覽器或教材回答品質已驗收。

主題建立：`backend/tests/runtime/materials/test_topics.py` 驗證確認前無研究工作、選取前無空教材、批准重送、過期版本、跨帳號、取消晚到及刪除後不復活，並使用隔離 DB 沿原 publisher 建立地圖。`frontend/e2e/mock/topics.spec.ts` 驗證可修改範圍、選取後才建立、重開與零來源處理；這些替身測試與公開站真實 Luna／來源流程的證據分開記錄。

場景對齊：`backend/tests/test_scene_alignment.py` 驗證測得時間點、同音字不改稿、不可杜撰時間及流程條件；`backend/tests/runtime/materials/test_podcast_scenes.py` 驗證真 DB 的依賴身分、準備重送、取消晚到、刪除與音訊不變。`frontend/e2e/mock/podcasts.spec.ts` 的同步案例使用測試 WAV 的實際 media clock，檢查暫停、seek、2× 倍速及版面；媒體時鐘與畫格同步量測只驗證播放器呈現，不能代替 ASR 精度、實際對齊或真人聽評。

Podcast 影片另由 `backend/tests/test_podcast_video_plan.py`、`test_podcast_video_layout.py`（anchor 換算與退回、段內拆頁與退回、頁數上限、重點詞驗證與丟棄、字幕帶上緣）、`test_podcast_video_motion.py`（聚焦時間窗、章節列、重點詞像素、往返 seek）、`test_podcast_video_render.py`、`test_podcast_video_service.py` 及 `backend/tests/runtime/materials/test_podcast_videos.py`（v2／v3／v4 結果保存與讀取）覆蓋。回歸包含單人與雙人節目的音訊完成自動排隊；長對談逐字還原、角色與 Evidence 關係；短問答合併 cue；來源核對失敗後重做仍須再驗證。`frontend/e2e/mock/podcasts.spec.ts` 另驗證雙人共用 cue 的角色標籤與跳轉。這些合成測試不代表真實音質或分鏡品質。

## Podcast 講稿、時間軸與互動

- `test_podcast_provider.py` 核對節目講稿 prompt 帶入主持人名字、系列位置與下集預告，主持人名字或系列資訊不合格時不呼叫模型；`runtime/materials/test_podcasts.py` 核對建立時抽定的主持人在同系列各集相同、API 回傳 `hosts`、worker 領取時附系列位置，以及舊的未寫稿集數補名字而不改已完成集數。
- `test_podcast_beats.py` 核對多來源 beat、重複 claim ID 的來源位置、逐 part 引用與 claim 跨 beat；`test_podcast_cues.py` 核對字幕錨點與教學段落的對應。
- `test_podcast_audio.py` 核對首尾 trim、內部停頓與保護區、語境停頓、非有限值及無聲拒絕，以及真 FFmpeg 音量正規化、AAC 解碼、1080p60 編碼與 bundle 驗證。
- `test_podcast_timeline.py` 核對原文字元範圍、VTT escaping、舊影片與 refined scene 的唯讀 locator 投影；stored manifest、script hash 與 VTT 不變。
- `runtime/materials/test_podcast_interaction.py` 核對教材從 A revision 更新至 B 後，既有 Podcast 的 Voice、來源與 Assessment 仍使用 A；涵蓋偽造 locator、錄音 context、冪等、取消／刪除晚到結果，以及播放／提問不產生作答事件。另經正式 API 錯誤邊界核對：音訊完成、對齊尚未寫入時，時間軸與字幕回可重試的 409 `PODCAST_TIMELINE_NOT_READY`（逐字稿 Markdown 匯出為 `SCENE_CONFLICT`），對齊失敗、取消或沒有對齊工作（舊版講稿）回不可重試的 `PODCAST_TIMELINE_UNAVAILABLE`，對齊與音訊不一致回不可重試的 `PODCAST_TIMELINE_SOURCE_MISMATCH`；`e2e/mock/podcasts.spec.ts` 驗證前端只對 `PODCAST_TIMELINE_NOT_READY` 輪詢，其餘直接顯示錯誤。
- `runtime/materials/test_podcast_interaction_browser.py` 與 `e2e/api/podcast-interaction.spec.ts` 使用真 API／DB，驗證來源開啟、Voice 問答與語音、VTT、末集入口及原 revision Assessment handoff。字幕可以比教學單位細，Voice 仍收到完整原段落。
- `e2e/mock/podcasts.spec.ts` 與 `material-tools.spec.ts` 覆蓋 seek、倍速、音訊切換影片、字幕開關、無 alignment 時手動選段、固定提問 context、已完成 session 不重開、過期 cue 拒絕及手機版面。

分集須保留 claims 的順序、身分與引用，在容量允許時保持概念完整；planner 與 provider 共用現行容量契約。比較講稿或語音效果應固定來源與 revision，不以篇幅、時長或較少分集代替深入程度與教學成效。

Voice 回答與 Podcast 互斥播放；STT 與虛擬麥克風測試不等於實體麥克風或辨識品質驗收。

## Podcast 動畫與影片快取

`test_podcast_video_motion.py`、`test_podcast_video_layout.py`、`test_podcast_video_render.py` 與 `test_podcast_video_service.py` 覆蓋進場、焦點、關係、flow／exchange、換頁、reveal／emphasis、字幕錨點、往返 seek、媒體時鐘、既存格式與重複渲染；geometry、來源與時間驗證仍須通過。字形像素回歸核對透明度合成，快取回歸核對像素與進度更新，不以快取改變畫面內容。

`runtime/materials/test_podcast_videos.py` 核對 ready 重開不重製、ownership／range、取消／重試／晚到結果，以及無效 motion anchor 不得寫入 artifact。影片維持 1920×1080／60fps；效能比較應固定分鏡、音訊、編碼參數與工作負載，核對完整解碼、音訊及換頁畫格，不以不同生成內容的總耗時宣稱加速。

動畫依保存資料與播放時間計算，不累積影格狀態；反向 seek 與重訪相同時間須得到一致結果。工程檢查不代替真人觀看、音質或教學成效驗收。

效能報告須分清繪圖、編碼與模型／TTS／ASR 的量測範圍，單一主機與素材的量測不保證其他素材同樣受益。慢速播放會放大低幀率的時間取樣差異，不能只因 CPU 收益就降低呈現規格。

粗錨點可能同時呈現多項資訊，沒有新語意錨點時不憑空補動畫；crossfade 仍可能有短暫疊影。少量工程與播放抽查不能外推為多教材、長課程、行動裝置或所有瀏覽器均已全面驗收。

## 學習互動與生成租約

隔離 DB 與受控 provider 驗證首答完成、錯後兩道不同題連對、再錯重計、再次練習保留完成、部分／零可檢測，以及未變重點的跨版本承接。前端回歸涵蓋測驗清單與直接接題、固定比例概念卡、Voice Mode 持續聆聽／靜音送出／打斷播放、權限失敗清理、歷史對話與來源編號。模擬音量只驗證互動，不代表真實麥克風或回音環境通過。

`runtime/materials/test_generation_leases.py` 使用隔離 DB 驗證問答、主題與研究跨過原租約期限後仍不被重新領取，取消仍傳至 provider。轉檔取消與模型子程序回收由前述 `test_normalization_cancellation.py`、`test_podcast_process.py` 覆蓋，不呼叫真實模型。

模型、語音、辨識及資源排隊的取消須傳到實際子程序，無生成期限不代表取消失效。健康檢查不得排在生成工作後面。

## 搜尋、網頁正文與研究閱讀

`test_research_web.py` 與 `test_research_sources.py` 驗證免費搜尋接線、中英文結果交錯、網址去重、查詢／正文快取、同站節流、Retry-After、拒絕存取後冷卻、頁面授權範圍、正文的表格／程式碼字面值，以及取得時重新核對頁面身分。`test_luna_test_provider.py` 以替身驗證一個生成請求回傳多行關鍵詞，不呼叫真實模型。Runtime 的 `materials/test_research.py`／`test_topics.py` 使用隔離 DB，網頁正文沿既有 normalization／來源流程；不把搜尋摘要當 Evidence。

`test_research_reading.py`、`test_research_preview.py` 與 `test_research_workflow.py` 核對逐步保存、正文引用、依缺口補查、來源去重、403 替代入口、限流等待、摘要分流、選讀引用與取消後拒絕晚到結果。`research-background.spec.ts` 與來源分頁案例涵蓋階段進度、取消接續、參考來源與桌機／手機版面。

定位結果須逐段列出 read／skip／uncertain 與理由，所有編號恰好一次；重複、漏列或越界不得靜默去重。略過區段的線索與理由須保存並供缺口檢查使用，避免把參考文獻誤判成漏讀正文。

來源核對涵蓋無 `.pdf` 副檔名的下載入口、登入／下載標題頁與真正正文的區別，以及跨相鄰文字區塊的完整題名比對，不以相似度放寬；取得 PDF 不等於取得匯入授權；只取得登入、下載入口或題名時，應回報正文未取得，不把它判成內容偏題。同一論文且內容 hash 相同時可重用已完成的全文審閱，部分選讀不得代替全文，不同內容版本仍須重新閱讀；重用審閱不複製其他入口的匯入資格。

少量公開查詢或替代入口成功，不保證其他主題的來源可用性、長期穩定性或完整即時回答。局部閱讀流程的量測未包含搜尋、下載或補查時，不得當作端到端效能結論。

僅列為參考的來源不能代替經正文核對、符合匯入條件的教材內容。資訊不足時可擴充導讀，具體缺口可促使背景來源升級閱讀或補讀既有來源，但不得僅因只讀過部分區段就重讀全文。

## 八路獨立子圖

`test_independent_blocks.py` 驗證八路併行、取消、亂序完成後只補缺口、checkpoint 後已完成回應重播、舊策略升級及來源／索引邊界；`test_material_provider_parallel.py` 使用真 HTTP 驗證第九個教材請求會等待槽位，不呼叫模型。`runtime/sources/test_independent_block_archive.py` 使用隔離 DB 驗證舊進度升級、artifact 已保存但 checkpoint 寫入失敗時不重做 inference，以及取消後拒絕晚到寫入。舊策略 fixture 僅包含公開 runtime 契約，沒有私人教材或模型回應。

## 選段分析、RAG 與問答

`test_analysis_scope.py`、`test_material_retrieval.py`、`test_material_qa.py` 檢查原文範圍、去重、輸入預算、補查及逐段核對。`runtime/sources/test_research_selected_scope.py` 使用合成十頁 PDF，驗證只分析第七頁並回查原始第七頁，以及索引儲存失敗不發布新 head。`runtime/materials/test_retrieval.py` 保護 owner／revision 邊界與相鄰來源範圍。這些測試不代表真實 Luna 品質。

研究重用 staged 原檔時，bytes 與分析範圍必須同時相同。`test_research_selected_scope.py` 以兩個有效研究租約驗證同範圍重播可重用；引文、區域或全文／局部模式不同時回報既有 `DUPLICATE_SOURCE`，不修改來源範圍或新增 Artifact／正規化工作。

語音與文字由 `runtime/materials/test_voice.py` 驗證，回答使用受控 `/semantics` 與 `material-answer/v2`。研究／主題回歸核對排定工作完整執行、選段匯入、相同原文重用與補讀不重複範圍。瀏覽器回歸使用 `voice-chat.spec.ts`、`processing.spec.ts`、`topics.spec.ts`、`research-background.spec.ts`、`source-pagination.spec.ts`；包含索引的準備流程共六個階段。

長對話回歸驗證按 token 超額壓縮、超過三輪的短對話完整保留、摘要沿用、不重壓舊輪次與無效摘要不得靜默丟棄歷史。Runtime 另驗證摘要與回答一起保存、取消隔離、重試舊問題不讀未來摘要、原始輪次保留及刪除時清除摘要。這些合成測試不等於真實摘要內容品質已通過。

研究涵蓋核對以單一子題的已選原文為單位，變動時重算該題並保留其他題快取；請求容量不足時接續分批，不以新來源摘要覆蓋舊機制。`test_research_workflow.py` 驗證跨來源合併、變動子題隔離、來源撤回與更正、分批範圍及無原文不得宣稱涵蓋。

補入更多檢索內容未必提升品質，還須核對原有例子與細節是否被擠掉。局部改善不能外推為所有教材都會受益。

## 概念卡整理、版面設計與持久進度

`test_card_design.py` 核對來源編號、整卡合併與詳情分流、容量及有向連線限制，以及 Luna 暫用與 Gemma／既有 Pod endpoint 的路由隔離；Gemma 的這項檢查使用 HTTP 替身，不宣稱遠端模型品質通過。Runtime 的 `test_card_generation.py` 使用隔離 DB 驗證分步保存、同版本與模型快取、定向修正、失敗重試、重新整理的版本衝突、租約回收與刪除後不得發布。瀏覽器檢查各設計版型的實際文字邊界、引用入口、原文保留、Canvas 顯示與 PNG 下載；真實 Luna 的整理品質另以授權教材生成後人工檢查，不把 JSON 合法或模型核對結果單獨當成品質通過。

大型教材讀取的回歸包含 JSONB 的 NUL 無損還原、來源驗證邊界，以及同一工作階段內目前地圖與舊版卡組的兩份不可變結構快取。登出、帳號失效與刪除教材會清除快取，失敗請求可重試；快取不寫入瀏覽器持久儲存。以實際網路回應與功能可用狀態量測，不能把固定等待時間當成完成時間。

概念卡分批讀取：`test_card_reading.py` 驗證首批／尾批、跨批相鄰概念與 Evidence 的完整投影、權限、版本衝突、投影損壞及部分續頁計數。瀏覽器回歸驗證首屏不要求完整 KS、直接跳讀遠端觀念、跨批續頁返回及帳號切換時取消待回應批次。首次讀取效能以實際 request 數、大小及首張 Canvas 時間量測。

## 按需教材讀取

`runtime/study/test_knowledge_reads.py` 驗證 outline 不含完整 claims、概念／來源投影與 canonical 內容一致、搜尋只回傳 ID、workspace 只帶當前／下一個概念、跨 owner 與損壞索引拒絕、補建冪等。測試在頁面讀取時禁止呼叫完整教材驗證，避免僅縮小 HTTP 回應卻仍讀全文。

進度、跨版本繼承、題組與來源回歸保持原完成／補強規則。`client.test.mjs` 驗證按需資料的 material／revision／selection 綁定及帳號失效；地圖詳情、學習入口、study-layout、卡組及 Podcast 選材 browser 案例覆蓋延遲讀取、來源、搜尋與歷史切換。Runtime browser 仍用獨立 ports、disposable DB 與受控 provider，不以產品 DB 跑測試。

## 跨語言學習內容

`test_spoken_analysis.py` 驗證同區塊的獨立主張切分、句／子句邊界、至少 4 個非空白／非標點字元（含整段）、單字拒收、否定不可被從句中裁掉、無標點整段回退、唯一原文節錄、失敗引用的單次重選與呼叫記帳、技術值切斷／錯字引用拒絕、翻譯／審查／概念整理保留精確引用，以及 `uncertain` 保留原文並排除去重改寫；疑義改述僅可撤回原節錄，涵蓋 PDF 詞內換行與外文保留代號的核對。同一概念內的逐字稿、PDF 與混合引用 Claim 分別核對，混合引用中的 PDF 短標題仍可發布，語言例外與去重只保護逐字稿 Claim。新契約與舊 checkpoint 隔離，舊投影不放寬。`runtime/sources/test_media_source_flow.py` 以受控模型驗證 mapping 的字幕／ASR 身分經來源集合傳到分析，來源頁碼與時間回查不變；`runtime/study/test_knowledge_reads.py` 保護完整關係來源。這些不代表真實語音辨識品質；Luna 的授權樣本比較只報實際呼叫及觀察結論。

`test_cross_language_claims.py`、`test_material_localization.py`、`test_review_language.py` 覆蓋原文引用不變、數值／單位／程式保護、跨段公式邊界、代號遺漏與重複、混入外語詞及修正後保留的完整來源。子圖語言補正失敗會阻止該批完成；Review 的局部語言修正無法核對時保留原文並標記 `needs_review`，其他合法修改仍可套用，保留代號遺漏或重複的提案則仍拒絕。獨立子圖與審查回歸另核對呼叫計數、已完成結果重用及舊工作提示不可被新政策覆蓋。

語言與功能驗證不等於內容全面認可，原教材內容不足的 `SOURCE_REVIEW_SUGGESTED` 必須保留。有限語言樣本的檢查不能外推為所有語言、否定條件或專業術語均已驗收。

## 問答與補充研究的機制效率

問答在沒有對話／Podcast 指涉時先檢索原問題；空結果再規劃查詢，仍保留回答、逐段引用核對與補查。`test_material_qa.py` 驗證直接路徑、空結果補救、對話指涉、否定與技術字面值、無依據拒答及上下文容量。

補充研究先從使用者要求閱讀的網址取得原頁，沿用來源身分、授權與安全連線檢查；不把網址改成搜尋詞重找同一篇。來源數量／授權等管理要求不當作每篇正文必須回答的知識子題。不提供手動提早交付；無可行新方向或持續無新增證據時，系統整理現有結果並保留缺口。workflow／來源單元測試、隔離研究 runtime 及 research-background 瀏覽器案例覆蓋網址排除、無授權不得匯入、舊提早交付操作拒絕及晚到 worker 拒絕。

問答核對必須回到原文，不能以 Claim 改述充當原文依據；無法支持的斷言不得發布。

## 持續語音的人聲判定

`frontend/e2e/mock/voice-chat.spec.ts` 的介面案例以語音事件替身驗證開關、插話、取消與載入晚到；其中 `real local VAD` 案例使用真正 Silero v6／ONNX WASM 與 WebAudio 串流，檢查持續噪音／嗡聲／脈衝不送出、RMS 0.008 的合成人聲與混噪人聲各送出一次，以及 800ms 句中停頓不拆段。錄音保留 800ms 前置與 1400ms 收尾，輸出 mono 16kHz WAV。此為合成音源回歸，不能宣稱實體麥克風或所有背景聲均已驗收；附近其他人的說話仍可能被判為人聲。

模型與 WASM 由 Vite 從鎖定 npm 依賴發佈至 `/voice-assets/`，開啟問答面板時平行預載公共模型與運算資產，開啟語音模式後才取得麥克風並初始化辨識器；版本化資產採一年 immutable 快取，授權文字不套用此快取。Nginx 必須保留該路徑的 `.mjs`／`.wasm` MIME 設定。測試音源來源見 `frontend/e2e/fixtures/voice/README.md`。

## 需求確認與論文語意搜尋

補充學習與從主題建立教材共用需求確認；確認前不得建立研究或搜尋，確認後不得由搜尋規劃擴張 topics。回歸涵蓋教材版本衝突、跨帳號拒絕、取消與晚到 worker、舊研究重新規劃、原檔快照重啟後重用、跨研究與 hash 損壞拒絕，以及原文頁碼／區塊綁定。

首次閱讀時保存已核對原檔；隔離 DB 測試須在重啟後禁止網路下載，核對仍能匯入相同 bytes，不以重下載取代快照或放寬 SHA-256。首次閱讀可採索引列明的其他版本，已讀 bytes 不得被換版本；PDF、HTML 正文與結構化摘要的來源身分須區分，文字快照重啟後仍可沿用並完成實際轉檔與引用定位。

已涵蓋的子題仍可查證具體方法或反例，原有支持來源不因此撤除；續接舊政策時重核原文涵蓋，不重抓或丟棄已讀來源。單一地區、個案或預印本的支持範圍須如實保留，部分支持不等於來源無用。`requested_scope` 只建立選取要求；必須另核對 `locate_scope` 的原文頁碼／區塊定位。

單次模型涵蓋判定不能視為穩定滿分；搜尋與模型輸出會變動，不能僅以涵蓋結果差異推定單一改動的效果。原檔保存與引用定位通過不等於搜尋或學術內容品質通過，仍須區分直接觀察、作者解釋及研究適用範圍。

`ops/research/qualify_scope.py` 使用已確認範圍驗證真實流程，部分涵蓋以非零結束碼回報。原始結果、失敗與中斷嘗試、來源核對及必要原檔只留本機私人紀錄，不加入 Git／CI。

## 專用 UID 的隔離驗證

以獨立 Compose project 與暫存資料驗證 `STUDYDY_BACKEND_UID=20001`：新 artifact 由後端擁有且維持私有權限，init 不重設既有資料根／PostgreSQL／模型目錄。假模型只授目錄穿越與檔案讀取 ACL，重跑 init 後仍須有效；不讀真模型或真金鑰。bridge 的假金鑰依其保留的1000身分驗證。

jerry 直接存取私有產物應被拒絕，改以 API／服務 UID 的受控命令驗證讀取，不放寬0700／0400斷言。沿用原NPROC1024、容器pids512及所有隔離參數；最小namespace探針不增加UID1000負載，也不調高限制。現有單元與runtime套件仍按原範圍執行。

完整重建後端映像後，以 `python3 -B ops/tests/backend_identity.py <映像>` 核對執行 UID 有 passwd 身分、HOME 為 /tmp，且比照 OCR 子程序的清空環境仍可匯入 transformers、torch inductor cache 與 matplotlib 設定落在 /tmp。腳本以唯讀 rootfs、無網路執行，不 build／pull、不掛產品資料、不載入模型；UID 依 `STUDYDY_BACKEND_UID`（預設20001）。


## 學習資料匯出

`test_exports.py` 檢查筆記三種格式的表格、公式、來源、註記快照與中文字型粗體，原本一頁的長英文摘要在中文標題換粗體後仍為 A4 一頁且正文位置不變，診斷摘要的頁數與省略數量、MP3 實際解碼、逐字稿名字／時間，以及 Anki 資料庫與全部 PNG；也核對影音來源的「段」標示、DOCX 控制字元／作者及空診斷文案。`runtime/study/test_export_routes.py` 在一次性 DB 驗證 owner、固定版本、Origin、私人快取、未發布筆記、缺少影片／時間軸與偽造地圖選取。

受影響的 notes、concept-cards、knowledge-map-viewport、learning-diagnosis、podcasts mock specs 驗證下載格式、整組分批、地圖渲染、失敗訊息與原介面；notes 真 API browser 同時驗證筆記三種下載。共用 client 的二進位下載沿用帳號失效／請求取消，因此仍需完整 mock 套件與 global-layout／shell-layout。這些驗證不宣稱重新驗收生成內容；匯出本身不呼叫模型。

`test_export_uploads.py` 以 148 個合成概念、1200×760 PNG 實際分批送 HTTP，累計 payload 超過舊 90 MiB 上限，核對單批大小、整組 ZIP、下載後清理、逾時清理與鎖定中的工作保留。另以 44 個並行檔案回應、僅 4 個工作執行緒驗證非同步排隊不會死鎖，同時最多佔兩個產檔槽位；Anki 測試禁止在工作目錄外建立 SQLite 暫存。concept-cards browser 案例用實際 Canvas 繪製 148 個概念，核對每批最多兩張與完整下載。


卡組暫存上限測試涵蓋並行建立時每人最多兩份、另一位 owner 不受影響、取消後釋放名額，以及總位元組／每概念頁數超限整批不寫入。真 API 與 client 測試核對三種明確錯誤。慢速卡組下載測試確認產檔名額已釋放，暫存仍存在且持鎖，其他匯出可完成，對同一下載的重複下載／取消會立即回忙碌且不占名額；MP4 在兩個產檔名額都被占用時仍可直接下載。
