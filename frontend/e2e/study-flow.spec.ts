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
  const pageRef = `page:sha256:${"4".repeat(64)}`;
  const formalConceptId = `formal-concept:sha256:${"6".repeat(64)}`;
  return {
    schema: "knowledge-map-view/v8",
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
      formal_concept_id: formalConceptId,
      label: "二元樹",
      aliases: [],
      claims: [{
        claim_id: `claim:sha256:${"8".repeat(64)}`,
        text: "每個節點最多有兩個子節點的樹。",
        evidence: [{
          evidence_id: `evidence:sha256:${"7".repeat(64)}`,
          page_ref: pageRef,
          page_number: 1,
          kind: "native_text",
          region: { coordinate_space: "unrotated_pdf_points", bbox: [40, 50, 220, 82] },
        }],
      }],
      source_concept_ids: [`concept:sha256:${"9".repeat(64)}`],
      source_page_numbers: [1],
      supplementary_resources: [],
      quality: "needs_review",
      decision: "review",
      reason_codes: ["FORMAL_CONCEPT_REVIEW_REQUIRED"],
    }],
    concept_diagnostics: {
      possible_pairs: 0,
      candidate_pairs: 0,
      selected_pairs: 0,
      pair_ceiling: 16,
      qwen_same_pairs: 0,
      qwen_distinct_pairs: 0,
      qwen_uncertain_pairs: 0,
      verifier_requested_pairs: 0,
      verifier_scored_pairs: 0,
      verifier_allowed_pairs: 0,
      verifier_vetoed_pairs: 0,
      verifier_unsupported_pairs: 0,
      verifier_failed_pairs: 0,
      source_concepts_before: 1,
      canonical_concepts_after: 1,
      duplicate_delta: 0,
      coverage_before: 1,
      coverage_after: 1,
    },
    relations: [],
    relation_diagnostics: {
      possible_pairs: 0,
      candidate_pairs: 0,
      selected_pairs: 0,
      selected_signal_counts: {},
      model_calls: 0,
      model_no_relation_pairs: 0,
      model_review_pairs: 0,
      unexpected_pairs: 0,
      canonical_rejections: 0,
      verifier_calls: 0,
      verifier_accepted: 0,
      verifier_rejected: 0,
      verifier_unsupported: 0,
      model_contains_pairs: 0,
      model_prerequisite_pairs: 0,
      model_related_pairs: 0,
      invalid_pairs: 0,
      verifier_failures: 0,
      accepted_relations: 0,
    },
    resource_binding: {
      context_revision: `map-resource-context:sha256:${"1".repeat(64)}`,
      library_revision: `resource-library:sha256:${"2".repeat(64)}`,
      matching_policy: "resource-context-exact-distinct-source/v3",
      promotion_policy: "resource-formal-concept-promotion/v1",
    },
    resource_diagnostics: {
      matches: 0,
      promoted_matches: 0,
      promoted_resources: 0,
      dropped_matches: 0,
      split_review_matches: 0,
    },
    resource_decisions: [],
    initial_learning_path: [formalConceptId],
    excluded_pages: [],
  };
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
  await page.getByRole("button", { name: /二元樹/ }).click();
  await expect(page.getByRole("heading", { name: "二元樹" })).toBeVisible();
  await expect(page.getByText("原始教材第 1 頁")).toBeVisible();
  await page.getByRole("tab", { name: "學習順序" }).click();
  await expect(page.getByRole("heading", { name: "教材建議學習順序" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Assessment", exact: true })).toHaveCount(0);
});
