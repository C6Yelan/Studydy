import assert from "node:assert/strict";
import test from "node:test";

import {
  formatFileSize,
  materialFailureMessage,
  materialRunLabel,
  validatePdfFile,
} from "./material-flow.ts";

test("PDF validation keeps formal media and size boundary", () => {
  assert.equal(validatePdfFile(null), "請先選擇 PDF 教材。");
  assert.equal(validatePdfFile({ type: "text/plain", size: 10 }), "只接受 application/pdf 格式的教材。");
  assert.equal(validatePdfFile({ type: "application/pdf", size: 0 }), "PDF 不可為空白檔案。");
  assert.equal(validatePdfFile({ type: "application/pdf", size: 10 }), null);
  assert.equal(formatFileSize(1024), "1 KiB");
});

test("v2 run labels and content failures remain truthful", () => {
  assert.equal(materialRunLabel("succeeded"), "處理完成，等待複核");
  assert.equal(materialRunLabel("partial"), "部分頁面已排除，等待複核");
  assert.equal(materialFailureMessage("NO_USABLE_EVIDENCE"), "教材沒有產生可安全回查的概念與依據。");
});
