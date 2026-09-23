# 單檔文件轉換（B2-I）

PDF 是主要教材格式。其他已開放格式上傳後自動轉成 PDF，轉換品質不保證；保留原檔、轉換後預覽及來源回查。首次多檔建立與既有教材追加見 [B3-A／B3-B](source-revisions.md)。以下逐檔轉檔政策沿用 B02。

## 環境與啟用

PDF、DOC／DOCX、PPT／PPTX、UTF-8 TXT／Markdown 的單檔上限統一為 100 MiB（104,857,600 bytes）；轉換後 PDF 也使用相同單檔上限。不另設頁數、段落數、ZIP 項目數、解壓總量或壓縮比使用門檻。需要 LibreOffice **26.2.5.2** Writer/Impress、bubblewrap、fontconfig、Noto CJK 與一個獨立的 Python 3.12 converter 環境。不要修改正式版與競賽版共用的 backend/.venv。
目前隔離命令依賴 Linux namespace、`/usr` 目錄布局、`/etc/fonts` 及 `/usr/lib/libreoffice/program/soffice`；換 Linux 主機也須核對這些實際路徑與轉檔測試，不能只複製虛擬環境。

```bash
uv venv --python backend/.venv/bin/python .studydy-runtime/normalizer-venv
uv pip install --python .studydy-runtime/normalizer-venv/bin/python -r backend/normalizer-requirements.txt
export STUDYDY_NORMALIZER_PYTHON="$PWD/.studydy-runtime/normalizer-venv/bin/python"
```

可將絕對路徑存入正式版 private-config.json 的 `normalizer_python` 與 `semantic_command_config`，由既有 local launcher 注入上述環境變數；私人設定不提交 Git，執行器／模型選擇仍只在 private command config。

未設定 converter 時初次上傳只宣告 PDF 來源格式。來源轉檔／追加須有本節的 normalizer 設定。現行來源與處理結構由 `0001_identity_and_materials.sql`／`0002_sources_and_processing.sql` 建立；不要在未完成 baseline 接軌的舊帳本上套用新 SQL。正式 DB 升級須依工作區資料政策，先有明確授權、可驗證備份與回復計畫。這裡的指令不是自動套用正式資料的授權。

現行 normalization policy 為 v1，記錄轉檔套件版本、中文字型清單雜湊及統一檔案大小設定。既有 ready 來源已在 v1 契約切換時完成 metadata 接軌；轉檔失敗會明確回報，不把失敗產物當作 ready。

## 資料生命週期

1. `POST /v1/materials`：建立草稿，body 為 `material-draft-create/v1` + `display_name`，使用 Idempotency-Key。
2. `POST /v1/materials/{id}/sources`：raw bytes、實際 MIME、URL-encoded `X-Material-Name` 與獨立 Idempotency-Key。保存原檔 receipt 和 pending normalization，HTTP request 不執行轉檔。
3. worker 領取 source normalization，轉檔在 DB transaction 外；120 秒 lease、最大 60 秒子程序 wall time。意外中斷後 expired lease 可重領，最多 3 次；使用者可明確 POST retry。
4. ready 原子發布 normalized PDF／mapping。失敗保留原檔與固定錯誤代碼；明確重試只重設同一筆 job 的失敗狀態，不建立舊版並行紀錄或改寫其政策。已 ready 的來源綁定保持不可變。
5. `POST /v1/materials/{id}/revisions`：初次使用 `material-revision-create/v1`、`base_revision: null`、一份或多份 ready `normalization_ids`、獨立 Idempotency-Key。短交易內封存有序 SourceSet 與 run。B3-A 起沿用各來源 normalized PDF，以集合閱讀序號映射來源頁碼，不另存合併 PDF。
6. UI 用既有 run 頁顯示分析；開始分析是明確操作，GET／reload 不生成、不轉檔。新 run 回應 `material-processing-run/v1` 並帶 `input_source_set_id`；reader 只接受 v1。

單來源頁碼映射為 identity；bundle manifest 保存在 run JSON + hash，mapping bytes 在 private artifact store。所有原檔／normalized／mapping 使用既有 owner-scoped store。B3-A 的多來源資料契約見其專頁。

## 來源與資料保存

`0001_identity_and_materials.sql` 保留完整原檔 MIME，包括 application/msword／application/vnd.ms-powerpoint。

`0002_sources_and_processing.sql` 建立來源、normalization、SourceSet／items、run bundle binding 與 head 外鍵。空白 DB 不重播歷史 PDF backfill；既有 pdf-v1 DB 接軌前須取得退役清理授權並保留原始 PDF。現行建立入口只有來源集合，不再提供 v1 PDF upload 或舊 PDF reader。
資料庫只描述來源集合模型，不再保存 `ingestion_kind` 標記或 `source_pdf` artifact 類型。
原檔使用 `original`，預覽／處理 PDF 使用 `normalized_pdf`，來源對照使用 `source_mapping`。
`source_artifact_id` 仍保存教材代表 PDF；草稿可為 NULL，轉檔完成後由現行流程填入。
`seed_pdf()` 測試 fixture 也走來源集合流程，保留使用。既有產品 DB 的清理與帳本接軌狀態見
[本地環境](local-environment.md#目前日常服務與資料契約)，其他尚未清理的舊 DB 不可直接套用本版 SQL。

正式 KS 使用唯一的 `knowledge-structure/v1`，詳見來源版本文件。content revision 包含 input binding，完成來源綁定後重新計算；不把舊 revision 改標籤沿用。read 再核對 SourceSet membership、三種 artifact SHA、bundle page map 與 runtime 身分；不接受 client 任意 URL 或路徑。草稿／失敗轉檔也能找回及刪除。

`GET /v1/materials/{id}/knowledge-structures/{revision}/evidence/{id}/source` 以 exact KS/Evidence 回查 normalized page 與相交的來源 block：

- PDF：原頁碼。
- PPTX：原投影片編號；hidden slides 和 notes 不匯出，原编号不重編。
- DOC／PPT：先確認舊二進位 Office 容器與類型，轉成 PDF；原生 locator 標示 unavailable，提供轉換後頁碼與原檔下載，不偽造原頁碼／投影片編號。
- DOCX：轉換後頁碼；paragraph anchors 可 exact／ambiguous／unavailable，不冒充作者 Word 分頁。
- TXT／MD：原始行／block 範圍，視覺換行不改 source line。

Map、Relation、Study、Assessment 共用來源按鈕，分開「開啟 PDF 來源頁」和「下載原檔」。非 PDF 原檔以 attachment、正確 MIME、nosniff 下載；不直接渲染任意 HTML。原生定位缺失時仍回查轉換後 PDF，不偽造位置。

完整刪除先等待／取消活動工作，再移除所有 owned artifacts、SourceSet 與學習資料。新 artifact 發布留下 pending marker；commit 結果未知時依 DB reference 決定保留或清理。quarantine rollback 使用原 reconciliation，不刪其他使用者資料。

## 隔離與限制

所有格式的解析（含 Story）在無網路 bubblewrap 中執行；只掛必要唯讀系統／Python／renderer／input 路徑，output 可寫，profile/temp 在獨立 tmpfs。整次轉檔（含 LibreOffice）共用 60 秒 wall timeout；子程序設有 address space 2 GiB、單檔 100 MiB、128 descriptors 上限。NPROC 1024 為每 UID 限制，不是 cgroup 的每 job aggregate memory/process quota。

舊 DOC／PPT 使用 olefile 0.47 辨識 OLE 容器和 Word／PowerPoint stream，拒絕可辨識的巨集、主動物件及加密容器；不支援或受密碼保護的內容會如實失敗。LibreOffice 使用最高巨集安全層級的獨立 profile。

OOXML 保留檔案類型與 ZIP 路徑檢查，拒絕加密、宏與外部 relationships；不按頁數、ZIP 項目數、解壓總量或壓縮比拒絕檔案。Markdown raw HTML 不執行、圖片不載入、連結以文字呈現。TXT 長行按 72 顯示欄位有界換行；Markdown 複雜排版與長 code 仍屬盡力轉換。100 MiB 是產品大小上限，不保證所有上限內文件都能轉換成功。不能以此宣稱解析所有 Office 文件，或以 exit 0 代替有效 PDF／hash 檢查。

## 開發替代模型

語意邊界支援明確啟用的 command transport；產品 source 與正式 runtime lock 不寫死執行器或替代模型名稱。來源／增量請求與 command 分批政策都納入現行 runtime lock v1，詳見 [多來源教材](source-revisions.md)。

`STUDYDY_SEMANTIC_COMMAND_CONFIG` 指向 private JSON，欄位如下：

```json
{
  "schema": "semantic-command-config/v1",
  "argv": ["/absolute/executable", "{model}", "{schema}", "{output}", "{workdir}"],
  "model_id": "configured-model",
  "model_revision": "declared-version-or-unversioned-alias"
}
```

argv 由 subprocess argument array 執行，不經 shell。stdin 是 instructions／input／response_schema JSON；執行器將 final JSON 寫至 `{output}`。開發設定採 Codex CLI 的 luna，具體命令留在本機 private config；CLI 在本機執行不表示模型離線。資料外送範圍依當次授權。

2026-09-19 依使用者要求，已移除私人執行器的 `timeout_seconds`、`max_input_bytes`、`max_calls` 欄位及檢查：不以 bytes 拒絕輸入、不限制每 server process 的呼叫次數、不設定子程序逾時。command 回應也不再另設 1 MiB 大小上限。模型服務本身仍可能回報上下文或其他執行錯誤，必須如實失敗。明確有預算的獨立實驗由實驗腳本管理；不將實驗限制放入日常使用設定。

取消上述限制不取消分批。command 使用同一連續 Evidence 分批器與累積概念流程，以本機估算新增內容來選擇批次；Gemma 仍用原服務端 tokenizer。估算只安排批次，不是限制可處理的教材總量，也不裁切或丟棄原文。

子程序不繼承 `STUDYDY_*`（含產品 DSN／store 路徑）及 VLLM credential。API 仍經既有 grounded／答案安全驗證，不切換其他模型或固定答案。command binding/v1、KS execution_identity、Assessment provenance/v1 保存實際設定的 model identity／config hash；已保存的 bindings／provenance 保持嚴格 reader。

開發執行器不再使用會自動清除輸出的 `TemporaryDirectory`。執行 cwd 仍是與專案隔離的 0700 `studydy-semantic-*` 目錄，不額外引入專案上下文；執行目錄不自動刪除，且輸入、schema、原始回應、stdout／stderr 及 exit code 另存至私人保存目錄，包括 JSON 無效或子程序失敗。教材分析由 caller 提供 owner／Material／run 範圍的保存位置；獨立 command 呼叫在私人設定檔同層保存。教材目錄記錄 cwd 與 nonce，只有明確刪除教材時才核對並一起清理。這些是私人診斷產物，不公開或提交。教材分批接續規則見 [多來源教材](source-revisions.md#分批保存與失敗重試)。

若供應商只提供 alias，`model_revision` 明示 unversioned，而不假冒 immutable weight revision。替代模型結果只能作開發流程證據；Gemma 品質驗收仍另列。

## 驗證與部署邊界

- 後端標準測試使用 disposable PostgreSQL。converter 測試需上方獨立環境。
- `test_source_normalization.py` 覆蓋實際轉檔、owner／origin、replay、lease recovery、policy version、snapshot、來源回查、legacy／完整刪除與 command provenance 保存。
- 瀏覽器 fixture 可用 `STUDYDY_E2E_FRONTEND_PORT=4183`、`STUDYDY_E2E_API_PORT=8002`，不必停止使用者 4173／8001 服務。production preview 驗證真實建置；fixture transport 不啟動模型。
- migration rollback 不刪新資料；寫入現行來源集合後要保留對應 reader，採 forward repair 或經授權的 DB backup restore。不要直接切回舊 binary 期待它能讀所有新格式。

本頁說明 B02 轉檔基礎；B3-A 追加見獨立文件，B4 地圖改版與 B5 題組仍不在本批範圍。
