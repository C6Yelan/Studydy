import { expect, test, type Page } from "@playwright/test";

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
    schema: "material-processing-run/v2",
    run_id: runId,
    material_id: materialId,
    source_artifact_id: artifactId,
    status: "succeeded",
    output_binding: {
      schema: "material-run-output-binding/v2",
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
  const pageRef = `page:sha256:${"4".repeat(64)}`;
  return {
    schema: "knowledge-map-view/v2",
    material_ref: `material:sha256:${"5".repeat(64)}`,
    knowledge_map_revision: mapRevision,
    source_output_id: outputRevision,
    status: {
      processing: "succeeded",
      quality: "needs_review",
      decision: "review",
      reason_codes: ["WHOLE_DOCUMENT_REVIEW_REQUIRED"],
    },
    concepts: [{
      concept_id: `concept:sha256:${"6".repeat(64)}`,
      label: "二元樹",
      definition: "每個節點最多有兩個子節點的樹。",
      key_points: ["左子節點", "右子節點"],
      page_ref: pageRef,
      evidence: [{
        evidence_id: `evidence:sha256:${"7".repeat(64)}`,
        page_ref: pageRef,
        page_number: 1,
        kind: "native_text",
        region: { coordinate_space: "unrotated_pdf_points", bbox: [40, 50, 220, 82] },
      }],
      quality: "needs_review",
      decision: "review",
      reason_codes: ["CONCEPT_REVIEW_REQUIRED"],
    }],
    images: [],
    excluded_pages: [],
  };
}

test("review-only Map 只顯示概念與同頁 PDF locator", async ({ page }) => {
  await sessionReady(page);
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => {
    return route.fulfill({ status: 200, json: terminalRun() });
  });
  await page.route("**/v1/materials/*/knowledge-maps/**", (route) => {
    return route.fulfill({ status: 200, json: reviewMap() });
  });

  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}`);
  await expect(page.getByRole("heading", { name: "教材概念與 Evidence 複核" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "二元樹" })).toBeVisible();
  await expect(page.getByText("第 1 頁 · native_text")).toBeVisible();
  await expect(page.getByRole("button", { name: "Learning Path", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Assessment", exact: true })).toHaveCount(0);
});
