import assert from "node:assert/strict";
import test from "node:test";

import {
  materialProgressStageLabel,
  materialCurrentStagePercent,
  materialOverallProgressPercent,
  materialFailureMessage,
} from "./material-flow.ts";

test("review failures explain the actual cause instead of a generic failure", () => {
  assert.match(materialFailureMessage('REVIEW_METADATA_REASON_INVALID'), /非教學內容.*分類理由/);
  assert.match(materialFailureMessage('REVIEW_ALIAS_EDIT_INVALID'), /別名.*大小寫/);
  assert.match(materialFailureMessage('REVIEW_GROUPING_CYCLE'), /循環/);
  assert.match(materialFailureMessage('REVIEW_RESPONSE_INVALID'), /回應格式/);
  assert.match(materialFailureMessage('SEMANTIC_INPUT_TOO_LARGE'), /目前批次.*單次模型容量.*分批方式/);
});

test("material stages describe the current processing work", () => {
  assert.equal(materialProgressStageLabel("evidence"), "整理頁面與教材來源");
  assert.equal(materialProgressStageLabel("semantics"), "建立概念、關係與學習順序");
});

function processing(stage, completed, total = 45, status = "running") {
  return { progress_stage: stage, completed_pages: completed, total_pages: total, status };
}

test("only published success has overall percent; stage counts use their own real units", () => {
  for (const stage of ["queued", "evidence", "semantics", "review", "publishing"]) {
    for (const status of ["running", "failed", "cancelled"]) {
      assert.equal(materialOverallProgressPercent(processing(stage, 368, 368, status)), null);
    }
  }
  for (const status of ["succeeded", "partial"])
    assert.equal(materialOverallProgressPercent(processing("completed", 368, 368, status)), 100);
  assert.equal(materialCurrentStagePercent(processing("semantics", 368, 368)), null);
  assert.equal(materialCurrentStagePercent(processing("evidence", 3, 45)), 6);
  assert.equal(materialCurrentStagePercent({ ...processing("semantics", 368, 368), completed_units: 99, total_units: 100 }), 99);
  assert.equal(materialCurrentStagePercent({ ...processing("semantics", 368, 368), completed_units: 999, total_units: 1000 }), 99);
  assert.equal(materialCurrentStagePercent({ ...processing("review", 368, 368), completed_units: 18, total_units: 48 }), 37);
  assert.equal(materialCurrentStagePercent({ ...processing("review", 368, 368), completed_units: 18, total_units: 49 }), 36);
  assert.equal(materialProgressStageLabel("review"), "檢查與修正知識結構");
});

test("unknown or invalid stage denominators are never replaced by guessed progress", () => {
  for (const total of [null, 0, -1, NaN, Infinity, Number.MAX_VALUE])
    assert.equal(materialCurrentStagePercent(processing("evidence", 3, total)), null);
  for (const completed of [-1, 46, NaN, Infinity])
    assert.equal(materialCurrentStagePercent(processing("evidence", completed)), null);
  assert.equal(materialCurrentStagePercent(processing("publishing", 368, 368)), null);
});

test("delete warning describes existing learner content, independently of active processing", async () => {
  const { materialDeleteCopy } = await import("./material-delete-copy.ts");
  const initial = { available_structures: [], study_sessions: [], latest_attempt: null };
  const sources = [
    { status: "ready", media_type: "application/pdf", normalized_artifact_id: "pdf" },
    { status: "failed", media_type: "text/plain", normalized_artifact_id: null },
  ];
  const plain = materialDeleteCopy(initial, sources);
  assert.equal(plain.scope, "將刪除目前已上傳的 2 份教材。此操作無法復原。");
  assert.doesNotMatch(plain.scope, /知識地圖|學習紀錄|題目|作答|轉換/);
  sources.push({ status: "ready", media_type: "text/plain", normalized_artifact_id: "converted" });
  assert.match(materialDeleteCopy(initial, sources).scope, /3 份教材，以及已產生的轉換內容/);
  const map = { ...initial, available_structures: [{}] };
  assert.equal(materialDeleteCopy(map).scope, "將刪除這份教材、已建立的知識地圖與保存的概念卡組。此操作無法復原。");
  const history = { ...map, study_sessions: [{ status: "completed" }] };
  assert.match(materialDeleteCopy(history).scope, /知識地圖與保存的概念卡組，以及相關的學習紀錄、題目與作答/);
  for (const status of ["pending", "running"]) {
    assert.equal(
      materialDeleteCopy({ ...initial, latest_attempt: { status } }).notice,
      "目前的教材處理會先停止，再刪除這份教材。",
    );
    assert.equal(
      materialDeleteCopy({ ...history, latest_attempt: { status } }).notice,
      "目前的教材更新會先停止，再刪除這份教材。",
    );
  }
  assert.match(materialDeleteCopy(initial, [{ status: "running" }]).notice, /先停止/);
  assert.doesNotMatch(materialDeleteCopy().scope, /知識地圖|學習紀錄|題目|作答/);
});


test("remembered material indexes stay isolated by API client and material", async () => {
  const {rememberMaterial, rememberedMaterial} = await import("./material-memory.ts");
  const firstClient = {}, secondClient = {};
  rememberMaterial(firstClient, {material_id:"first", display_name:"first version"});
  rememberMaterial(firstClient, {material_id:"second", display_name:"other material"});
  assert.equal(rememberedMaterial(secondClient,"first"),null);
  assert.equal(rememberedMaterial(firstClient,"first").display_name,"first version");
  rememberMaterial(firstClient, {material_id:"first", display_name:"updated version"});
  assert.equal(rememberedMaterial(firstClient,"first").display_name,"updated version");
  assert.equal(rememberedMaterial(firstClient,"second").display_name,"other material");
});


test("Luna call limit explains why material processing stopped", () => {
  assert.equal(materialFailureMessage("LUNA_CALL_LIMIT_EXCEEDED"), "這次生成用量超過上限，已停止。可以稍後重試。");
});
