import assert from "node:assert/strict";
import test from "node:test";

import {
  formatFileSize,
  materialFailureMessage,
  materialRunLabel,
  materialRunRequestTimeoutMs,
  maximumPdfBytes,
  readRunSubject,
  saveRunSubject,
  validatePdfFile,
} from "./material-flow.ts";

const runId = "bf9619ff-8b86-4e3a-a2f1-2bb9424d5c74";

class MemoryStorage {
  #entries = new Map();

  get length() { return this.#entries.size; }
  clear() { this.#entries.clear(); }
  getItem(key) { return this.#entries.get(key) ?? null; }
  key(index) { return [...this.#entries.keys()][index] ?? null; }
  removeItem(key) { this.#entries.delete(key); }
  setItem(key, value) { this.#entries.set(key, String(value)); }
}

test("PDF validation 明確拒絕格式、空檔與超過 100 MiB", () => {
  assert.equal(validatePdfFile(null), "請先選擇 PDF 教材。");
  assert.equal(validatePdfFile({ type: "text/plain", size: 8 }), "只接受 application/pdf 格式的教材。");
  assert.equal(validatePdfFile({ type: "application/pdf", size: 0 }), "PDF 不可為空白檔案。");
  assert.equal(
    validatePdfFile({ type: "application/pdf", size: maximumPdfBytes + 1 }),
    "PDF 不可超過 100 MiB。",
  );
  assert.equal(validatePdfFile({ type: "application/pdf", size: maximumPdfBytes }), null);
});

test("檔案大小使用真實 bytes 顯示", () => {
  assert.equal(formatFileSize(8), "1 KiB");
  assert.equal(formatFileSize(15.2 * 1024 * 1024), "15.2 MiB");
});

test("refresh recovery 只保存 run 對應科目並 fail closed", () => {
  const storage = new MemoryStorage();
  assert.equal(saveRunSubject(storage, runId, "economics"), true);
  assert.equal(readRunSubject(storage, runId), "economics");
  assert.equal(saveRunSubject(storage, "not-a-run", "economics"), false);
  storage.setItem(`studydy.material-run.${runId}`, JSON.stringify({ schema: "material-run-recovery/v1", subject: "legacy_subject" }));
  assert.equal(readRunSubject(storage, runId), null);
});

test("UI 狀態與 failure 原因直接對應 API contract", () => {
  assert.equal(materialRunLabel("pending"), "等待開始處理");
  assert.equal(materialRunLabel("running"), "正在分析教材");
  assert.equal(materialRunLabel("partial"), "部分完成，需要複核");
  assert.equal(materialFailureMessage("RESTART_INTERRUPTED"), "服務重新啟動時中斷了這次處理。");
  assert.equal(materialFailureMessage("MATERIAL_OUTPUT_FAILED"), "教材輸出未能安全完成。");
  assert.equal(materialRunRequestTimeoutMs, 10_000);
});
