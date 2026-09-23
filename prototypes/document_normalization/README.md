# B2-P：文件轉檔可行性實驗

本目錄保存 2026-09-17 的獨立 prototype 與合成教材實驗，不是 current production 實作。
下文「未整合／未執行 B2-I」描述當時階段；目前整合與限制見 [文件正規化](../../docs/document-normalization.md) 及 [來源版本](../../docs/source-revisions.md)。
保留原實驗結果、限制與隔離依據；不可直接把 probe 當成對外上傳服務。

## 本次結論（2026-09-17）

五種格式均可產生可開啟的 PDF，適合在「PDF 優先、其他格式自動轉 PDF、轉換品質不保證」政策下繼續評估整合。這是小型合成樣本結果，不是任意文件的成功率或品質保證。

| 格式 | 採用建議 | 實跑結果 | 已知限制 |
|---|---|---|---|
| PDF | 驗證後 pass-through | 1 頁，原始 bytes／SHA 完全相同，保留旋轉 | 加密／损壞檔拒絕 |
| DOCX | LibreOffice Writer headless | 3 頁，中文、程式碼、65 列跨頁表格、最後一段可抽出；已查看前兩頁 | 原生定位使用保守文字比對：44 exact／91 ambiguous；不代表原 Word 分頁或所有 anchor 精確 |
| PPTX | LibreOffice Impress headless | 3 張原投影片轉為 2 頁，正確對回原第 1、3 張；hidden slide／notes 均未混入 | 圖表、複雜公式、動畫與作者端版面未完整驗證；字型可能替換 |
| TXT | escaped HTML → PyMuPDF Story | 3 頁，93 個原始行號有對應；中文、tab 展開、CRLF、空行可處理 | 極長無空白行在此 CSS 策略下會被裁切，不能把 anchor exact 解讀為全文無損 |
| Markdown | markdown-it → 限制 HTML → Story | 1 頁，標題、列表、表格、程式碼與 25 個 block 位置；raw HTML 以文字呈現 | 圖片不載入、連結只保留文字；非法 local-file image 語法可能原樣顯示；不是完整 Markdown／HTML 支援 |

DOCX 的 44／91 是此合成文件的 anchor matching 計數，不是教材準確率。TXT／MD 的 exact 表示原始行／block 與排版 element 的對應，不保證 element 中的文字全部可見。

## 實際環境

- LibreOffice 26.2.5.2 `620(Build:2)`。
- 初始環境只有 nogui core／Impress，缺 Writer。使用者已補裝 `libreoffice-writer-nogui` `4:26.2.5.2-0ubuntu0.26.04.1`。
- PyMuPDF 1.28.0，使用既有 backend/.venv，未更動共用環境。
- 系統 markdown-it-py 3.0.0；prototype 明確讀系統 package path，正式依賴規劃留待 B2-I，不應直接複製此 sys.path 作法進產品。
- 系統已有 Noto CJK；manifest 記錄 font inventory 與 PDF 實際嵌入字型。Story 使用 MuPDF 內建 CJK fallback。
- LibreOffice export options 明確關閉 hidden slides、notes、notes pages、only notes pages、form fields。參考 [官方 PDF export options](https://help.libreoffice.org/latest/en-US/text/shared/guide/pdf_params.html)。
- TXT／MD 利用 Story element positions 記錄頁面及 region；參考 [官方 Story API](https://pymupdf.readthedocs.io/en/latest/story-class.html)。實際本機版本與輸出已測，不依賴 latest 文件的版本假設。

## 驗證證據

本機 ignored 目錄：`.studydy-runtime/b2-p-20260917-run7/`。

- `report.json`：工具／字型／policy hash、格式矩陣、9 個拒絕或逾時結果、11 項 behavioral checks。
- `*-manifest.json`：原檔 SHA、normalized PDF SHA、頁數、page map、原生 locator／accuracy、字型、抽出文字。
- `sources/`：全部自行生成的合成原檔；沒有私人教材。
- `rendered/`：實際 PDF、TXT／MD 的限制 HTML、預覽圖；已實際開圖檢查 DOCX 前兩頁、PPTX 第一頁、TXT 第一頁、Markdown 第一頁。
- `repeat-check.json`：相同原檔再次轉檔，DOCX／PPTX／TXT／MD 頁數及抽出文字相同。Office PDF bytes 不同，TXT／MD 此樣本相同；不要求跨次 byte-identical。
- `run.log`：本次執行結果。

9 個負例：損壞 PDF、加密 PDF、非 UTF-8 TXT、非 UTF-8 MD、DOCX 偽裝 PPTX、ZIP traversal、高壓縮比 package、Office 外部 relationship、子程序逾時。各項均拒絕／終止，沒有把失敗當成功。

11 項 checks：PDF bytes、PPTX 原投影片編號、hidden 排除、notes 排除、可見文字、TXT 原始行號覆蓋、Markdown raw HTML 文字化、外部圖片不呈現，以及 mapping unique／repeated／absent 三種結果。mapping 反例的單元檢查和實際 DOCX 轉檔結果分列。

## 隔離與適用界線

LibreOffice 使用獨立 bubblewrap network／PID 等 namespaces，只見必要唯讀系統路徑與合成輸入；僅專屬 output 可寫，profile 與 temp 位於隔離 tmpfs。已驗證無法連線外部 IP，且看不到 host `/home/jerry`。

子程序限制：CPU 30 秒、address space 2 GiB、單檔 64 MiB、128 descriptors、wall timeout 60 秒；逾時終止 process group。RLIMIT_NPROC=1024 是每 UID 限制，**不是每 job 的 cgroup quota**；128 會令本機 namespace 建立失敗。這些是 prototype 限額，不是已決定的產品容量。

Story 在 probe process 內執行；輸入 HTML 由 escape／停用 raw HTML 的 parser 產生，移除圖片與可點連結。**未完成 Story OS sandbox 或每 job cgroup 限額驗證**。本結果只支持可信合成 fixture 的 prototype；對外上傳前仍要完成該執行邊界，不因品質免保證而省略檔案安全。

OOXML 檢查是 prototype 所需的基本型別／路徑／容量／external relationship 防護，不宣稱已完成全部 malicious Office／encrypted OOXML／macro／embedded object 安全資格。沒有做租戶權限、HTTP MIME、DB recovery 或 delete/resume：那些屬 B2-I。

## 重跑

從正式版 root，使用一個不存在的新 output 目錄：

```bash
backend/.venv/bin/python prototypes/document_normalization/probe.py \
  --output .studydy-runtime/b2-p-new-run
```

需要現有 LibreOffice Writer／Impress、bubblewrap、fontconfig、PyMuPDF、系統 markdown-it；本機工具沙箱可能需要允許建立 namespaces。腳本不安裝工具、不啟動產品、不讀 DB 或原教材，不使用 OCR、Gemma、codex exec、luna 或 Pod。

B2-P 結束於本報告。建議後續 B2-I 採此轉檔路徑，保留 normalized PDF 回查與不保證轉換品質文案；TXT 長行採明示限制或有界換行策略，原生 anchor 不確定就明示，Office 輸出按实际 SHA 綁定。**尚未開始 B2-I，也未將任何格式開放到產品 UI。**
