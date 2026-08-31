import { expect, test, type Page } from "@playwright/test";

import type { KnowledgeMapView } from "../src/api/contracts";

const materialId = "1f9619ff-8b86-4e3a-a2f1-2bb9424d5c81";
const artifactId = "2f9619ff-8b86-4e3a-a2f1-2bb9424d5c82";
const runId = "3f9619ff-8b86-4e3a-a2f1-2bb9424d5c83";
const mapRevision = `knowledge-map:sha256:${"a".repeat(64)}`;

async function sessionReady(page: Page) {
  await page.route("**/v1/session/refresh", (route) => route.fulfill({ status: 204 }));
}

async function captureAcceptance(page: Page, filename: string) {
  if (process.env.STUDYDY_CAPTURE_ACCEPTANCE !== "1") return;
  await page.locator("img").evaluateAll(async (images) => {
    await Promise.all(images.map((item) => item.decode().catch(() => undefined)));
  });
  await page.screenshot({ path: `../docs_local/p07_acceptance/${filename}`, fullPage: true });
}

function revision(prefix: string, value: string) {
  return `${prefix}:sha256:${value.repeat(64)}`;
}

function concept(index: number, label: string): KnowledgeMapView["concepts"][number] {
  const value = String(index);
  return {
    formal_concept_id: revision("formal-concept", value),
    label,
    aliases: [],
    claims: [{
      claim_id: revision("claim", value),
      text: `${label} 的教材重點。`,
      evidence: [{
        evidence_id: revision("evidence", value),
        page_ref: revision("page", value),
        page_number: index,
        kind: "paragraph",
        region: { coordinate_space: "unrotated_pdf_points", bbox: [40, 50, 260, 90] },
      }],
    }],
    source_concept_ids: [revision("concept", value)],
    source_page_numbers: [index],
    supplementary_resources: [],
    quality: "needs_review",
    decision: "review",
    reason_codes: ["FORMAL_CONCEPT_REVIEW_REQUIRED"],
  };
}

function publishedMap(): KnowledgeMapView {
  const concepts = [concept(1, "概念甲"), concept(2, "概念乙"), concept(3, "概念丙")];
  concepts[0].supplementary_resources.push({
    promotion_id: revision("resource-promotion", "4"),
    resource_concept_id: revision("resource-concept", "5"),
    resource_id: revision("resource", "6"),
    label: "概念甲",
    title: "補充教材",
    authors: ["Studydy Team"],
    source_url: "https://example.com/resource",
    citation: "Studydy Team. 補充教材。",
    license: "CC BY 4.0",
    license_url: "https://creativecommons.org/licenses/by/4.0/",
    use_boundary: "依授權條款使用",
    page_numbers: [1],
    resource_evidence_ids: [revision("resource-evidence", "7")],
    match_ids: [revision("resource-match", "8")],
    study_concept_ids: [concepts[0].source_concept_ids[0]],
    match_reason: "EXACT_NORMALIZED_LABEL",
  });
  const relation = (index: number, type: "prerequisite" | "contains" | "related", source: number, target: number) => ({
    relation_id: revision("formal-relation", String(index + 3)),
    type,
    source_formal_concept_id: concepts[source].formal_concept_id,
    target_formal_concept_id: concepts[target].formal_concept_id,
    reason: "兩個概念共享一個具體的教材應用。",
    inference_basis: "claim_semantics",
    relation_evidence: [{
      owner_formal_concept_id: concepts[source].formal_concept_id,
      claim_id: concepts[source].claims[0].claim_id,
      evidence_ids: [concepts[source].claims[0].evidence[0].evidence_id],
    }],
    relation_context: [],
    needs_review: false,
    quality: "needs_review",
    decision: "review",
    reason_codes: ["RELATION_REVIEW_REQUIRED"],
    is_in_prerequisite_cycle: false,
  });
  return {
    schema: "knowledge-map-view/v9",
    material_ref: revision("material", "9"),
    knowledge_map_revision: mapRevision,
    source_output_id: revision("study-material-output", "b"),
    status: {
      processing: "succeeded",
      quality: "needs_review",
      decision: "review",
      reason_codes: ["KNOWLEDGE_MAP_REVIEW_REQUIRED"],
    },
    concepts,
    concept_diagnostics: {
      possible_pairs: 3,
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
      source_concepts_before: 3,
      canonical_concepts_after: 3,
      duplicate_delta: 0,
      coverage_before: 3,
      coverage_after: 3,
    },
    relations: [
      relation(1, "prerequisite", 0, 1),
      relation(2, "contains", 1, 2),
      relation(3, "related", 0, 2),
    ],
    relation_diagnostics: {
      possible_pairs: 3,
      candidate_pairs: 3,
      selected_pairs: 3,
      selected_signal_counts: { explicit_relation: 3 },
      model_calls: 1,
      model_no_relation_pairs: 0,
      model_review_pairs: 0,
      unexpected_pairs: 0,
      canonical_rejections: 0,
      verifier_calls: 2,
      verifier_accepted: 2,
      verifier_rejected: 0,
      verifier_unsupported: 0,
      model_contains_pairs: 1,
      model_prerequisite_pairs: 1,
      model_related_pairs: 1,
      invalid_pairs: 0,
      verifier_failures: 0,
      accepted_relations: 3,
    },
    resource_binding: {
      context_revision: revision("map-resource-context", "c"),
      library_revision: revision("resource-library", "d"),
      matching_policy: "resource-context-exact-distinct-source/v3",
      promotion_policy: "resource-formal-concept-promotion/v1",
    },
    resource_diagnostics: {
      matches: 1,
      promoted_matches: 1,
      promoted_resources: 1,
      dropped_matches: 0,
      split_review_matches: 0,
    },
    resource_decisions: [],
    topology: {
      roots: [concepts[0].formal_concept_id, concepts[1].formal_concept_id],
      nodes: concepts.map((item, index) => ({
        formal_concept_id: item.formal_concept_id,
        depth: index === 2 ? 1 : 0,
        primary_parent_formal_concept_id: index === 2
          ? concepts[1].formal_concept_id : null,
        flat_group_id: revision("document-section", String(index + 1)),
        flat_group_anchor: {
          evidence_id: item.claims[0].evidence[0].evidence_id,
          page_ref: item.claims[0].evidence[0].page_ref,
          page_number: item.source_page_numbers[0],
          reading_order: index,
        },
      })),
      flat_groups: concepts.map((item, index) => ({
        flat_group_id: revision("document-section", String(index + 1)),
        label: `教材單元 ${index + 1}`,
        label_source: "heading",
        heading_evidence_id: item.claims[0].evidence[0].evidence_id,
        source_order: {
          evidence_id: item.claims[0].evidence[0].evidence_id,
          page_ref: item.claims[0].evidence[0].page_ref,
          page_number: item.source_page_numbers[0],
          reading_order: index,
        },
        formal_concept_ids: [item.formal_concept_id],
      })),
    },
    topology_diagnostics: {
      component_count: 2,
      orphan_concept_count: 1,
      secondary_parent_count: 0,
      skipped_parent_before_child_count: 0,
    },
    initial_learning_path: concepts.map((item, index) => ({
      step_number: index + 1,
      formal_concept_id: item.formal_concept_id,
      placement_reason: index === 0
        ? "依教材第 1 頁的首次出現位置安排。"
        : index === 1
          ? "先理解「概念甲」，再進入這個概念。"
          : "先建立上層概念「概念乙」，再學習這個子概念。",
      order_basis: {
        prerequisite_formal_concept_ids: index === 1
          ? [concepts[0].formal_concept_id] : [],
        parent_formal_concept_ids: index === 2
          ? [concepts[1].formal_concept_id] : [],
        flat_group_id: revision("document-section", String(index + 1)),
        hierarchy_depth: index === 2 ? 1 : 0,
        source_page_number: index + 1,
      },
    })),
    excluded_pages: [],
  };
}

function terminalRun() {
  return {
    schema: "material-processing-run/v3",
    run_id: runId,
    material_id: materialId,
    source_artifact_id: artifactId,
    status: "succeeded",
    progress_stage: "completed",
    completed_pages: 3,
    total_pages: 3,
    output_binding: {
      schema: "material-run-output-binding/v3",
      producer_bundle_id: revision("text-first-producer-bundle", "1"),
      producer_run_id: "text-first-run:00000000-0000-4000-8000-000000000001",
      concept_evidence_output_id: revision("concept-evidence-output", "2"),
      study_material_output_revision: revision("study-material-output", "b"),
      knowledge_map_revision: mapRevision,
      runtime_binding_sha256: "3".repeat(64),
      page_count: 3,
      processing: "succeeded",
      quality: "needs_review",
      decision: "review",
      reason_codes: ["WHOLE_DOCUMENT_REVIEW_REQUIRED"],
      ocr_calls: 0,
      concept_calls: 1,
    },
    error_code: null,
    created_at: "2026-08-27T00:00:00Z",
    updated_at: "2026-08-27T00:01:00Z",
    completed_at: "2026-08-27T00:01:00Z",
  };
}

test("Knowledge Map 只呈現 published 三種 Relation，related 保持對稱", async ({ page }) => {
  await sessionReady(page);
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) => route.fulfill({ status: 200, json: terminalRun() }));
  await page.route("**/v1/materials/*/knowledge-maps/**", (route) => route.fulfill({ status: 200, json: publishedMap() }));

  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}`);
  await expect(page.getByRole("heading", { name: "知識地圖", exact: true })).toBeVisible();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await expect(page.locator(".react-flow__controls")).toBeVisible();
  await expect(page.getByLabel("教材平面段落").locator("article")).toHaveCount(3);
  await expect(page.getByLabel("教材平面段落").getByText("教材單元 1", { exact: true })).toBeVisible();
  await expect(page.getByText("目前位於本段第 1 個概念", { exact: true })).toBeVisible();
  await captureAcceptance(page, "01_app_shell_desktop.png");
  await captureAcceptance(page, "06_map_focus_concept.png");

  await page.getByRole("tab", { name: "概念地圖" }).click();
  await expect(page.locator('.relation-connector.is-prerequisite[data-directional="true"]').first()).toBeVisible();
  await expect(page.locator('.relation-connector.is-contains[data-directional="true"]').first()).toBeVisible();
  await expect(page.locator('.relation-connector.is-related[data-directional="false"]').first()).toBeVisible();
  await expect(page.getByText(/similar|confusing|application|example/i)).toHaveCount(0);
  await expect(page.getByText(/candidate_pairs|verifier_calls|relation_diagnostics/i)).toHaveCount(0);
  await page.getByLabel("焦點概念").selectOption({ label: "概念乙" });
  await expect(page.locator(".concept-flow-node.is-focus")).toContainText("概念乙");
  await expect(page.getByLabel("教材平面段落").getByText("教材單元 2", { exact: true })).toBeVisible();
  const viewport = page.locator(".react-flow__viewport");
  const beforeZoom = await viewport.getAttribute("style");
  await page.locator(".react-flow__controls-zoomin").click();
  await expect.poll(() => viewport.getAttribute("style")).not.toBe(beforeZoom);
  await page.locator(".react-flow__controls-fitview").click();
  const pane = page.locator(".react-flow__pane");
  const paneBox = await pane.boundingBox();
  expect(paneBox).not.toBeNull();
  const beforePan = await viewport.getAttribute("style");
  await page.mouse.move(paneBox!.x + paneBox!.width - 30, paneBox!.y + paneBox!.height - 30);
  await page.mouse.down();
  await page.mouse.move(paneBox!.x + paneBox!.width - 90, paneBox!.y + paneBox!.height - 70, { steps: 4 });
  await page.mouse.up();
  await expect.poll(() => viewport.getAttribute("style")).not.toBe(beforePan);
  await page.locator(".concept-flow-edge.is-related").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "互相關聯" })).toBeVisible();
  await expect(page.getByText("兩個概念共享一個具體的教材應用。")).toBeVisible();
  await expect(page.getByText("推論依據：教材敘述")).toBeVisible();
  await captureAcceptance(page, "07_relation_detail.png");

  await page.getByRole("tab", { name: "學習順序" }).click();
  await expect(page.getByRole("heading", { name: "教材建議學習順序" })).toBeVisible();
  await expect(page.locator(".learning-path li")).toHaveCount(3);
  await expect(page.getByText("先理解「概念甲」，再進入這個概念。", { exact: true })).toBeVisible();
  await captureAcceptance(page, "08_initial_path.png");

  await page.getByRole("tab", { name: "總覽" }).click();
  await expect(page.locator(".concept-card")).toHaveCount(3);
  await captureAcceptance(page, "05_map_overview.png");
  await page.getByRole("button", { name: /概念甲/ }).click();
  await expect(page.getByLabel("概念詳情").getByText("概念甲 的教材重點。")).toBeVisible();
  await expect(page.getByRole("link", { name: "開啟資源" })).toHaveAttribute("href", "https://example.com/resource");
});

test("390px 使用可讀的語意階層與連結清單", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await sessionReady(page);
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) =>
    route.fulfill({ status: 200, json: terminalRun() }));
  await page.route("**/v1/materials/*/knowledge-maps/**", (route) =>
    route.fulfill({ status: 200, json: publishedMap() }));

  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}`);
  await expect(page.locator(".focus-graph")).toBeHidden();
  await expect(page.getByLabel("概念階層清單")).toBeVisible();
  await expect(page.getByRole("treeitem")).toHaveCount(3);
  await expect(page.getByLabel("概念階層清單").getByRole("heading", { name: "教材單元 1" })).toBeVisible();
  await expect(page.getByRole("treeitem").first().getByText("目前位置", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "其他教材連結" })).toBeVisible();
});
