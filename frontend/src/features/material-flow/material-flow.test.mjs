import assert from "node:assert/strict";
import test from "node:test";

import {
  materialProgressStageLabel,
  materialCurrentStagePercent,
  materialOverallProgressPercent,
  validateSourceFile,
} from "./material-flow.ts";

test("material stages describe the current processing work", () => {
  assert.equal(materialProgressStageLabel("evidence"), "整理頁面與教材來源");
  assert.equal(materialProgressStageLabel("semantics"), "建立概念、關係與學習順序");
});

test("each source follows its advertised format and individual size limit", () => {
  const formats = [
    { extension: ".pdf", media_type: "application/pdf", max_bytes: 100 * 1024 * 1024 },
    { extension: ".txt", media_type: "text/plain", max_bytes: 100 * 1024 * 1024 },
  ];
  const valid = { name: "教材.PDF", type: "application/pdf", size: formats[0].max_bytes };
  assert.equal(validateSourceFile(valid, formats), null);
  assert.equal(validateSourceFile({ name: "notes.txt", type: "", size: 12 }, formats), null);
  assert.match(validateSourceFile({ ...valid, type: "text/plain" }, formats), /類型不一致/);
  assert.match(validateSourceFile({ ...valid, name: "file.exe" }, formats), /不支援/);
  assert.match(validateSourceFile({ ...valid, size: 0 }, formats), /空白/);
  assert.match(
    validateSourceFile({ ...valid, size: formats[0].max_bytes + 1 }, formats),
    /100 MiB/,
  );
});

function processing(stage, completed, total = 45, status = "running") {
  return { progress_stage: stage, completed_pages: completed, total_pages: total, status };
}

test("processing projections distinguish measured stage work from structural overall estimates", () => {
  for (const [stage, completed, overall, current] of [
    ["queued", 0, 0, null],
    ["evidence", 0, 0, 0],
    ["evidence", 3, 3, 7],
    ["evidence", 45, 49, 100],
    ["semantics", 0, 49, 0],
    ["semantics", 20, 71, 44],
    ["semantics", 45, 99, 100],
    ["publishing", 45, 99, null],
  ]) {
    assert.equal(materialOverallProgressPercent(processing(stage, completed)), overall);
    assert.equal(materialCurrentStagePercent(processing(stage, completed)), current);
  }
  for (const status of ["succeeded", "partial"]) {
    assert.equal(materialOverallProgressPercent(processing("completed", 45, 45, status)), 100);
    assert.equal(materialCurrentStagePercent(processing("completed", 45, 45, status)), 100);
  }
  assert.equal(materialProgressStageLabel("queued"), "等待處理資源");
  assert.equal(materialProgressStageLabel("publishing"), "發布知識地圖");
});

test("progress handles unknown/invalid denominators and clamps without declaring early completion", () => {
  assert.equal(materialOverallProgressPercent(processing("queued", 0, null, "pending")), 0);
  for (const total of [null, 0, -1, NaN, Infinity, Number.MAX_VALUE]) {
    assert.equal(materialCurrentStagePercent(processing("evidence", 3, total)), null);
    assert.equal(materialOverallProgressPercent(processing("semantics", 3, total)), null);
  }
  assert.equal(materialCurrentStagePercent(processing("evidence", -4)), 0);
  assert.equal(materialCurrentStagePercent(processing("semantics", 99)), 100);
  assert.equal(materialOverallProgressPercent(processing("evidence", -4)), 0);
  assert.equal(materialOverallProgressPercent(processing("evidence", 99)), 49);
  assert.equal(materialOverallProgressPercent(processing("semantics", 99)), 99);
  assert.equal(materialOverallProgressPercent(processing("publishing", 500, 500)), 99);
  assert.equal(materialCurrentStagePercent(processing("evidence", Infinity)), null);
  assert.equal(materialOverallProgressPercent(processing("evidence", NaN)), null);
  assert.equal(materialOverallProgressPercent(processing("completed", 45, 45, "failed")), null);
  assert.equal(materialOverallProgressPercent(processing("semantics", 20, 45, "failed")), 71);
});

test("legal lifecycle projections never decrease as stage page counts reset", () => {
  for (const total of [1, 45, 500]) {
    const snapshots = [processing("queued", 0, null, "pending")];
    for (const stage of ["evidence", "semantics"]) {
      for (let completed = 0; completed <= total; completed++)
        snapshots.push(processing(stage, completed, total));
    }
    snapshots.push(
      processing("publishing", total, total),
      processing("completed", total, total, "succeeded"),
    );
    const values = snapshots.map(materialOverallProgressPercent);
    assert.equal(values[0], 0);
    assert.equal(values.at(-1), 100);
    assert.ok(values.slice(0, -1).every((value) => value >= 0 && value <= 99));
    assert.ok(values.every((value, index) => index === 0 || value >= values[index - 1]));
  }
});

test("cancelled is never projected as successful 100 percent completion", () => {
  for (const stage of ["queued", "evidence", "semantics", "publishing", "completed"]) {
    assert.equal(materialCurrentStagePercent(processing(stage, 45, 45, "cancelled")), null);
    assert.equal(materialOverallProgressPercent(processing(stage, 45, 45, "cancelled")), null);
  }
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
  assert.equal(materialDeleteCopy(map).scope, "將刪除這份教材及已建立的知識地圖。此操作無法復原。");
  const history = { ...map, study_sessions: [{ status: "completed" }] };
  assert.match(materialDeleteCopy(history).scope, /知識地圖，以及相關的學習紀錄、題目與作答/);
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
