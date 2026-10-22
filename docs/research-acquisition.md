# 公開來源取得比較

`ops/research/` 保留開發比較工具，不是產品取得器。產品沿用 direct HTTP／provider API 與 metadata-only fallback，HTML 正文由 `runtime.research_web` 擷取；現行來源範圍見 [教材文件](materials.md)。

## 範圍與可重跑入口

比較工具不讀產品設定、DB、教材或模型，不寫入 canonical ingestion。既有搜尋整合沿用產品流程，不另外建立搜尋系統，也不因比較結果直接啟用 Chromium 或 JATS ingestion。

從 checkout 根目錄執行，結果放在新的系統暫存目錄：

```bash
research_output=$(mktemp -d /tmp/studydy-research-acquisition.XXXXXX)
PYTHONPATH=backend/src backend/.venv/bin/python ops/research/benchmark.py \
  --output "$research_output"
PYTHONPATH=backend/src backend/.venv/bin/pytest -q ops/research/test_egress.py ops/research/test_benchmark.py
node --check ops/research/browser.mjs
```

需要既有 `backend/.venv`、`frontend/node_modules`、Playwright Chromium、公開網路及本機 socket 權限。沿用開發依賴，不安裝產品套件；重新取得可能因網站、DNS、版本或網路而有不同結果。

輸出包含取得結果、實際回應／DOM／抽取文字及其 SHA-256、授權快照與執行身分。這些是比較資料，不是產品教材；確認結論後清理本次暫存目錄，不提交原始結果。`browser-input.json` 的固定 IP 與本機 proxy port 只適用該次執行，應從 Python 入口重新建立，不直接重用。

## 方法與檢查

| 方法 | 範圍與限制 |
| --- | --- |
| A：direct | 使用 `research_sources.fetch`，保留 DNS 全 IP 檢查、固定 IP 連線、TLS server name、HTTPS 443、redirect 與 credential 規則。比較工具用 `MainText` 擷取 HTML；JSON／XML 診斷內容不代表產品支援。 |
| B：官方路徑 | 使用同一文件的官方正規路徑；JATS XML 使用開發 parser。沒有獨立 API 的樣本記為 `not-available`，不當作網路失敗。 |
| C：Chromium | 一般 headless Chromium，不登入、不偽裝 UA、不使用 stealth、CAPTCHA 解題或輪換 proxy。固定樣本與出口限制不構成接受任意 URL 的產品隔離器。 |
| D：metadata | 不取得全文，只保留候選 metadata 與原始 link；身分未重新核實，也不代表可引用全文。 |

Chromium 每個樣本使用新 context，阻擋 service worker、WebSocket、popup 與非必要影像／media／font。僅允許 manifest hosts，所有 DNS 位址須為 public，連線固定至核對過的 IP；本機 CONNECT 閘道只允許這些 host 的 443，TLS 由 Chromium 正常驗證，不能用作存取限制繞道。

工具限制 navigation／context 時間、request／redirect 數量、tunnel／DOM bytes 與抽取文字長度，實際上限見 `ops/research/browser.mjs` 與 `egress.py`。CDP 的頁面 bytes 停止是事件式，不能宣稱零超額的硬性總量配額。

正文資格要求取得成功、身分核對成功、授權符合範圍，以及官方 HTML 的正文 markers 或 JATS body。固定樣本的 title／DOI 核對不是通用身分演算法，也不代表所有表格、公式、圖說、附檔或整篇內容均已轉換。

## 結果解讀與接入邊界

固定公開樣本含重複文件與負向案例，不能外推全網 coverage、產品通過率或模型品質。不同方法的 API 適用性、渲染工作、DNS、cache 與網路條件不同，elapsed 不能直接當作效能排名。

瀏覽器能顯示內容，不代表具有匯入授權；有授權也不能接受身分不符的文件。原始 HTML 已含資料時，渲染結果較完整可能只是 parser／selector 差異，不證明一定需要瀏覽器；未實作 XML parser 也不代表 Chromium 無法下載 XML。

尚未建立「direct 被拒絕、一般瀏覽器卻可合法讀取」的有效對照，不把合成結果或不同網路出口當成此情境的證據。遇到拒絕存取時保留 metadata／link，不破解或繞過限制。

## 正式路徑與產品決策

- JATS adapter 尚未接入產品。接入前須明定表格、公式、圖說、版本與來源定位，再走既有 normalization；不能因固定樣本可解析就直接啟用 ingestion。參考 [Europe PMC REST 文件](https://europepmc.org/RestfulWebService)。
- PMC 自動取得應走官方指定服務，不把主站瀏覽器當作任意 downloader。參考 [PMC 開發者文件](https://pmc.ncbi.nlm.nih.gov/tools/developers/)。
- Crossref metadata、license 與全文 links 不會自動授予所連全文的再利用權利。參考 [Crossref REST 文件](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)。
- 額外 content API 須另行核准資料與費用範圍；下載本身不授予內容權利，不為比較下載大型 dataset。
- 現有 Unpaywall DOI resolver 維持 email 設定的 opt-in，不新增舊 search endpoint 依賴。
- Chromium 僅保留為開發比較工具；有具體、獲授權且 direct／API 不足的來源時再評估，不能僅因頁面可渲染就增加產品 runtime。
- 不使用不透明的模型瀏覽結果取得 canonical 全文；AI 不決定出口或授權。

回歸入口為 `backend/tests/test_research_sources.py`、`backend/tests/runtime/materials/test_research.py`、`ops/research/test_egress.py`、`ops/research/test_benchmark.py` 與 `frontend/e2e/mock/material-tools.spec.ts`。公開來源的實際取得與 fake provider／隔離 DB 回歸須分開解讀，不把其中一者通過當作另一者驗收。
