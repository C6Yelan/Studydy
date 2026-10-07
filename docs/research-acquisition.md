# 公開來源取得比較（2026-10-07）

保留 production 的 direct HTTP／provider API 與 metadata-only fallback；本次不加入 Chromium runtime。十個固定公開樣本中，A direct 與 C Chromium 均取得九份回應，符合本次正文／身分／授權檢查的各三份；A+B 的聯集為四份。B 新增的是 Europe PMC JATS XML 的開發解析，尚未接入產品。這是小型、目的性選樣的結果，不代表全網 coverage 或模型品質。

## 範圍與可重跑入口

實作位於 `ops/research/`，不讀產品設定、DB、教材或模型，不寫 canonical ingestion。既有 production 已有 OpenAlex、Crossref、選用 Semantic Scholar、Python docs、MDN 與 opt-in Unpaywall；本次未再建搜尋系統。

```bash
PYTHONPATH=backend/src backend/.venv/bin/python ops/research/benchmark.py \
  --output .studydy-runtime/research-acquisition-new-run
PYTHONPATH=backend/src backend/.venv/bin/pytest -q ops/research/test_egress.py ops/research/test_benchmark.py
node --check ops/research/browser.mjs
```

需要既有 `backend/.venv`、`frontend/node_modules`、Playwright Chromium、公開網路及本機 socket 權限。沿用開發依賴；不安裝新 production 套件。重新取得可能因網站、DNS、版本或網路改變而有不同結果。

本次保存於 `.studydy-runtime/quality-hardening-20261007/D-result/`：

- `results.json`：40 列 A/B/C/D，含 HTTP、final URL、type、實際 bytes、title／identity、license／source、extraction、elapsed、failure、資格判定。
- `*.body`、`*.dom`、`*.txt`：實際回應、rendered DOM、抽取文字；各自 SHA-256 留在結果內。不是產品教材。
- `licenses.json` 與 `license-*.body`：Python／MDN 的獨立授權快照。
- `direct.json`、`browser.json`：原始量測。沒有 HTML title 的 XML／JSON 頁面，最終分析標為身分尚未驗證，沒有誤稱 DOI 不符；此離線分類更正在 `run.json` 記錄，取得 bytes 未更動。
- `run.json`：完成時間、樣本與程式 hash。`browser-input.json` 記錄當次固定 IP；其中 loopback port 只供該次執行，不可直接重用，應從上述 Python 入口重跑。

## 方法與檢查

A 使用現行 `research_sources.fetch`，保留 DNS 全 IP 檢查、固定 IP 連線、TLS server name、HTTPS 443、redirect 與 credential 規則。HTML 用現行 MainText。JSON／XML 另記診斷內容；A 的 XML 不冒充現行 production 支援。

B 對同一文件使用官方正規路徑。Python 的 B 是 canonical URL，與 A 相同；XML 的 A/B URL 也相同，差別是 B 明確使用 PoC JATS parser。不是所有樣本都有正式 API，缺少者記 `not-available`，不當作網路失敗。

C 是一般 headless Chromium，無登入、stealth、UA 偽裝、CAPTCHA 解題、外部／輪換 proxy。每個樣本新 context，阻擋 service worker、WebSocket、popup 與非必要影像／media／font。僅允許固定 manifest hosts；所有 DNS 位址須為 public，連線固定至核對過的 IP。本機 CONNECT 閘道只允許這些 host 的 443，阻擋 loopback／metadata IP／未知 host；它是出口限制，不是網站存取繞道。TLS 仍由 Chromium 正常驗證。

PoC 限制：每個 navigation 最多五次請求（四次 redirect）、每頁最多 60 個受控 request、20 秒 navigation／30 秒 context、每 CONNECT tunnel 8 MiB、CDP 另監看頁面解碼 bytes、DOM 8 MiB、抽取文字 200,000 字元。頁面總 bytes 的 CDP 停止是事件式，不能宣稱零超額的硬性總量配額。這套固定公開樣本工具不是接受任意 URL 的 production 安全隔離器。

D 不取得全文，只保留 manifest 的候選 metadata／原始 link；其身分未重新核實，也不代表可引用的全文。

正文資格要求：取得成功、身分核對成功、授權符合既有範圍、正文 markers 完整（官方 HTML）或存在 JATS body（B XML）。這個小型檢查不代表所有表格、公式、圖說、附檔與整篇內容已完整轉換。JATS 以文章 DOI 核對；HTML 以該固定樣本的 title 核對，不是新通用 identity 演算法。

## 實際結果

A/B/C 的數字為 HTTP status；「可用」只表示上述 benchmark gate，未寫入教材。D 十列皆為 metadata/link-only。

| 樣本 | A direct | B 官方路徑 | C Chromium | 授權／結果 |
|---|---|---|---|---|
| Python Data Structures | 200，可用 | 200，同一官方頁，可用 | 200，可用 | PSF-2.0；獨立授權快照 |
| Python tutorial redirect | 301→200，可用 | 200 canonical，可用 | 301→200，可用 | PSF-2.0；保留 redirect chain |
| MDN Array | 200，可用 | 未選獨立全文 API | 200，可用 | CC BY-SA 2.5-or-later；需保留署名／相同授權 |
| Quotes `/js/` | 200，MainText 無正文 | 無 | 200，渲染出 1,420 字元 | 授權未知，不可加入 |
| Europe PMC PMC12900525 | 200 XML；production 不支援 | 200 JATS body，35,649 字元 | 200，raw XML 可讀；未核對結構身分 | CC BY-NC-SA 4.0，超出既有接受政策 |
| Europe PMC PMC13466022 | 200 XML；production 不支援 | 200，DOI 相符，45,205 字元，可用 | 200，raw XML 可讀；未核對結構身分 | 明確 CC BY 4.0；值得評估 JATS adapter |
| Crossref nature12373 | 200 metadata | 200 metadata | 200 JSON，可見但不是全文 | metadata／abstract 不等於全文再利用授權 |
| Quotes static | 200，MainText 無正文 | 無 | 200，1,500 字元 | 授權未知；新增來自 selector，不是 JS 優勢 |
| httpbin `/status/403` | 403，停止 | 無 | 403，停止 | 只保留 metadata/link |
| Python 頁＋故意錯誤 title | 200，身分不符 | 200，身分不符 | 200，身分不符 | 有授權也不可接受錯文件 |

A/C 原始取得同為 9/10；正文資格各 3/10，C 相對 A+B 沒有新增合格樣本。A+B 聯集 4/10。重複文件與負向樣本刻意包含在分母，不能外推成產品通過率。

成功回應的 elapsed 中位數：A 935 ms、B 1,403.5 ms、C 1,917 ms。這不是效能競賽：B 缺四個不適用項，C 包含渲染與更多 request，DNS／cache／網路條件不同。

人工檢查保存文字：Python 保留 queue／comprehension 與程式 literal；MDN 保留 Array methods；JS 頁出現固定 quote 節點；JATS 保留 body 的章節與段落。JS 原始 HTML 本來就帶資料，故此結果只證明現行 MainText 不執行渲染，不證明一定需要 browser 才能取得資料。XML 沒有在 C 重做完整 parser，不能把「沒寫 C parser」解讀成 Chromium 無法下載 XML。

**未驗證情境：direct 403、普通瀏覽器卻可合法讀取。** 本次拒絕樣本兩者均為 403；額外 Wikipedia 單次探測兩者均 200，保存在 `D-probe/`；Europe PMC 官方文件兩者均 403，保存在 `D-official-doc-probe/`。沒有找到此差異便停止擴大探測；不以合成結果或搜尋工具的不同網路出口假裝通過。沒有破解或繞過存取限制。這是本次要求的樣本涵蓋缺口。

## 正式路徑研究與決策

- Europe PMC JATS XML 值得下一步做小型 adapter：本次已確認 exact URL、DOI、body、CC BY 4.0 與 snapshot/hash。若接入，仍須明定表格／公式／圖說、版本與來源定位，再走既有 normalization；本次不因一篇樣本直接啟用 ingestion。[官方 REST 文件](https://europepmc.org/RestfulWebService)
- PMC 自動取得應走官方指定服務，不能把主站 browser 當作任意 downloader；可評估 BioC／OAI-PMH 或正式 cloud 分發。[PMC 開發者文件](https://pmc.ncbi.nlm.nih.gov/tools/developers/)
- Crossref 可供 metadata、license 與全文 links，不能將 metadata 授權視為所連文件的全文授權。現有整合保留。[Crossref REST 文件](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
- OpenAlex 目前提供 `content_urls` 的 PDF／GROBID XML，需要 API key 且依用量計費；下載並不額外授予內容權利。值得列入後續經核准的 content API 比較，本次未用 key、未花額度；不下載大型 dataset。[OpenAlex Fulltext](https://help.openalex.org/access/fulltext/)
- Unpaywall DOI resolver 仍要求 email；現有 opt-in 保留。官方文件顯示 search endpoint 已於 2026-09-18 退役，不新增依賴舊 search endpoint。[Unpaywall API](https://data.unpaywall.org/products/api)
- Chromium 此樣本沒有新增合格 coverage，不值得增加 production image、排程及攻擊面。保留此開發 PoC，待有具體、有授權且 direct/API 不足的來源再測。
- 不使用 opaque model browsing 取得 canonical 全文；本次沒有模型呼叫。AI 未決定 egress 或授權。

## 驗證

`backend/tests/test_research_sources.py` 22 passed；`backend/tests/runtime/materials/test_research.py` 11 passed（disposable PostgreSQL）；`ops/research/test_egress.py` 9 passed、`test_benchmark.py` 3 passed；`frontend/e2e/mock/material-tools.spec.ts` 20 passed。Node／TypeScript／production build 沿用同一未再修改的 C 前端已通過結果。初次 DB 與 socket 測試受 sandbox 限制，取得執行權限後重跑成功；不是略過測試。

D 未改 production API、DB、schema 或 UI，不需再部署。公開來源結果與 fake provider／隔離 DB 回歸分開解讀。
