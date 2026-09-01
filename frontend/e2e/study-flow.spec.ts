import { expect, test, type Page } from "@playwright/test";

import { mapView as sharedMapView } from "./fixtures/learning";

const materialId = "6f9619ff-8b86-4e3a-a2f1-2bb9424d5c81";
const artifactId = "7f9619ff-8b86-4e3a-a2f1-2bb9424d5c82";
const runId = "8f9619ff-8b86-4e3a-a2f1-2bb9424d5c83";
const mapRevision = `knowledge-map:sha256:${"d".repeat(64)}`;
const outputRevision = `study-material-output:sha256:${"a".repeat(64)}`;

async function sessionReady(page: Page) {
  await page.route("**/v1/session/refresh", (route) => route.fulfill({ status: 204 }));
}

function terminalRun() {
  return {
    schema: "material-processing-run/v3",
    run_id: runId,
    material_id: materialId,
    source_artifact_id: artifactId,
    status: "succeeded",
    progress_stage: "completed",
    completed_pages: 1,
    total_pages: 1,
    output_binding: {
      schema: "material-run-output-binding/v3",
      producer_bundle_id: `text-first-producer-bundle:sha256:${"1".repeat(64)}`,
      producer_run_id: "text-first-run:00000000-0000-4000-8000-000000000001",
      concept_evidence_output_id: `concept-evidence-output:sha256:${"2".repeat(64)}`,
      study_material_output_revision: outputRevision,
      knowledge_map_revision: mapRevision,
      runtime_binding_sha256: "3".repeat(64),
      page_count: 1,
      processing: "succeeded",
      quality: "needs_review",
      decision: "review",
      reason_codes: ["WHOLE_DOCUMENT_REVIEW_REQUIRED"],
      ocr_calls: 1,
      concept_calls: 1,
    },
    error_code: null,
    created_at: "2026-08-19T00:00:00Z",
    updated_at: "2026-08-19T00:01:00Z",
    completed_at: "2026-08-19T00:01:00Z",
  };
}

function reviewMap() {
  const view = structuredClone(sharedMapView());
  const concept = view.concepts[0];
  concept.label = "二元樹";
  concept.claims[0].text = "每個節點最多有兩個子節點的樹。";
  concept.claims[0].evidence[0].kind = "native_text";
  view.knowledge_map_revision = mapRevision;
  view.source_output_id = outputRevision;
  view.concepts = [concept];
  view.concept_diagnostics.possible_pairs = 0;
  view.concept_diagnostics.source_concepts_before = 1;
  view.concept_diagnostics.canonical_concepts_after = 1;
  view.concept_diagnostics.coverage_before = 1;
  view.concept_diagnostics.coverage_after = 1;
  const section = view.document_tree.sections[0];
  section.label = "二元樹";
  section.concept_ids = [concept.formal_concept_id];
  view.document_tree.root.section_ids = [section.section_id];
  view.document_tree.sections = [section];
  view.initial_learning_path = [{
    ...view.initial_learning_path[0],
    formal_concept_id: concept.formal_concept_id,
  }];
  return view;
}

test("review-only Map 顯示概念、教材順序與同頁 PDF locator", async ({ page }) => {
  await sessionReady(page);
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => {
    return route.fulfill({ status: 200, json: terminalRun() });
  });
  await page.route("**/v1/materials/*/knowledge-maps/**", (route) => {
    return route.fulfill({ status: 200, json: reviewMap() });
  });

  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}`);
  await expect(page.getByRole("heading", { name: "知識地圖", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "教材概念：二元樹" }).click();
  await expect(page.getByRole("heading", { name: "二元樹" })).toBeVisible();
  await expect(page.getByText("原始教材第 1 頁")).toBeVisible();
  await page.getByRole("tab", { name: "學習順序" }).click();
  await expect(page.getByRole("heading", { name: "教材建議學習順序" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Assessment", exact: true })).toHaveCount(0);
});
