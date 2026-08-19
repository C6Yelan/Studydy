# 精簡修正清單

只記錄尚未完成的修改。每個檔案獨立列出具體動作、必須保留的行為與最小驗證。

狀態：`待分析`、`待批准`、`已批准`、`進行中`、`已完成`、`不採用`。

隔夜批次只執行 `已批准` 項目；遇到 contract 變更、新 dependency／Provider、Evidence 斷鏈、假成功或 scope drift 時停止該項目。Commit、merge、push 不包含在批次內。

## `backend/tests/test_concept_generation.py`

狀態：`待批准`

- 將只由本測試檔使用的 `_request()`、`_output()` 改成檔內直接建立的小型 dict，不再跨到 `local_ai/tests/fixtures` 讀取 Concept 測試資料。
- malformed model output 從 8 cases 減為 4，只留 Markdown fence、截斷 JSON、duplicate key、NaN。
- 刪除 `test_semantic_request_fields_and_evidence_references_remain_exact`：request 重驗與既有 unknown Evidence 測試重複。
- 將 `test_model_status_or_locator_fields_are_not_trusted` 改名為只描述實際測到的 backend status 防護，不新增 locator case。
- 保留 request identity／locator、Evidence grounding、同頁 partial candidate、backend-owned status、Unicode normalization、空值、正規化後超長與控制字元測試。
- 預期由 8 functions／17 cases 降為 7 functions／約 12 cases。
- 驗證：`test_concept_generation.py`、`test_concept_api.py`、`test_text_first_run.py`；production code 不得產生 diff。

## `backend/tests/test_local_ai_process.py`

狀態：`待批准`

- 將 `test_bounded_ndjson_pipe_and_clean_exit` 改名為實際行為：NDJSON round trip 與正常關閉；正常路徑只呼叫一次 `close()`，idempotent cleanup 留給 failure tests 驗證。
- 保留大型 blocked pipe timeout 測試，因為它確認 timeout 同時涵蓋 request write 與 response read，並能終止 child。
- 保留 nonzero child exit 與第二次 `close()`，確認失敗後 cleanup 不會再次拋錯。
- 最後一個測試的 request-limit 子案例改用不會立即退出的 child，並精確驗證 `PROTOCOL_LIMIT_EXCEEDED`；stderr 子案例保留，確認 child diagnostics 不會進入 response 或改成假失敗。
- 不新增 malformed response、locator 或其他 defensive cases；維持 4 test functions。
- 驗證：`test_local_ai_process.py`、`test_text_first_run.py` 的 OCR child failure case；production code 不得產生 diff。

## `backend/tests/test_ocr_page_evidence.py`

狀態：`待批准`

- 保留 200-DPI RGB render、1-based page、旋轉頁 bbox、Evidence identity／locator 與 257 個 image metadata 不截斷的 regression。
- `test_all_unusable_blocks_fail_without_page_artifact` 刪除「空文字加 image-only」組合 case；空文字與 image-only 已在前面 partial-page 測試覆蓋，這裡只留空文字及零寬 bbox 兩個不同原因。
- `test_blank_and_image_only_blocks_are_rejected_but_text_page_remains` 改名時一併說明 malformed image metadata 會被排除，避免名稱漏掉實際 assertion。
- 保留 negative bbox 只排除該 block、全部無 usable Evidence 才整頁失敗，以及 page identity、extra field、null text 必須 hard fail 的差異。
- 預期維持 6 test functions，實際 cases 由 8 降為 7；不新增其他 bbox／OCR type 組合。
- 驗證：`test_ocr_page_evidence.py`、`test_text_first_run.py` 的 page Evidence／cache cases；production code 不得產生 diff。

## `backend/tests/test_text_first_run.py`

狀態：`待批准`

- 保留目前 19 個流程案例；它們分別覆蓋 full-PDF success／replay、retry exhaustion、OCR failure、兩種 cache recovery、no Evidence、malformed child、runtime／source preflight、partial page、無頁數截斷、Concept server cleanup 與三種 lock 行為，沒有整組重複。
- 移除測試名稱中的 `formal`、`dispatched` 等流程式用詞，改成直接描述 whole-document、server cleanup、busy lock、worker lock ownership 與 caller page subset 行為。
- 在 runtime drift、external API、invalid media type 三個測試之間補正常空行，不合併成難讀的 parameterized mutation。
- whole-document partial 測試移除 `source_documents` 與同一 `pymupdf.Document` object identity assertion；是否重用同一 Python object 是實作細節，不是產品 contract。
- success／replay 測試保留「無 PNG、無 raw model text、模型已釋放」的主要 assertion；partial 與 long-PDF 測試移除重複的 resident／PNG assertion。
- partial 測試仍保留逐頁移除 `png_bytes`／`native_evidence`、不建立 native artifact、JSON 不保存 `raw_text`；long-PDF 測試仍保留全頁數、頁序、locator、concurrency、單次 model load 與 server close。
- 不移動 source snapshot 測試到新檔案，避免只為分類增加測試模組；不刪除 Evidence、truthful failure、raw retention 或 lock safety cases。
- 預期維持 19 test functions／cases，但刪除重複 assertion 與 implementation coupling。
- 驗證：`test_text_first_run.py`、`test_ocr_page_evidence.py`、`test_concept_generation.py`、`test_local_ai_process.py`、`runtime/test_workers.py`；production code 不得產生 diff。

## `backend/src/pdf_evidence/concept_generation.py`

狀態：`待分析`

- 確認所有 production caller 傳入 `validate_concepts()` 的 request 都來自 `build_semantic_request()`；若成立，移除 `validate_concepts()` 對同一 request 的第二次完整 `validate_semantic_request()`。
- `build_semantic_request()` 保留 request schema、identity、Evidence、locator 與文字驗證，作為唯一 request validation owner。
- `validate_concepts()` 仍從已驗證 request 取得 Evidence allowlist，並保留 model JSON、candidate shape、unknown／duplicate Evidence、文字與 concept count 驗證。
- 若找到外部 caller、persisted request 或跨 process 可修改邊界，停止修改並標為 `不採用`。
- 驗證：Concept generation、Concept API、Text-first runtime focused tests；不得改 prompt、model、schema 或 reason contract。

## `local_ai/runtime-lock.json`

狀態：`待批准`

- 已確認 `semantic_request.json` 與 `semantic_model_output.json` 只由 `backend/tests/test_concept_generation.py` 使用，沒有 qualification、replay、部署或 runtime consumer。
- 從 `semantic.fixture_hashes` 移除這兩個單元測試 fixture SHA，保留空 object；其他 OCR、model、prompt、schema、resource 與 lifecycle binding 不變。
- 若執行 `ocr_process.py` 或 `protocol.py` 精簡，同步更新 `ocr.code_hashes.local_ai_ocr`／`local_ai_protocol`；不改 OCR model、prompt 或 inference 參數。
- 驗證：重新計算 canonical runtime-lock SHA，執行 runtime binding 與 Text-first focused tests。

## `local_ai/tests/fixtures/semantic_request.json`

狀態：`待批准`

- 刪除檔案；它是 backend Concept 單元測試輸入，不是 local AI runtime、OCR、qualification 或 replay fixture。
- 等價的小型 request dict 留在 `backend/tests/test_concept_generation.py`，不建立新的 fixtures 目錄或搬移檔案。
- 驗證：`backend/tests/test_concept_generation.py` 與 runtime-lock binding focused tests。

## `local_ai/tests/fixtures/semantic_model_output.json`

狀態：`待批准`

- 刪除檔案；它只提供一個 Concept candidate 給 backend 單元測試，沒有獨立 artifact 或 Golden 價值。
- 等價的小型 output dict 留在 `backend/tests/test_concept_generation.py`，每次呼叫建立新 object 供 mutation cases 使用。
- 驗證：`backend/tests/test_concept_generation.py` 與 runtime-lock binding focused tests。

## `local_ai/src/studydy_local_ai/__init__.py`

狀態：`待批准`

- 保留空的 `__init__.py`，讓 `studydy_local_ai` 明確維持一般 Python package，不改成 implicit namespace package。
- 刪除檔頭 docstring 與未被任何程式讀取的 `__version__`；套件版本仍由 `local_ai/pyproject.toml` 產生的 distribution metadata 提供。
- 同步更新 `backend/src/runtime/material_processing.py` 中 `_LOCAL_AI_SOURCE_HASHES["__init__.py"]`，不改 package version 或其他 runtime binding。
- 驗證：`local_ai/tests`、`test_local_ai_process.py` 與 runtime preflight hash tests。

## `local_ai/src/studydy_local_ai/ocr_process.py`

狀態：`待分析`

- 刪除檔頭 docstring；保留 function docstring，只說明非顯然的 offline model、memory-only inference 與 fail-closed 邊界。
- 刪除 `main()` 中重複的 `os.environ.update()` 與不再使用的 `os` import；唯一 production launcher `LocalAIProcess` 已用封閉 environment 設定相同四個 Hugging Face／Transformers offline 變數。
- 保留 `AutoModel`／`AutoTokenizer`：Unlimited-OCR 使用 reviewed custom model code 與 `infer()`，不是 Qwen 的 OpenAI-compatible text API。
- 保留 `trust_remote_code=True`，但仍由 offline model root、reviewed code SHA 與 runtime preflight 約束。
- 確認 backend 是 OCR 內容上限的唯一 contract owner；若成立，刪除 child 程序內重複的 `MAX_BLOCKS`、`MAX_BLOCK_TEXT`、`MAX_PAGE_TEXT` 與對應拒絕邏輯，由 4 MB response 上限維持 transport 資源邊界。
- 保留 sequential NDJSON serve、PNG hash、strict det-block parser、memory-only PIL patch、固定 reason exit 與 CUDA cleanup。
- `max_length=32768` 是 Unlimited-OCR 的模型產生參數，不與一般 bytes／pixels 限制一起刪除；若要調整，另以代表性 PDF 量測品質、VRAM 與 latency。
- 同步更新 `local_ai/runtime-lock.json` 的 OCR code hash 與 `backend/src/runtime/material_processing.py` 的 local AI source hash；除取消已確認重複的內容上限外，不改模型設定、det grammar 或 failure reporting。
- 驗證：`local_ai/tests/test_ocr_process.py`、`local_ai/tests/test_protocol.py`、`backend/tests/test_local_ai_process.py`、Text-first OCR focused tests及 runtime preflight。

## `local_ai/src/studydy_local_ai/protocol.py`

狀態：`待分析`

- 刪除檔頭 docstring；保留 `ProtocolError` 對固定 reason code、不攜帶 request／模型內容的安全說明。
- 將只為 side effect 使用的 `_depth()` 改為 `_check_depth()`：逐層檢查上限 32，不再建立 generator、計算並回傳沒有人使用的最大深度。
- `MAX_BLOCKS`、`MAX_BLOCK_TEXT`、`MAX_PAGE_TEXT` 不屬於 protocol framing；若 backend 已維持必要的內容 contract，從此模組移除這三個常數，避免 transport layer 重複定義產品上限。
- 保留 bounded NDJSON、duplicate key／NaN／深度、closed OCR request、request ID、render dimensions、base64、PNG signature 與 request／response byte limits。`96 MB` request 可容納 `64 MB` PNG 經 base64 膨脹後的大小，這組限制不視為無意義重複。
- parent 與 child 的大小限制雖數值重複，但位於不同 Python process／trust boundary，不建立跨環境共用 dependency。
- 同步更新 `local_ai/runtime-lock.json` 的 protocol code hash與 `backend/src/runtime/material_processing.py` 的 local AI source hash；不改 schema 或必要的 transport／resource 限制。
- 驗證：`local_ai/tests/test_protocol.py`、`local_ai/tests/test_ocr_process.py`、`backend/tests/test_local_ai_process.py` 及 runtime preflight。

## `backend/src/pdf_evidence/ocr_page_evidence.py`

狀態：`待分析`

- 刪除 OCR blocks 固定不得超過 64 的數量限制，只要求為非空 list；複雜排版不應因 block 數量較多就整頁失敗。
- 保留每 block 8,000 字元、每頁 100,000 字元與 4 MB artifact 作為 backend 內容／儲存邊界；這些精確數值目前沒有量測依據，後續只依代表性 PDF 分布調整，不為追求無上限而直接刪除。
- 保留 50,000,000 pixels、32,768 單邊、64 MB PNG 上限；它們限制解碼與記憶體使用，不是頁數或圖片數量截斷。
- 同步修正只驗證 64-block 上限的 tests，不新增窮舉數量測試。
- 驗證：`backend/tests/test_ocr_page_evidence.py`、`local_ai/tests/test_ocr_process.py` 與 Text-first OCR focused tests；不改 Evidence identity、locator、failure status 或 raw retention 行為。

## `local_ai/tests/test_ocr_process.py`

狀態：`待批准`

- 將 `test_exact_p02_det_grammar_preserves_type_text_and_bbox` 移除階段代號，改成直接描述 strict det grammar。
- invalid det cases 從 8 個減為 6 個：刪除舊 `<|ref|>` grammar case 與一個重複的 bbox 上下界 case；保留無 blocks、marker 外文字、bbox shape、bbox ordering、bbox range 與缺少 closing marker。
- inference 測試改名為 locked Unlimited-OCR arguments；回傳文字改為 exact assertion，刪除已被 call arguments 覆蓋的獨立 `OCR_PROMPT` assertion。
- 保留 valid blocks、fail-closed grammar 與 exact Unlimited-OCR inference arguments；不新增 restore／exception 或其他 synthetic parser cases。
- 驗證：`local_ai/tests/test_ocr_process.py`；production behavior 不得因測試改名而變更。

## `local_ai/tests/test_protocol.py`

狀態：`待批准`

- 刪除未使用的 `json` import。
- 保留 duplicate key、NaN 與過深 JSON 三種不同的 untrusted NDJSON failure；維持同一 test function，不為 reason code 拆成更多 cases。
- 將 `test_ocr_request_decodes_only_bound_png` 改名為實際驗證的「解碼 PNG 並拒絕額外欄位」；SHA 內容綁定由 `ocr_process.serve()` 負責，不在名稱中誤稱此函式已驗證。
- 維持 2 test functions／4 cases；不新增所有 dimension、base64、schema 欄位排列組合。
- 驗證：`local_ai/tests/test_protocol.py` 與 `local_ai/tests/test_ocr_process.py`；production code 不得產生 diff。

## `backend/src/pdf_evidence/concept_evidence_output.py`

狀態：`待分析`，依賴 `local_ai/runtime-lock.json` 的內容是否改變

- 只有在 fixture binding 或 OCR code hash 改變後，更新 `RUNTIME_LOCK_SHA256` 為新 runtime lock 的 canonical SHA。
- 不修改 output schema、aggregation、artifact size、Evidence validation 或其他常數。
- 驗證：Concept evidence output 與 Text-first runtime-lock mismatch tests。

## `backend/src/runtime/material_processing.py`

狀態：`待分析`，依賴 local AI source 或 `local_ai/runtime-lock.json` 是否改變

- `__init__.py`、`ocr_process.py` 或 `protocol.py` 改變時，只更新 `_LOCAL_AI_SOURCE_HASHES` 的對應 SHA。
- runtime-lock 內容改變時，只更新 `_LOCKED_FILES` 中 `local_ai/runtime-lock.json` 的檔案 SHA。
- 不修改 worker lifecycle、runtime file plan、資料庫狀態或 API 行為。
- 驗證：`test_runtime_binding_contains_exact_code_and_no_private_paths`、`test_formal_runtime_preflight_hashes_actual_files_and_detects_drift` 與直接相關 runtime tests。
