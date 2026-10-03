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
