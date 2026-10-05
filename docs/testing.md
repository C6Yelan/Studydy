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

基線 `e62d7415c1a2e15b19f5083a227f3a14709dd42d`。這次使用合成教材、隔離 PostgreSQL、受控 provider 與 CPU FFmpeg；未呼叫 Luna、CosyVoice、Whisper 或 GPU，也未部署／對產品 DB 套 migration。舊 Podcast 仍固定原 KS revision，不把工程測試當成真實教學、ASR 精度或音質驗收。

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
