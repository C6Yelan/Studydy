# 合成文件與轉檔探查工具

本目錄提供合成文件生成及獨立探查腳本。產品轉檔入口位於 [document_normalization](../../backend/src/document_normalization/)，使用方式見 [教材處理](../../docs/materials.md)，回歸方式見 [測試](../../docs/testing.md)。

| 工具 | 用途 |
| --- | --- |
| [probe.py](probe.py) | 生成合成 DOCX、PPTX、TXT、Markdown、PDF，執行獨立轉檔與內容探查 |
| [size_probe.py](size_probe.py) | 生成不同頁數／大小的合成文件，在工作目錄中探查 renderer 與資源行為 |

backend 測試使用的部分固定文件來自 probe.py 的生成函式；見 [fixture 說明](../../backend/tests/fixtures/README.md)。

執行工具前先閱讀腳本的依賴、limits 與輸出行為。它們有自己的探查設定，不能用其結果取代正式 converter 的政策、資料隔離測試或真實教材品質驗收。

輸出只放在新建且不受 Git 追蹤的工作目錄，不使用私人教材或產品資料：

~~~bash
backend/.venv/bin/python prototypes/document_normalization/probe.py \
  --output .studydy-runtime/normalization-probe
~~~

probe.py 使用 LibreOffice、bubblewrap、fontconfig、PyMuPDF 與 markdown-it。輸出目錄必須尚不存在；生成產物可重建，不加入 repo。
