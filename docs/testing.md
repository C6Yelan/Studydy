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

依 [安裝與啟動](getting-started.md) 安裝應用程式與轉檔依賴。瀏覽器測試另需 Chromium：

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

raise SystemExit(main("e2e/product-cutover.spec.ts", timeout_seconds=300))
PY
~~~

替換 spec 名稱即可執行其他 mock browser 測試。多檔可使用 Playwright 檔名 regex；runner 的 timeout_seconds 是整組執行上限，不改單項 timeout 或 retries。

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
