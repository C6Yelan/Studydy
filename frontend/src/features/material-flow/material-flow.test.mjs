import assert from "node:assert/strict";
import test from "node:test";

import {
  formatFileSize,
  materialElapsedLabel,
  materialFailureMessage,
  materialProgressStageLabel,
  materialRunHasUsableMap,
  materialRunLabel,
  parseLatestMaterialRun,
  validatePdfFile,
  validatePdfSelection,
} from "./material-flow.ts";

test("PDF validation keeps formal media and size boundary", () => {
  assert.equal(validatePdfFile(null), "請先選擇 PDF 教材。");
  assert.match(validatePdfFile({ type: "text/plain", size: 10 }), /不是可用的 PDF/);
  assert.equal(validatePdfFile({ type: "application/pdf", size: 0 }), "PDF 不可為空白檔案。");
  assert.equal(validatePdfFile({ type: "application/pdf", size: 10 }), null);
  assert.equal(validatePdfFile({ type: "application/pdf", size: 100 * 1024 * 1024 + 1 }), "PDF 不可超過 100 MiB。");
  assert.equal(formatFileSize(1024), "1 KiB");
  assert.deepEqual(validatePdfSelection([
    { type: "application/pdf", size: 10 },
  ]), { file: { type: "application/pdf", size: 10 }, message: null });
  assert.equal(validatePdfSelection([
    { type: "application/pdf", size: 10 },
    { type: "application/pdf", size: 20 },
  ]).message, "一次只能處理一份 PDF 教材。");
  assert.equal(validatePdfSelection([
    { type: "application/pdf", size: 100 * 1024 * 1024 + 1 },
  ]).message, "PDF 不可超過 100 MiB。");
});

test("latest run pointer only accepts two canonical UUID fields", () => {
  const pointer = {
    materialId: "9f9619ff-8b86-4e3a-a2f1-2bb9424d5c72",
    runId: "bf9619ff-8b86-4e3a-a2f1-2bb9424d5c74",
  };
  assert.deepEqual(parseLatestMaterialRun(JSON.stringify(pointer)), pointer);
  assert.equal(parseLatestMaterialRun(JSON.stringify({ ...pointer, owner: "client" })), null);
  assert.equal(parseLatestMaterialRun(JSON.stringify({ ...pointer, runId: "../foreign" })), null);
  assert.equal(parseLatestMaterialRun("not-json"), null);
});

test("v3 run labels, stages and content failures remain truthful", () => {
  assert.equal(materialRunLabel("succeeded"), "處理完成，等待複核");
  assert.equal(materialRunLabel("partial"), "部分頁面已排除，等待複核");
  assert.equal(materialFailureMessage("NO_USABLE_EVIDENCE"), "教材沒有產生可安全回查的概念與依據。");
  assert.equal(materialProgressStageLabel("concept_generation"), "建立並檢查教材概念");
  assert.equal(materialProgressStageLabel("publishing"), "發布可複核結果");
  assert.equal(
    materialElapsedLabel("2026-08-28T00:00:00Z", Date.parse("2026-08-28T00:02:03Z")),
    "2 分 3 秒",
  );
});

test("terminal binding with no formal concept cannot open a Map", () => {
  const run = {
    status: "partial",
    output_binding: { reason_codes: ["NO_FORMAL_CONCEPT"] },
  };
  assert.equal(materialRunHasUsableMap(run), false);
  run.output_binding.reason_codes = ["KNOWLEDGE_MAP_REVIEW_REQUIRED"];
  assert.equal(materialRunHasUsableMap(run), true);
});
