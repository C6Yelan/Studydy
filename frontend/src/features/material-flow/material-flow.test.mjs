import assert from "node:assert/strict";
import test from "node:test";

import { materialProgressStageLabel, materialRunHasUsableMap, validatePdfFile } from "./material-flow.ts";

test("final material stages and usable binding are direct", () => {
  assert.equal(materialProgressStageLabel("evidence"), "整理頁面與教材來源");
  assert.equal(materialProgressStageLabel("semantics"), "建立概念、關係與學習順序");
  assert.equal(materialRunHasUsableMap({ output_binding: { decision: "retain" } }), true);
  assert.equal(materialRunHasUsableMap({ output_binding: null }), false);
});

test("upload remains PDF-only and bounded", () => {
  assert.equal(validatePdfFile({ type: "application/pdf", size: 12 }), null);
  assert.match(validatePdfFile({ type: "text/plain", size: 12 }), /PDF/);
});
