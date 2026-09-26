# 合成文件 fixture

這些文件由專案自製文字生成，不含私人教材、帳號、憑證或模型產出。

| 檔案 | 覆蓋內容 |
| --- | --- |
| sample.docx | 中文、程式碼、重複段落、65 列表格與明確換頁 |
| sample.pptx | 三張投影片，其中一張 hidden，各頁含合成 speaker-notes marker |
| sample.doc／sample.ppt | 對應文件的二進位 Office 格式，用於格式辨識與轉換後頁碼回查 |

PPTX 與二進位 Office 檔透過 LibreOffice 匯出。

Fixture 用於內容保留、hidden／notes 排除與來源 mapping 的程式回歸，不代表所有 Office 文件或真實模型品質。執行方式見 [測試文件](../../../docs/testing.md)。
