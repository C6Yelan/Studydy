# 測試

[文件入口](../README.md) · [安裝與啟動](getting-started.md) · [限制](limitations.md)

## 選擇範圍

| 變更 | 驗證入口 |
| --- | --- |
| Evidence、知識結構、題目與純邏輯 | backend/tests 對應單元測試 |
| OCR adapter 與 protocol | local_ai/tests |
| API、資料庫、worker、來源與交易 | backend/tests/runtime 對應領域 |
| 前端 API、路由與資料投影 | Node tests、TypeScript |
| 介面與互動 | production build、對應 Playwright spec |
| 文件與註解 | 連結、命令與語法／行為不變核對 |

測試通過後，只有新變更、失敗或未解疑慮才擴大或重跑。

## 無網路容器測試

~~~bash
docker compose -p studydy-unit-tests -f compose.test.yaml run --build --rm unit
~~~

容器不掛載產品資料，不需要 GPU 或模型。前端映像建置包含 Node tests、TypeScript 與 production build。

## 原始碼測試

需先準備 backend/.venv、frontend/node_modules；真轉檔還需要部署相同的 LibreOffice、字型及 Bubblewrap。依賴版本以 lock files 為準，不重建其他 checkout 共用的環境。

~~~bash
PYTHONPATH=backend/src:backend/tests:local_ai/src \
  backend/.venv/bin/pytest -q backend/tests/test_*.py
npm --prefix frontend test
npm --prefix frontend run typecheck
~~~

Node runner 的摘要可能按測試檔計數；需要個別案例時可直接執行對應 test.mjs。

OCR adapter／protocol 的原始碼測試：

~~~bash
PYTHONPATH=local_ai/src backend/.venv/bin/pytest -q local_ai/tests
~~~

## Runtime 與瀏覽器

Runtime 按 accounts、assessments、infrastructure、materials、sources、study 分類。fixture 預設建立 disposable PostgreSQL 18，每個案例使用獨立 DB 與 artifact root；禁止接產品 DB。

如需指定資料庫，STUDYDY_TEST_POSTGRES_DSN 只接受本機、名稱以 studydy_test 開頭的專用 control database，並核對版本及 superuser 權限。

完整 runtime 測試需要獨立前端 build 與 ports：

~~~bash
export STUDYDY_E2E_FRONTEND_DIST="$PWD/.studydy-runtime/test-frontend"
export STUDYDY_E2E_FRONTEND_PORT=4183 STUDYDY_E2E_API_PORT=8002
npm --prefix frontend run build -- --outDir "$STUDYDY_E2E_FRONTEND_DIST" --emptyOutDir

env -u STUDYDY_TEST_POSTGRES_DSN -u STUDYDY_DATABASE_DSN \
  PYTHONPATH=backend/src:backend/tests:local_ai/src \
  backend/.venv/bin/pytest -q backend/tests --durations=10
~~~

- frontend/e2e/mock 攔截 API，驗證公開契約、互動及代表性桌機／手機版面。
- frontend/e2e/api 由 Python fixture 啟動真 API、隔離 DB 及必要 worker；模型回應受控。故障注入案例會明確攔截個別請求。
- fixture 負責設定啟用旗標；不要手動設旗標或把 skip 當通過。

執行 mock browser，沿用上述 build 與 ports：

~~~bash
PYTHONPATH=backend/tests/runtime backend/.venv/bin/python - <<'PYTEST'
from browser_e2e_runner import main
raise SystemExit(main("e2e/mock/", timeout_seconds=600))
PYTEST
~~~

可將 spec 改為檔名或 regex，限制驗證範圍。真 API 案例應執行其 backend/tests/runtime fixture，例如 sources/test_source_revisions_browser.py。

## 必須保護的行為

- owner／session 隔離、私密答案及來源綁定。
- 整組交卷的原子性、冪等、版本衝突與回應遺失恢復。
- 刪除、quarantine、checkpoint 清理與晚到 worker 的隔離。
- migration checksum、序列、併發安裝、升級與失敗回滾。
- 真轉檔、頁碼／區塊定位，以及瀏覽器恢復。

記錄實際命令、範圍、結果與限制。合成測試提供程式回歸證據；模型品質須另以指定素材及標準，透過產品流程生成並人工核對。

Podcast 的可重跑入口為 `backend/tests/runtime/materials/test_podcasts.py`（隔離 DB／API／音訊保存與取消）及 `backend/tests/test_podcast_provider.py`（無模型的來源核對失敗與有限修正）。前端 client 測試包含錯來源腳本與不完整音訊 manifest 的拒絕。provider 的容器測試使用 `compose.test.yaml`，不需安裝 TTS 或呼叫模型。

本輪另以 Playwright MCP 直接操作隔離真 API：先使用受控音訊驗證取消／刪除／帳號隔離，再以合成素材呼叫真實 Luna＋Kokoro，驗證 13 個概念全選成三集、完整模式單概念、播放／seek／重開及連續播放。其後另於公開站完成已授權的真實教材驗收：31 個概念／189 個重點生成 32 集，全部 WAV 端點與來源綁定核對通過；末集續播、連續播放、取消／重試、刪除及登入隔離由 MCP 驗證。這不代表原教材 `needs_review` 已轉為合格，也不代表人工音質驗收已完成。

F1 語音問答／F2 研究：`backend/tests/runtime/materials/test_voice.py`、`test_research.py` 驗證真 DB、權限、重送、取消晚到結果、來源追加與版本衝突；`backend/tests/test_research_sources.py` 驗證下載目標與授權邊界；`test_luna_test_provider.py` 確認替代身分及不呼叫 Gemma。介面入口為 `frontend/e2e/mock/material-tools.spec.ts`，涵蓋文字問答、回查來源、錄音權限晚到取消，以及超過三份來源的選取與不可逆確認。這些合成測試不代表實體麥克風、跨瀏覽器或教材回答品質已驗收。

F3：`backend/tests/runtime/materials/test_topics.py` 驗證確認前無研究工作、選取前無空教材、批准重送、過期版本、跨帳號、取消晚到及刪除後不復活，並使用隔離 DB 沿原 publisher 建立地圖。`frontend/e2e/mock/topics.spec.ts` 驗證可修改範圍、選取後才建立、重開與零來源處理；這些替身測試與公開站真實 Luna／來源流程的證據分開記錄。

F5：`backend/tests/test_scene_alignment.py` 驗證測得時間點、同音字不改稿、不可杜撰時間及流程條件；`backend/tests/runtime/materials/test_podcast_scenes.py` 驗證真 DB 的依賴身分、準備重送、取消晚到、刪除與音訊不變。`frontend/e2e/mock/podcasts.spec.ts` 的同步案例使用測試 WAV 的實際 media clock，檢查暫停、seek、2× 倍速及版面；不能用此替代真實 Podcast 對齊驗收。實際模型與公開站成果另留在本機 `data/scenes/acceptance/`。

Podcast 影片另由 `backend/tests/test_podcast_video_plan.py`、`test_podcast_video_render.py`、`test_podcast_video_service.py` 及 `backend/tests/runtime/materials/test_podcast_videos.py` 覆蓋。模式回歸包含完整單人、快速雙人、完整雙人的音訊完成自動排隊；長對談逐字還原、角色與 Evidence 關係；短問答合併 cue；來源核對失敗後重做仍須再驗證。`frontend/e2e/mock/podcasts.spec.ts` 另驗證雙人共用 cue 的角色標籤與跳轉。這些合成測試不代表真實音質或分鏡品質；真實模式驗證的私人證據保存在本機 `data/podcast/mode-qualification-20261005/`，不加入 Git／CI。

2026-10-05 以相同四個來源重點，經正式 API 新建完整單人（73.38 秒／6 輪）、快速雙人（107.8 秒／11 輪）、完整雙人（125.34 秒／12 輪），三者皆由音訊完成自動排入影片。兩組雙人曾因版面或來源核對失敗停止；補上所有超框的具體回饋，並分開計算版面與來源核對的修正機會後，正式重試均通過。最後核對原稿與音訊 hash 未變，影片時間軸逐字還原角色與來源，並完成公開站字幕、seek、2×、手機版及整支 H.264 1080p60 解碼；結果各為 13／14／13 個 cue、3／4／3 頁。這不等於多教材或更長完整課程均已驗收，也不取代人耳音質審查。


### 2026-10-04 隔夜公開站驗證

本輪功能分支 `feature/voice-research-overnight-20261004` 已部署，未 push／merge。文字均為真實 `codex exec -m gpt-5.6-luna`，辨識／對齊為本機 Whisper，TTS 共用 CosyVoice；未呼叫 Gemma。

- F1：真實教材文字問答、引用、保存重開、追問及語音播放；合成音源經瀏覽器 MediaRecorder 產生 WebM，再進真 STT、修改逐字稿送出。登出音訊停止、未登入讀取 401。不是物理麥克風測試。
- F2：原教材副本追加 MDN TCP slow start 與 CC BY 論文，真下載／授權快照／轉檔／Luna 分析／發布完成，共 48 頁與 102 概念，兩個來源分別回查第 1／2 頁。原教材及來源清單 API 前後 SHA-256 相同。
- F3：Luna 提案後在 UI 修改程度／目標、批准搜尋並選 MDN TCP handshake，產生 4 頁／6 概念的新教材；地圖與第 1 頁來源已開啟。批准前無搜尋，選取前無空教材。
- F5：兩份真實 Podcast、7 集、40 段完成 `whisper-phonetic-boundaries/v2`。包含來源支持的三向握手流程及頻寬／吞吐量對照。7 份 WAV 實際 hash 均與 Podcast 及場景描述一致；第 1 集原稿／音訊與準備前相同。
- F5 桌機 Chromium：12 個邊界 seek 與 36 個 0.75／1.5／2 倍播放樣本均顯示正確段落。renderer 相對 media clock 的 P95 差 0.037 秒、最大 0.044 秒，不能當作 ASR 人工精度分數。來源自動暫停、來源第 6 頁與 390px 比較版面已驗證。

最後對齊修正的針對性檢查：`test_scene_alignment.py` 與 `test_podcast_scenes.py` 共 10 項通過；`e2e/mock/podcasts.spec.ts:302` 同步案例通過；TypeScript／production build 通過。其餘相關測試依各次變更分批執行，非宣稱一次全站測試全綠。

必要私有證據保留於 `data/voice/acceptance/summary.json`、`data/scenes/acceptance/browser-sync-v2.json`、`prepared-v2.json` 及對應截圖；部署版本與備份在 `data/deployments/voice-research-20261004/`。不提交教材、帳號或生成內容。

原教材及新地圖的 `partial`／`needs_review` 保留。自然度、聽感與美感待精修；實體麥克風、手機鎖屏／真正背景凍結及其他瀏覽器待實機。headless 開新分頁時 `document.hidden` 仍為 false，不把此當成背景恢復證據。40 段中 1 段自第 3 字才匹配，未杜撰開頭時間；本輪是段落同步，不是逐字高亮。

## Podcast teaching beats 工程驗證（2026-10-05）

基線 `e62d7415c1a2e15b19f5083a227f3a14709dd42d`。以下工程回歸使用合成教材、隔離 PostgreSQL、受控 provider 與 CPU FFmpeg，不呼叫 Luna、CosyVoice、Whisper 或 GPU；後續實際部署與模型驗收另列於文末。舊 Podcast 仍固定原 KS revision，不把工程測試當成真實教學、ASR 精度或音質驗收。

主要新增入口：

- `test_podcast_beats.py`：多來源 beat、重複 claim ID 的來源位置、逐 part 引用、claim 跨 beat、原全集字數預算及雙 blocking verdict。
- `test_podcast_audio.py`：首尾 trim、內部停頓／保護區、語境停頓、非有限值及無聲拒絕；真 FFmpeg two-pass mastering、AAC 解碼量測、實際 1080p60 編碼與 bundle 驗證。
- `test_podcast_timeline.py`：原文字元範圍、VTT escaping、舊影片與 refined scene 的唯讀 locator 投影；stored manifest／script hash／VTT 不變。
- `runtime/materials/test_podcast_interaction.py`：A revision Podcast 在教材更新至 B 後，Voice／來源／Assessment 仍使用 A；偽造 locator、錄音 context、冪等、取消／刪除晚到、0016 additive upgrade，以及播放／提問不產生作答事件。
- `runtime/materials/test_podcast_interaction_browser.py` + `e2e/api/podcast-interaction.spec.ts`：真 API／DB 的來源開啟、Voice 問答與語音、VTT、末集 CTA、原 revision Assessment handoff。
- 既有 Podcast／scene／video／Voice／migration／Assessment／study／source revision 回歸，以及 `e2e/mock/podcasts.spec.ts`、`material-tools.spec.ts`。包括 seek／倍速／audio→video handoff、字幕開關、無 alignment 手動選段、固定提問 context、completed session 不重開、過期 cue 拒絕及手機版面。

主機的純測試環境可唯讀使用現有 CPU renderer 的 Pillow／imageio-ffmpeg；不修改共用 backend venv：

~~~bash
PYTHONPATH=backend/src:backend/tests:local_ai/src:data/podcast/video-runtime/lib/python3.12/site-packages \
  backend/.venv/bin/pytest -q backend/tests --ignore=backend/tests/runtime
~~~

integration 使用上文的 disposable PostgreSQL fixture。執行 API browser 前建置專用 frontend dist，設定 `STUDYDY_E2E_FRONTEND_DIST`、`STUDYDY_E2E_FRONTEND_PORT=4183`、`STUDYDY_E2E_API_PORT=8002`，不要與產品 ports 共用，也不要平行執行占用同一組 ports 的 browser runners。

最終主機 backend 單元套件 **343 項通過、0 skip**；無網路容器含 local_ai 測試為 **353 項通過、4 skip**，四項為容器缺少 Pillow／FFmpeg 的實際媒體測試，已由前述主機套件實際執行。最後的真 API handoff／context／scene／video 批次 **27 項通過**。

已執行的相關批次包括 Podcast／Voice／migration 53 項、後續 context／影片 27 項、Voice revision／lease 17 項、Assessment 加真 API handoff 45 項、study／source revision 43 項；批次有重疊，不相加宣稱獨立覆蓋率。前端 Node tests、TypeScript、production build 通過；Podcast mock 18 項、共用 material-tools mock 12 項通過。新增來源投影後另重跑 timeline／beats 24 項及真 API／影片回歸。失敗案例均修正後針對性重跑，不把初次失敗或 skip 當成通過。

### 30fps benchmark

重跑入口只生成合成資料，不連產品服務：

~~~bash
data/podcast/video-runtime/bin/python ops/podcast/benchmark.py .studydy-runtime/podcast-beats/benchmark
node ops/podcast/benchmark_browser.mjs .studydy-runtime/podcast-beats/benchmark
~~~

同機器、1920×1080、相同字型／音訊／cue／plan、H.264 fast／CRF18／4 threads，三次交錯配對。此量測涵蓋 render／encode，不包含 TTS、ASR 或模型延遲。

| 合成案例 | 長度 | 60fps 中位時間 | 30fps 中位時間 | 速度比 |
| --- | --- | --- | --- | --- |
| 靜態頁 | 6s | 7.320s | 3.755s | 1.95× |
| reveal／trace | 9s | 10.798s | 5.450s | 1.98× |
| 較長片段 | 24s | 28.975s | 14.505s | 2.00× |

三組每次皆完整解碼通過，固定時間點的文字／版面對照無明顯差異；實際 Chromium 前後 seek 的 reveal 狀態一致。0.5／1／2× 播放皆有量測 frame callback 與 media clock，最大差約 0.050 秒，這不是 ASR 精度。0.5× 時，60fps 成品的 presentation 約 30 frames/s，30fps 成品約 15 frames/s，trace 的時間取樣更粗；因此尚未滿足動態呈現無退化的條件，**正式維持 60fps**，不單憑 CPU 收益切換。

量測摘要保存在 ignored 的 `.studydy-runtime/podcast-beats/benchmark/summary.json` 與 `browser-summary.json`；比較圖及合成影片可由上述命令重建，不加入 Git。此結果不代表長課程、實體手機、其他瀏覽器或真實教學內容已驗收。


## Teaching beats 公開站真實驗收（2026-10-05）

使用指定測試帳號、兩份既有教材、四種 quick/full × solo/dialogue 模式，實際呼叫原有 Luna、CosyVoice／GPU、Whisper 與 CPU renderer。這次亦已在 studydy.net 套用 0016；DB／artifacts 備份及映像身分留於本機 `data/deployments/podcast-beats-20261005/`，設定原檔未複製或更改。私有驗收資料留於 `data/podcast/beat-qualification-20261005/`，不加入 Git／CI。

本輪修正後的 Podcast provider／timeline／renderer／video service 與隔離影片 integration 回歸共 99 項通過。

真實生成發現並修正：模型直接抄寫 Evidence hash 時會引用同頁 context 的其他區塊，因此改用 claim 內的整數索引與受限 schema，由程式回填原 ID。保存的 v2 script contract 與 canonical KS 不變。分鏡錯誤另補入 reveal、跨行 quote、trace 目標、文字重疊及箭頭落點的可操作回饋；版面修正候選的 emphasis 限為空陣列，後續仍須全部驗證及雙 blocking review。

| 模式 | Beats／turns | 音訊長度 | WAV LUFS／dBTP | MP4 結果 |
| --- | --- | --- | --- | --- |
| quick solo | 2／3 | 91.25s | −19.01／−1.98 | ready，7 cues |
| full dialogue | 3／6 | 82.61s | −19.05／−1.97 | ready，7 cues |
| full solo | 3／3 | 92.15s | −19.01／−1.98 | **未通過：VIDEO_LAYOUT_INVALID**，未發布影片 |
| quick dialogue | 2／8 | 140.95s | −19.09／−1.99 | ready，7 cues |

四份音訊的來源、part 引用、實際 WAV 格式與 hash 已核對。三份已發布影片的逐字還原、cue evidence 交集、script/audio hash、VTT、60fps 與 AAC 成品量測通過；完整 MP4 解碼成功。公開站實測 0.5／1／2×、前後 seek、字幕開關、來源頁暫停、390px 版面，以及關閉自動接續後的末集 Assessment 入口。full solo 的分鏡在有界重試後仍有排版錯誤，保留失敗與可播放原音訊，**不能宣稱四模式影片或整體模型品質已全面驗收**。

Voice 完成文字提問、實際語音回答、來源引用及互斥播放；另以既有真實 CosyVoice 音訊作虛擬麥克風輸入，經 MediaRecorder → 真 Whisper → draft → 修改後送出，確認原 Podcast locator／revision 保留。辨識曾有誤字，draft 確認沒有被跳過；這不是實體麥克風或人耳音質驗收。

播放與 Voice 後，原有四筆作答事件不變。之後在測試帳號實際回答原 revision 的一題 Assessment，正確交卷後恰新增一筆作答事件。另有一份排隊 Podcast 在生成前取消，未產生 script／audio。原有五份 Podcast、十份影片 manifest 與四份 KS 的指紋保持不變；素材原有 partial／needs_review 狀態未升級。

本輪有記錄的文字請求加上首輪失敗工作的保守預留，上界為 79 次（驗收上限由 60 調至 80），不等同精確帳單或 token 計量。自動 review 並未消除所有教學風格問題：full dialogue 樣本仍有規律問答與結尾重述；真實語音的發音、弱音裁切與自然度仍需人耳複核。原教材圖片素材仍 deferred，renderer 保持 60fps。

## Podcast 第二輪品質修正（2026-10-05）

基線為 `dev` 的 `c719e62c0e93de6d900bac49ec39b1b5a6dc13a9`，功能分支 `feature/podcast-quality-round2`，不合併 dev。本輪沿用 Luna／CosyVoice／Whisper、既有 teaching beats 與 exact KS revision；沒有模型或持久資料搬移。第一輪未完成的 full solo 曾在移除非必要排版限制後重試成功（92.15 秒、3 頁、4 個原有 cue）；本輪另建樣本，不重製那份產物。

### 工程回歸

- 最終 backend 單元套件 **414 項通過、0 skip**，包括模式預算、提問 beat 合併、可定位的重述訊號、獨立字幕錨點、語意版面、細揭示／反向 seek、音訊組裝與原有純邏輯回歸。
- Podcast／scene／video／interaction／Voice 的隔離 PostgreSQL 回歸 **46 項通過**；加入字幕時機寫入邊界後，影片 integration **11 項通過**。批次有重疊，不相加宣稱獨立案例數。
- 細字幕與完整 teaching scope 的 API／Playwright 批次 **2 項通過**：字幕比教學單位細，Voice 仍收到完整原段落，Assessment 留在原 revision，不建立作答事件。
- 前端 Node **64 項通過**、TypeScript 與 production build 通過。沒有以這些合成測試宣稱真實教學或人耳音質通過。

新增／擴充的主要入口為 `test_podcast_quality.py`、`test_podcast_cues.py`、`test_podcast_spoken_input.py`、`test_podcast_video_layout.py`、既有 provider／video／audio 測試，以及 `runtime/materials/test_podcast_interaction*.py`、`test_podcast_videos.py`。主機可唯讀使用既有的渲染與正規化依賴，不修改共用 backend venv：

~~~bash
PYTHONPATH=backend/src:backend/tests:local_ai/src:data/podcast/video-runtime/lib/python3.12/site-packages:data/podcast/cosyvoice-b-runtime/lib/python3.12/site-packages:data/podcast/cosyvoice-runtime/lib/python3.12/site-packages \
  backend/.venv/bin/pytest -q backend/tests --ignore=backend/tests/runtime
~~~

### 真實成對樣本

使用已授權的兩份代表教材、原 revision 與測試帳號。單人組在相同四個頻寬／吞吐量來源上比較 Quick／Full；雙人組在相同四個協定來源上比較 Quick／Full，不再拿不同來源的時長直接比較。每組 claim／Evidence 完全相同，原教材的 partial／needs_review 不升級。

| 模式 | 講稿字數 | Beats／turns | 音訊秒數 | Teaching cues／caption cues | 頁數 | 最長字幕字數 |
| --- | ---: | --- | ---: | --- | ---: | ---: |
| quick solo | 121 | 2／2 | 25.06 | 2／5 | 1 | 28 |
| full solo | 177 | 2／2 | 35.91 | 2／6 | 2 | 41 |
| quick dialogue | 200 | 2／4 | 42.85 | 2／7 | 2 | 40 |
| full dialogue | 202 | 1／3 | 41.43 | 2／8 | 2 | 40 |

四份最終音訊與 MP4 均完成，script／video 的 correctness、teaching-quality 兩項審查均通過；canonical 來源、逐 part／cue 交集、音訊／講稿／影片 hash、60fps、AAC 成品量測及完整解碼皆核對。桌機 Chromium 實測 0.5／1／2×、前後 seek、字幕、390px 無橫向溢位及關閉自動接續後的末集 CTA。雙人圖解使用實測 caption 錨點，在保留兩個 teaching cues 的同時支援細揭示；Quick／Full 分別有 3／5 組 caption 錨點 reveal。來源 PDF 入口暫停播放，字幕沒有成為新的來源或提問單位。

**模式差異仍有限制**：單人組有明顯篇幅差異，但對談組僅差兩字，Quick 反而比 Full 長約 1.41 秒。較低的 Quick 上限與不放大的雙人字數預算已生效；這份短、重疊度高的來源仍不足以證明每組 Full 都更長或更深入。沒有加最低字數或固定秒數來湊出差距。

### 實際失敗與成本

首批雙人稿仍有確認式問答／重述；Full 後續也曾因重述、錯誤引用與把「可獨立」擴成「不會同時」而被拒絕。原 reviewer 還曾把 `reveal=false` 誤當隱藏，或把 exchange 的空 `text` 誤當沒有參與者標籤。修正了這些契約提示、純提問 beat 的組織及強制收尾提示衝突後，才取得上述最終樣本。初稿保留待修名稱與失敗紀錄，沒有把被拒絕的輸出當成通過。

共記錄 **58 次文字模型請求**：16 次產稿、13 次語意分鏡、28 次審查、1 次 Voice 回答。包含取消競態中已在途的兩次產稿／審查請求；原自訂 50 次上限因最後一項提示修正明確追加至 62，未隱藏重試或只統計成功結果。正常成功路徑由「script＋review＋cue＋storyboard＋review」五次降為四次；每次 script／video 工作仍各有四次／八次上限。這不等於整批實際費用或 token 數下降，本輪重試仍是主要成本。

13 份語意分鏡候選中，**0 次 `VIDEO_LAYOUT_INVALID`**；另有 1 次無效 cue 引用及多次內容／教學審查失敗。這是小樣本的版面觀察，不代表所有素材皆能生成或模型語意全面正確。

### 分集與節奏量測

在兩份原 KS 上離線規劃全部有來源的概念，不呼叫模型、不新增第二套知識：

| 真實來源 | 概念／claims | 原集數 → 新集數 | 概念內切點：原 → 新 |
| --- | --- | --- | --- |
| 網路教材 | 31／189 | 32 → 34 | 27 → 15 |
| 協定教材 | 6／17 | 4 → 4 | 2 → 1 |

全部 claims 的順序、身分與引用保持相同，各集仍不超過六個 claim／2,400 來源字元；第一份多出兩集是保留概念完整性的取捨，不是任意增加容量。

以同一份既有 397 字講稿、相同 CosyVoice／B 聲線做 v11／v12 真實語音比較，未替換產品音訊：總長 92.15 → 83.67 秒，20ms RMS 視窗下、至少 120ms 的內部安靜區段 64 → 45，累計 14.46 → 10.46 秒。最長單段停頓反而由 0.48 → 0.60 秒，不能宣稱每處都改善。v12 成品為 −19.03 LUFS／−1.98 dBTP，mastering policy 未變。ASR 對部分縮寫仍有重複／誤辨；這些量測不能替代人耳對發音、弱音和自然度的驗收。

合成 CPU 畫格 benchmark 在同機器、1920×1080、60fps 下交錯三次，每次 180 frames：中位時間 1.681 → 1.642 秒，抽查畫格 pixels 一致；只量測繪圖，不含編碼或模型。快取收益很小，不用這個結果宣稱整體生成顯著加速，也沒有改成 30fps。

### 互動與保留

新 Quick 對談的 Voice 提問收到完整 **98 字 teaching scope**（該集最長 caption 為 40 字），保留原 revision、script hash 與兩個 turn 的精確範圍；真實回答含兩個來源引用及可播放語音，播放回答時 Podcast 暫停。Assessment handoff 打開同一 KS revision 的檢測頁，本輪未生成或回答新題目。

取消測試遇到產稿已在途，取消後即使兩次模型請求返回，script／audio 仍未發布。舊 v11 MP4、13 個原 VTT cues 與提問入口繼續可讀。原有 **10 份 Podcast episode 資料、14 份影片 manifest、4 份 KS** 指紋保持不變，原 **5 筆作答事件**不變；播放／Voice 沒有改寫掌握度。臨時計數 wrapper 已移除，正式 provider 啟動方式恢復。

必要私人證據保存在本機 `data/podcast/quality-round2-20261005/`（summary、audit、before／after、request ledger、被拒絕候選、瀏覽器、媒體與節奏量測），部署身分與 rollback image tags 位於 `data/deployments/podcast-quality-round2-20261005/`。教材、產出原文、帳號、錄音、憑證與私有設定不加入 Git／CI；沒有搬入 archive 或複製可重建環境。

## Podcast deterministic 2D motion（2026-10-05）

基線為 `feature/podcast-quality-round2` 的 `21906b7daa991a2ce296a504bac5843fc1e55224`。本輪只擴充呈現層：v3 storyboard 保存可信 layout 編譯的節點／關係群組與既有 cue／caption 索引，模型 schema、講稿、來源、字幕切分與音訊政策不變。新片使用節點／文字 fade、連線 draw-on、平滑明暗與淡底焦點、沿既有箭頭的一次性指示，以及最長 0.2 秒的換頁 crossfade；不縮放文字、不循環播放指示。每個時間點皆由保存資料直接計算，沒有累積影格狀態。

### 工程與實際播放驗證

- 相關 unit／renderer／service 套件 **106 項通過、0 skip**，其中新增 motion 套件 32 項。涵蓋 enter、焦點、relation、flow／exchange、換頁、reveal／emphasis、短字幕／同時 anchor、往返 seek、0.5／1／2× 媒體時鐘、legacy、重複渲染，以及 geometry／來源／時間驗證。實際畫格曾發現 Pillow 在 RGB 畫布忽略文字 fill alpha，已改為文字區明確合成並加入字形像素回歸。
- disposable PostgreSQL 的影片 integration **15 項通過**；v2／v3 均檢查 ready 重開不重製、ownership／range、cancel／retry／late-result，以及無效 motion anchor 不得寫入 artifact。
- 重用 Round 2 四份已核可的語意分鏡、原 WAV 與 Whisper 對齊；**新增模型／TTS／ASR 呼叫均為 0**。產生四份私人獨立樣片，25.06／35.91／42.85／41.43 秒，皆為 1920×1080、60fps，完整 MP4 解碼與既有 AAC 成品量測通過。舊 flat elements、文字、reveal／emphasis、頁數及來源 timeline 未改。
- 四片在 headless Chromium 各測 0.5／1／2×，跨動態邊界的媒體時間單調，最大畫格／播放時鐘差約 54ms；114 次 seek 取樣涵蓋字幕和換頁前後，重訪同一時間的解碼畫格 hash 一致。這些是播放正確性量測，不是視覺品質分數。
- 舊 v2 renderer 與本輪 legacy 路徑在四份原分鏡共 20 個抽查時間的像素完全相同；產品中原四份 episode／ready manifest 未變，原 MP4 artifact 完整性與保存副本 hash 核對通過。本輪未部署、未替換 ready 影片。

~~~bash
PYTHONPATH=backend/src:backend/tests:local_ai/src:data/podcast/video-runtime/lib/python3.12/site-packages \
  backend/.venv/bin/pytest -q backend/tests/test_podcast_video_{motion,plan,layout,render,service}.py
PYTHONPATH=backend/src:backend/tests/runtime:backend/tests:local_ai/src:data/podcast/video-runtime/lib/python3.12/site-packages \
  backend/.venv/bin/pytest -q backend/tests/runtime/materials/test_podcast_videos.py
~~~

### 視覺抽查與限制

逐時序檢視四份樣片的進場、焦點與場景延續，並從實際編碼的 MP4 擷取 flow、反向 exchange 與 crossfade 前後畫格檢查。可見舊資訊保留、新資訊漸進建立，關係指示沿原方向移動後消失；文字不位移或縮放，額外 trace 不再產生第二顆指示。這是主要 motion cases 的視覺抽查，**不是完整真人觀影、教學成效研究或全面視覺品質驗收**。

既有粗 anchor 仍可能同時呈現多個節點／關係；不為增加動畫而猜測細時序。換頁 crossfade 在短暫窗口會有疊影，長 teaching cue 中沒有新語意 anchor 時也不持續添加動畫，因此尚不能宣稱完全消除簡報感。原 cue 標題與既有文字排版不在本輪重寫範圍。未測所有瀏覽器／行動裝置的主觀流暢度；倍速量測只代表本機 Chromium。

私人樣片、解碼時序圖與測量結果保留在本機忽略目錄 `data/podcast/semantic-motion-20261005/`，沒有加入 Git／CI。升級順序與 v2／v3 的分階段相容方式見 [安裝文件](getting-started.md#純平面影片與講稿時間軸)。
