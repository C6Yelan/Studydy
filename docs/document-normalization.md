# 單檔文件轉換（B2-I）

PDF 是主要教材格式。其他已開放格式上傳後自動轉成 PDF，轉換品質不保證；保留原檔、轉換後預覽及來源回查。一次一個檔案；不支援追加來源、多檔或跨版本語意合併。

## 環境與啟用

PDF、DOC／DOCX、PPT／PPTX、UTF-8 TXT／Markdown 的單檔上限統一為 100 MiB（104,857,600 bytes），包含原 PDF v1 與新來源端點；轉換後 PDF 也使用相同單檔上限。不另設頁數、段落數、ZIP 項目數、解壓總量或壓縮比使用門檻。需要 LibreOffice **26.2.5.2** Writer/Impress、bubblewrap、fontconfig、Noto CJK 與一個獨立的 Python 3.12 converter 環境。不要修改正式版與競賽版共用的 backend/.venv。

```bash
uv venv --python backend/.venv/bin/python .studydy-runtime/normalizer-venv
uv pip install --python .studydy-runtime/normalizer-venv/bin/python -r backend/normalizer-requirements.txt
export STUDYDY_NORMALIZER_PYTHON="$PWD/.studydy-runtime/normalizer-venv/bin/python"
```

可將絕對路徑存入正式版 private-config.json 的 `normalizer_python` 與 `semantic_command_config`，由既有 local launcher 注入上述環境變數；私人設定不提交 Git，執行器／模型選擇仍只在 private command config。

未設定 converter 時前端只宣告 PDF。部署前先完成 migration 0007 與 0008；不要在舊 schema 啟動新 worker。正式 DB 升級須依工作區資料政策，先有明確授權、可驗證備份與回復計畫。這裡的指令不是自動套用正式資料的授權。

Normalization policy 的 `version` 已升為 3（加入 olefile 0.47 的舊 Office 辨識），包含統一檔案大小設定；已完成的舊 normalization／SourceSet 不改寫，新操作採新 policy。執行期記憶體與逾時防護繼續保留，無法完成時回報失敗。

## 資料生命週期

1. `POST /v2/materials`：建立草稿，body 為 `material-draft-create/v1` + `display_name`，使用 Idempotency-Key。
2. `POST /v2/materials/{id}/sources`：raw bytes、實際 MIME、URL-encoded `X-Material-Name` 與獨立 Idempotency-Key。保存原檔 receipt 和 pending normalization，HTTP request 不執行轉檔。
3. worker 領取 source normalization，轉檔在 DB transaction 外；120 秒 lease、最大 60 秒子程序 wall time。意外中斷後 expired lease 可重領，最多 3 次；使用者可明確 POST retry。
4. ready 原子發布 normalized PDF／mapping。失敗保留原檔與固定錯誤代碼。改變 renderer policy 後重試會建立新 normalization record，不改寫舊 ready record。
5. `POST /v2/materials/{id}/revisions`：`material-revision-create/v1`、`base_revision: null`、單一 ready `normalization_ids`、獨立 Idempotency-Key。短交易內封存 SourceSet 與 run；單來源 canonical PDF 直接引用 normalized bytes，bundle manifest 另行 hash 綁定，不重複複製一份 PDF。
6. UI 用既有 run 頁顯示分析；開始分析是明確操作，GET／reload 不生成、不轉檔。新 run 回應 `material-processing-run/v6` 並帶 `input_source_set_id`；舊 run 仍為 v5。

本批單來源 assembly 為 identity mapping，因此在建立 run 的交易中完成並綁定，不新增假的非同步 assembly 階段。bundle manifest 保存在 run JSON + hash；mapping bytes 在 private artifact store。所有原檔／normalized／mapping 使用既有 owner-scoped store。

## 來源與舊資料

Migration 0008 只擴充原檔 MIME，新增 application/msword／application/vnd.ms-powerpoint，不改既有資料。

Migration 0007 增加來源、normalization、SourceSet／items、run bundle binding 與 nullable draft/head 欄位；不改 0001–0006 SQL。舊 PDF backfill 與後續 v1 PDF upload 都使用 identity normalization，沒有重轉／OCR／重算舊 KS／重評答案。

新 KS 使用 `knowledge-structure/v3` envelope，content revision 包含 input binding；舊 v2 reader 保留原嚴格驗證。read 再核對 SourceSet membership、三種 artifact SHA、bundle page map 與 runtime 身分；不接受 client 任意 URL 或路徑。Material 庫同時識別 legacy item/v2 與 source item/v3，草稿／失敗轉檔也能找回及刪除。

`GET /v2/materials/{id}/knowledge-structures/{revision}/evidence/{id}/source` 以 exact KS/Evidence 回查 normalized page 與相交的來源 block：

- PDF：原頁碼。
- PPTX：原投影片編號；hidden slides 和 notes 不匯出，原编号不重編。
- DOC／PPT：先確認舊二進位 Office 容器與類型，轉成 PDF；原生 locator 標示 unavailable，提供轉換後頁碼與原檔下載，不偽造原頁碼／投影片編號。
- DOCX：轉換後頁碼；paragraph anchors 可 exact／ambiguous／unavailable，不冒充作者 Word 分頁。
- TXT／MD：原始行／block 範圍，視覺換行不改 source line。

Map、Relation、Study、Assessment 共用來源按鈕，分開「開啟 PDF 來源頁」和「下載原檔」。非 PDF 原檔以 attachment、正確 MIME、nosniff 下載；不直接渲染任意 HTML。原生定位缺失時仍回查轉換後 PDF，不偽造位置。

完整刪除先等待／取消活動工作，再移除所有 owned artifacts、SourceSet 與學習資料。新 artifact 發布留下 pending marker；commit 結果未知時依 DB reference 決定保留或清理。quarantine rollback 使用原 reconciliation，不刪其他使用者資料。

## 隔離與限制

所有格式的解析（含 Story）在無網路 bubblewrap 中執行；只掛必要唯讀系統／Python／renderer／input 路徑，output 可寫，profile/temp 在獨立 tmpfs。子程序 CPU 45 秒、address space 2 GiB、單檔 100 MiB、128 descriptors、wall time 60 秒；NPROC 1024 為每 UID 限制，不是 cgroup 的每 job aggregate memory/process quota。

舊 DOC／PPT 使用 olefile 0.47 辨識 OLE 容器和 Word／PowerPoint stream，拒絕可辨識的巨集、主動物件及加密容器；不支援或受密碼保護的內容會如實失敗。LibreOffice 使用最高巨集安全層級的獨立 profile。

OOXML 保留檔案類型與 ZIP 路徑檢查，拒絕加密、宏與外部 relationships；不按頁數、ZIP 項目數、解壓總量或壓縮比拒絕檔案。Markdown raw HTML 不執行、圖片不載入、連結以文字呈現。TXT 長行按 72 顯示欄位有界換行；Markdown 複雜排版與長 code 仍屬盡力轉換。100 MiB 是產品大小上限，不保證所有上限內文件都能轉換成功。不能以此宣稱解析所有 Office 文件，或以 exit 0 代替有效 PDF／hash 檢查。

## 開發替代模型

語意邊界支援明確啟用的 command transport；產品 source 沒有寫死執行器或替代模型名稱。正式 runtime-lock.json 不修改。

`STUDYDY_SEMANTIC_COMMAND_CONFIG` 指向 private JSON，欄位如下：

```json
{
  "schema": "semantic-command-config/v1",
  "argv": ["/absolute/executable", "{model}", "{schema}", "{output}", "{workdir}"],
  "model_id": "configured-model",
  "model_revision": "declared-version-or-unversioned-alias",
  "timeout_seconds": 180,
  "max_input_bytes": 60000,
  "max_calls": 12
}
```

argv 由 subprocess argument array 執行，不經 shell。stdin 是 instructions／input／response_schema JSON；執行器將 final JSON 寫至 `{output}`。本次授權採 Codex CLI 的 luna，具體命令留在本機 private config；CLI 在本機執行不表示模型離線。本次模型測試只送出合成教材。

子程序不繼承 `STUDYDY_*`（含產品 DSN／store 路徑）及 VLLM credential。超時、失敗、超出每 server process 的 max_calls、無效 JSON 都如實失敗，不切換其他模型或固定答案。API 仍經既有 grounded／答案安全驗證。command binding/v2、KS execution_identity、Assessment provenance/v7 保存實際設定的 model identity／config hash；舊 Gemma bindings／provenance 保持嚴格 reader。

若供應商只提供 alias，`model_revision` 明示 unversioned，而不假冒 immutable weight revision。替代模型結果只能作開發流程證據；Gemma 品質驗收仍另列。

## 驗證與部署邊界

- 後端標準測試使用 disposable PostgreSQL。converter 測試需上方獨立環境。
- `test_source_normalization.py` 覆蓋實際轉檔、owner／origin、replay、lease recovery、policy version、snapshot、來源回查、legacy／完整刪除與 command provenance 保存。
- 瀏覽器 fixture 可用 `STUDYDY_E2E_FRONTEND_PORT=4183`、`STUDYDY_E2E_API_PORT=8002`，不必停止使用者 4173／8001 服務。production preview 驗證真實建置；fixture transport 不啟動模型。
- migration rollback 不刪新資料；寫入 sources-v2 後要保留新 reader，採 forward repair 或經授權的 DB backup restore。不要直接切回舊 binary 期待它能讀所有新格式。

本批不包含 B3 多來源／append、B4 地圖改版或 B5 題組／掌握度變更。
