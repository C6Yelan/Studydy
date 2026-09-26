# 測試

[文件入口](../README.md) · [安裝與啟動](getting-started.md)

## 選擇測試範圍

| 變更 | 優先執行 |
| --- | --- |
| Evidence、結構、題目或純邏輯 | backend/tests 中相關表層測試 |
| API、DB、worker、來源及交易 | backend/tests/runtime 對應領域 |
| 跨領域調整或後端收尾 | 完整 backend/tests，包含真 API／DB browser fixtures |
| OCR protocol／adapter | local_ai/tests |
| 前端 API client、路由或投影 | Node tests 與 TypeScript |
| UI 與互動 | 獨立 production build ＋對應 Playwright spec |
| 文件 | 路徑／連結、命令與現行實作核對 |

通過後只有新變更、失敗或未解疑慮才擴大或重跑。測試數量不是品質門檻，也不要求為每個小改動啟動全部整合環境。

## 環境與隔離

不安裝主機 Python 套件也能先執行後端表層與 local AI 測試：

~~~bash
docker compose -p studydy-unit-tests -f compose.test.yaml run --build --rm unit
~~~

此測試容器無網路、無產品資料掛載，也不需要 GPU。前端映像建置會執行 Node 測試、TypeScript 與 production build。

以下原始碼測試命令供開發環境使用：需自行準備 backend/.venv、frontend/node_modules，以及真轉檔使用的系統工具；它們不是容器部署的主機必要條件。瀏覽器測試另需 Chromium：

~~~bash
npm --prefix frontend run e2e:install
~~~

Backend runtime 測試預設建立 pinned PostgreSQL 18 disposable container，每個 DB 案例建立空白資料庫並清理。需 Docker 權限；文件轉檔需 bubblewrap namespaces。

可明確提供 STUDYDY_TEST_POSTGRES_DSN，僅接受本機、專用 studydy_test* control database，並核對 PostgreSQL 版本及 superuser 權限。不要指向產品 DB。

教材與檔案使用合成 fixture／暫存目錄；模型使用受控回應或 MockTransport。測試不需模型、Pod、私人 PDF 或產品 store。系統工具缺失應修正環境，不藉 skip 當成完整通過。

## 後端

純邏輯測試：

~~~bash
PYTHONPATH=backend/src:backend/tests:local_ai/src \
  backend/.venv/bin/pytest -q backend/tests/test_*.py
~~~

Runtime 依 accounts、assessments、infrastructure、materials、sources、study 六個目錄分類；共用 fixture 在 runtime 根目錄。

完整後端包含 browser，先建立獨立前端 bundle：

~~~bash
export STUDYDY_E2E_FRONTEND_DIST="$PWD/.studydy-runtime/test-frontend"
export STUDYDY_E2E_FRONTEND_PORT=4183 STUDYDY_E2E_API_PORT=8002
npm --prefix frontend run build -- --outDir "$STUDYDY_E2E_FRONTEND_DIST" --emptyOutDir

env -u STUDYDY_TEST_POSTGRES_DSN -u STUDYDY_DATABASE_DSN \
  PYTHONPATH=backend/src:backend/tests:local_ai/src \
  backend/.venv/bin/pytest -q backend/tests --durations=10
~~~

這個命令強制使用 disposable DB。若要使用已配置的專用測試 control database，移除對 STUDYDY_TEST_POSTGRES_DSN 的 unset。

只跑某個領域時，把最後的 backend/tests 換成對應目錄，例如 backend/tests/runtime/sources；含 browser 的目錄仍需上方 build 與 ports。

## Local AI

~~~bash
PYTHONPATH=local_ai/src backend/.venv/bin/pytest -q local_ai/tests
~~~

這裡驗證 protocol 與 adapter，不載入 OCR 權重，也不連線語意服務。實際 runtime verify 是另一種操作，見安裝文件。

## 前端

E2E 只依執行方式分資料夾，功能由檔名辨識；共用合成資料與 browser helpers 放在 `frontend/e2e/fixtures/`。

地圖共用 fixture 的 `mockKnowledgeMapApi`／`mockLearningMapApi` 限定教材、revision、題組 ID 與 HTTP 方法；未設定的請求回傳 404／405，來源回查依 Evidence 的頁碼回應。特定失敗情境可在案例中明確覆蓋路由。

學習版面共用 `studyLayoutFixture` 同樣限定路由 ID／HTTP 方法，過期 guidance 回傳 409；題組統計由實際題目與 feedback 推導，未發布或未作答的重點沒有答案事件，歷史題組不隨目前題組的版本改變。

- `frontend/e2e/mock/`：攔截 API 回應，驗證 UI、互動與公開契約，不需真實 API／DB。
- `frontend/e2e/api/`：需要對應 Python fixture 啟動隔離 API／DB；失敗注入可能攔截個別請求，但不代表整組是 mock 測試。

帳號測試的 cookie、owner 隔離與伺服器驗證保留在 `api/accounts.spec.ts`；表單互動、版面、逾時重試與登入競態由 `mock/accounts.spec.ts` 驗證。共用錯誤碼／訊息映射由 `frontend/src/api/client.test.mjs` 驗證。

錯題補強的完整 API／DB 流程由 `api/assessment-remediation.spec.ts` 在代表性桌機尺寸執行；桌機／手機的結果呈現、紀錄與下一步互動由既有 `mock/study-results.spec.ts`、`mock/study-rail.spec.ts`、`mock/study-next-step.spec.ts` 驗證。

整組交卷的 `api/assessment-sets.spec.ts` 保留「版本衝突同步後回應遺失」與「直接交卷回應遺失」兩種 API／DB 情境，不以畫面尺寸切換情境；卡片版面、準備狀態與結果呈現由既有 `mock/study-grid.spec.ts`、`mock/study-layout.spec.ts`、`mock/study-results.spec.ts` 驗證。

觀念切換的 `api/concept-navigation.spec.ts` 在代表性桌機尺寸驗證題組互不阻擋、衝突接續與原題目恢復；桌機／手機導覽、觀念入口與下一步互動由既有 `mock/knowledge-map-navigation.spec.ts`、`mock/knowledge-map-learning-entry.spec.ts`、`mock/study-next-step.spec.ts` 驗證。

初始混合來源的 `api/initial-sources.spec.ts` 在代表性桌機尺寸驗證上傳與分析分開、來源排序、三份來源納入同一結構及逐筆 Evidence 回查；桌機／手機的上傳與來源確認介面由既有 `mock/upload.spec.ts`、`mock/source-confirmation.spec.ts` 驗證。

教材列表的 `api/material-library.spec.ts` 驗證新瀏覽器從伺服器找回教材、工作區開啟目前 head、歷史版本 API 的 revision／run 綁定、唯讀且不呼叫模型，以及帳號／PDF 快取隔離；卡片樣式與各處理狀態的介面由既有 `mock/material-collection.spec.ts`、`mock/library-state.spec.ts` 驗證。

單檔轉換的 `api/normalization.spec.ts` 驗證原檔完整內容、PDF 預覽、重新整理後的持久化，以及轉換完成時沒有分析工作、明確點分析後才建立待處理工作；此 fixture 只跑轉換 worker 並禁止模型 HTTP，轉換狀態與版面由既有 `mock/normalization.spec.ts` 驗證。

~~~bash
npm --prefix frontend test
npm --prefix frontend run typecheck
~~~

Node runner 的摘要可能以檔案為單位。需要檢查個別案例時，可直接執行相應檔案，例如：

~~~bash
node frontend/src/api/client.test.mjs
~~~

瀏覽器使用上方獨立 build 與 ports；由 runner 啟動暫存 Vite preview：

~~~bash
PYTHONPATH=backend/tests/runtime backend/.venv/bin/python - <<'PY'
from browser_e2e_runner import main

raise SystemExit(main("e2e/mock/", timeout_seconds=600))
PY
~~~

這個命令執行全部 mock browser 測試。可改成 `e2e/mock/knowledge-map-` 選擇地圖測試，或 `e2e/mock/knowledge-map-details.spec.ts` 選擇單檔；不帶 spec 時 runner 預設執行這個單檔。多檔也可使用 Playwright 檔名 regex；runner 的 timeout_seconds 是整組執行上限，不改單項 timeout 或 retries。

需要真 API／DB 的 spec 由對應 Python fixture 啟動，例如 runtime/assessments/test_assessment_sets_browser.py；不要手動設定啟用旗標來冒充 fixture。單獨執行 Playwright 而出現 fixture skip，不算該流程已驗證。

## 必須保護的行為

- Owner／session 隔離，題目私密答案與來源綁定。
- 整組交卷原子性、回滾、冪等與版本衝突。
- 教材刪除、檔案 quarantine／reconciliation 與其他教材完整性。
- Checkpoint 保存、接續、發布後清理及晚到 worker。
- Migration checksum、序列、併發安裝與失敗回滾。
- 真轉檔的內容、來源定位，以及瀏覽器恢復與互動。

## 結果與品質驗收

執行結果記錄命令、範圍、通過／失敗／skip、耗時及環境限制，放在該次變更說明或測試產物；不持續把歷次數字追加到本文件。

模型品質驗收需另外指定模型快照、來源與審查標準，透過實際產品流程產生可回查內容，再人工核對。HTTP 成功、schema 通過及合成 fixture 均不代表語意正確。現有覆蓋缺口見 [限制](limitations.md)。
