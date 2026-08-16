import { expect, test } from "@playwright/test";

const materialId = "6f9619ff-8b86-4e3a-a2f1-2bb9424d5c81";
const artifactId = "7f9619ff-8b86-4e3a-a2f1-2bb9424d5c82";
const runId = "8f9619ff-8b86-4e3a-a2f1-2bb9424d5c83";
const mapRevision = `knowledge-map:sha256:${"d".repeat(64)}`;
const pathRevision = `initial-learning-path:sha256:${"e".repeat(64)}`;
const resourceRevision = `learning-resource-result:sha256:${"c".repeat(64)}`;
const outputRevision = `study-material-output:sha256:${"a".repeat(64)}`;
const catalogRevision = `resource-catalog:sha256:${"b".repeat(64)}`;

function terminalRun() {
  return {
    schema: "material-processing-run/v1",
    run_id: runId,
    material_id: materialId,
    source_artifact_id: artifactId,
    status: "partial",
    catalog_revision: catalogRevision,
    output_binding: {
      schema: "material-run-output-binding/v1",
      study_material_output_revision: outputRevision,
      catalog_revision: catalogRevision,
      learning_resource_result_revision: resourceRevision,
      knowledge_map_revision: mapRevision,
      learning_path_revision: pathRevision,
      assessment_revision: `assessment:sha256:${"f".repeat(64)}`,
      processing: "partial",
      quality: "needs_review",
      decision: "review",
      reason_code: "DEVELOPMENT_FULL_DOCUMENT_PARTIAL",
      provider_call_counts: {
        page_structure: 1,
        visual_alignment_adjudication: 0,
        concept_candidate: 1,
        concept_content: 1,
        total: 3,
      },
      development_only: true,
    },
    error_code: null,
    created_at: "2026-08-15T00:00:00Z",
    updated_at: "2026-08-15T00:01:00Z",
    completed_at: "2026-08-15T00:01:00Z",
  };
}

function emptyMap() {
  return {
    schema: "knowledge-map-view/v1",
    material_ref: "material-ref-1",
    knowledge_map_revision: mapRevision,
    learning_path_revision: pathRevision,
    status: {
      processing: "partial",
      quality: "needs_review",
      decision: "review",
      reason_code: "PAGE_CONTENT_EXCLUDED",
    },
    concepts: [],
    relations: [],
    review_items: [],
    path: {
      ordered_concept_ids: [],
      processing: "partial",
      quality: "needs_review",
      decision: "review",
      reason_code: "PATH_EMPTY",
    },
    limitations: [{
      reason_code: "PAGE_CONTENT_EXCLUDED",
      page_numbers: [1],
      affected_page_count: 1,
    }],
  };
}

function emptyResources() {
  return {
    schema: "learning-resource-result-view/v1",
    result_revision: resourceRevision,
    source_study_material_output_revision: outputRevision,
    catalog_revision: catalogRevision,
    subject: "data_structures",
    resources: [],
    produced_at: "2026-08-15T00:01:00Z",
    run_id: runId,
    processing: "partial",
    quality: "needs_review",
    decision: "review",
    reason_code: "NO_RESOURCE_MATCH",
  };
}

function symmetricRelationMap() {
  const concept = (id: string, label: string, x: number) => ({
    id,
    label,
    definition: `${label} 的教材定義。`,
    members: [],
    evidence: [],
    position: { x, y: 80 },
    quality: "accepted",
    reason_code: "CONCEPT_ACCEPTED",
  });
  const relation = (id: string, source: string, target: string, type: "similar" | "confusing") => ({
    id,
    source,
    target,
    type,
    statement: `${source} 與 ${target} 的教材關聯。`,
    evidence: [],
    reason_code: "DIRECT_CLUE_ACCEPTED",
  });
  return {
    ...emptyMap(),
    concepts: [
      concept("concept-a", "陣列", 40),
      concept("concept-b", "串列", 320),
      concept("concept-c", "佇列", 600),
    ],
    relations: [
      relation("relation-similar", "concept-a", "concept-b", "similar"),
      relation("relation-confusing", "concept-b", "concept-c", "confusing"),
    ],
    path: {
      ...emptyMap().path,
      ordered_concept_ids: ["concept-a", "concept-b", "concept-c"],
    },
  };
}

test("partial empty map 與 resource 顯示真實 reason、limitations 與空狀態", async ({ page }) => {
  await page.route(`**/v1/material-processing-runs/${runId}`, async (route) => {
    await route.fulfill({ status: 200, json: terminalRun() });
  });
  await page.route("**/v1/materials/*/knowledge-map-views/*/*?run_id=*", async (route) => {
    await route.fulfill({ status: 200, json: emptyMap() });
  });
  await page.route("**/v1/materials/*/learning-resource-results/*?run_id=*", async (route) => {
    await route.fulfill({ status: 200, json: emptyResources() });
  });

  await page.goto(`/materials/${materialId}/runs/${runId}/maps/${encodeURIComponent(mapRevision)}/paths/${encodeURIComponent(pathRevision)}`);
  await expect(page.getByText("目前沒有可顯示的概念")).toBeVisible();
  await expect(page.getByText("PAGE_CONTENT_EXCLUDED").first()).toBeVisible();
  await expect(page.getByText("PATH_EMPTY")).toBeVisible();
  await page.getByRole("button", { name: "學習資源 0" }).click();
  await expect(page.getByText("目前沒有與這份教材配對的公開學習資源。")).toBeVisible();
  await expect(page.getByText("NO_RESOURCE_MATCH")).toBeVisible();
});

test("similar 與 confusing 使用不同樣式且不顯示單向箭頭", async ({ page }) => {
  await page.route(`**/v1/material-processing-runs/${runId}`, async (route) => {
    await route.fulfill({ status: 200, json: terminalRun() });
  });
  await page.route("**/v1/materials/*/knowledge-map-views/*/*?run_id=*", async (route) => {
    await route.fulfill({ status: 200, json: symmetricRelationMap() });
  });
  await page.route("**/v1/materials/*/learning-resource-results/*?run_id=*", async (route) => {
    await route.fulfill({ status: 200, json: emptyResources() });
  });

  await page.goto(`/materials/${materialId}/runs/${runId}/maps/${encodeURIComponent(mapRevision)}/paths/${encodeURIComponent(pathRevision)}`);
  await expect(page.getByText("相似", { exact: true })).toBeVisible();
  await expect(page.getByText("易混淆", { exact: true })).toBeVisible();
  const similarEdge = page.locator(".react-flow__edge.relation-similar .react-flow__edge-path");
  const confusingEdge = page.locator(".react-flow__edge.relation-confusing .react-flow__edge-path");
  await expect(similarEdge).toBeVisible();
  await expect(confusingEdge).toBeVisible();
  expect(await similarEdge.getAttribute("marker-end")).toBeNull();
  expect(await confusingEdge.getAttribute("marker-end")).toBeNull();
  const similarStroke = await similarEdge.evaluate((element) => getComputedStyle(element).stroke);
  const confusingStroke = await confusingEdge.evaluate((element) => getComputedStyle(element).stroke);
  expect(similarStroke).not.toBe(confusingStroke);
});
