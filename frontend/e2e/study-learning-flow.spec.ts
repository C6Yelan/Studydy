import { expect, test, type Page } from "@playwright/test";

const materialId = "1f9619ff-8b86-4e3a-a2f1-2bb9424d5c91";
const artifactId = "2f9619ff-8b86-4e3a-a2f1-2bb9424d5c92";
const runId = "3f9619ff-8b86-4e3a-a2f1-2bb9424d5c93";
const outputRevision = `study-material-output:sha256:${"a".repeat(64)}`;
const mapRevision = `knowledge-map:sha256:${"b".repeat(64)}`;
const pathRevision = `initial-learning-path:sha256:${"c".repeat(64)}`;
const assessmentRevision = `assessment:sha256:${"d".repeat(64)}`;
const catalogRevision = `resource-catalog:sha256:${"6".repeat(64)}`;

function terminalRun() {
  return {
    schema: "material-processing-run/v1",
    run_id: runId,
    material_id: materialId,
    source_artifact_id: artifactId,
    status: "succeeded",
    catalog_revision: catalogRevision,
    output_binding: {
      schema: "material-run-output-binding/v1",
      study_material_output_revision: outputRevision,
      catalog_revision: catalogRevision,
      learning_resource_result_revision: `learning-resource-result:sha256:${"7".repeat(64)}`,
      knowledge_map_revision: mapRevision,
      learning_path_revision: pathRevision,
      assessment_revision: assessmentRevision,
      processing: "succeeded",
      quality: "accepted",
      decision: "retain",
      reason_code: "DEVELOPMENT_OUTPUT_ACCEPTED",
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

function assessmentView() {
  return {
    schema: "assessment-view/v1",
    assessment_view_id: `assessment-view:sha256:${"8".repeat(64)}`,
    version: "1",
    knowledge_map_revision: mapRevision,
    learning_path_revision: pathRevision,
    scoring_rule_version: "single-choice-exact/v1",
    questions: [{
      question_id: "question-1",
      concept_id: "concept-1",
      question_type: "single_choice",
      prompt: "哪一項敘述有教材依據直接支持？",
      options: [
        { option_id: "option-1", text: "教材沒有提供相關依據。" },
        { option_id: "option-2", text: "教材直接說明這個概念。" },
      ],
      source_evidence_ids: ["evidence-1"],
    }],
    practice_sets: [{
      practice_set_id: "practice-set-1",
      concept_id: "concept-1",
      question_ids: ["question-1"],
    }],
    processing: "succeeded",
    quality: "accepted",
    decision: "retain",
    reason_code: "ASSESSMENT_ACCEPTED",
  };
}

async function installAssessmentReads(page: Page) {
  await page.route(`**/v1/material-processing-runs/${runId}`, async (route) => {
    await route.fulfill({ status: 200, json: terminalRun() });
  });
  const mismatchedAssessment = {
    ...assessmentView(),
    knowledge_map_revision: `knowledge-map:sha256:${"f".repeat(64)}`,
  };
  await page.route("**/v1/materials/*/assessments/*?*", async (route) => {
    await route.fulfill({ status: 200, json: mismatchedAssessment });
  });
}

test("評量版本不一致時停止顯示題目", async ({ page }) => {
  await installAssessmentReads(page);
  await page.goto(`/materials/${materialId}/runs/${runId}/assessments/${encodeURIComponent(assessmentRevision)}`);
  await expect(page.getByRole("heading", { name: "無法載入學習評量" })).toBeVisible();
  await expect(page.getByText("評量版本與回應不一致")).toBeVisible();
  await expect(page.getByRole("radio")).toHaveCount(0);
});
