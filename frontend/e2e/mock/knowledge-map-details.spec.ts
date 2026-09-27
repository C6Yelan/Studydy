import { expect, test, type Page } from "@playwright/test";
import {
  browserOrigin,
  materialId,
  runId,
  sessionId,
  artifactId,
  structureRevision,
  firstConcept,
  secondConcept,
  firstClaim,
  structureView,
  json,
  mockKnowledgeMapApi,
  openMapConcept,
  workspaceView,
} from "../fixtures/knowledge-map";

const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const apiMapPath = `/v1/materials/${materialId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;

async function openFocusRelation(page: Page) {
  if (await page.getByRole("dialog", { name: "概念詳情" }).count())
    await page.keyboard.press("Escape");
  const edge = page.locator(".focus-graph .concept-flow-edge").first();
  await edge.focus();
  await page.keyboard.press("Enter");
  return edge;
}

test("map opens concept details and source-backed learning on demand", async ({ page }) => {
  await mockKnowledgeMapApi(page);
  await page.route("**/v1/study-sessions", (route) => {
    expect(route.request().postDataJSON()).toEqual({
      schema: "study-session-create/v1",
      material_id: materialId,
      knowledge_structure_revision: structureRevision,
      current_concept_id: firstConcept,
    });
    return route.fallback();
  });
  await page
    .context()
    .route(`**/v1/artifacts/${artifactId}`, (route) =>
      route.fulfill({ contentType: "text/plain", body: "Synthetic source document" }),
    );
  await page.goto(`/materials/${materialId}/runs/${runId}`);
  await page.getByRole("button", { name: "開啟知識地圖", exact: true }).click();
  await expect(page).toHaveURL(`${browserOrigin}${mapPath}`);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "學習入口" })).toHaveCount(0);
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  const edge = await openFocusRelation(page);
  await expect(page.getByRole("dialog", { name: "關係詳情" })).toContainText(
    "Stack must be learned before Array traversal.",
  );
  await page.keyboard.press("Escape");
  await expect(edge).toBeFocused();
  await openMapConcept(page, "Stack");
  await page.getByRole("button", { name: /查看第 1 頁來源/ }).click();
  const popup = page.waitForEvent("popup");
  await page.getByRole("link", { name: "開啟 PDF 來源頁" }).click();
  const source = await popup;
  await expect(source).toHaveURL(`${browserOrigin}/v1/artifacts/${artifactId}#page=1`);
  await source.close();
  await page.keyboard.press("Escape");
  // Array 的 Evidence 在第 2 頁，回查不能再沿用 Stack 的第 1 頁。
  await openMapConcept(page, "Array");
  await page.getByRole("button", { name: /查看第 2 頁來源/ }).click();
  await expect(page.getByRole("link", { name: "開啟 PDF 來源頁" })).toHaveAttribute(
    "href",
    `/v1/artifacts/${artifactId}#page=2`,
  );
  await page.keyboard.press("Escape");
  await openMapConcept(page, "Stack");
  await page.getByRole("button", { name: "開始學習", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
});

test("mobile map has a modal detail drawer with keyboard focus and source links", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockKnowledgeMapApi(page);
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "概念地圖" }).click();
  await openMapConcept(page);
  const dialog = page.getByRole("dialog", { name: "概念詳情" });
  await expect(dialog).toBeInViewport();
  await expect(dialog.getByRole("button", { name: /查看第 1 頁來源/ })).toBeVisible();
  await expect.poll(() => dialog.evaluate((element) => element.matches(":modal"))).toBe(true);
  await page.keyboard.press("Tab");
  await expect
    .poll(() => page.evaluate(() => !!document.activeElement?.closest(".detail-panel")))
    .toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".concept-flow-node.is-focus")).toBeFocused();
  await openFocusRelation(page);
  await expect(page.getByRole("dialog", { name: "關係詳情" })).toContainText(
    "Stack must be learned before Array traversal.",
  );
});

test("map errors can be retried and an empty map has an actionable explanation", async ({
  page,
}) => {
  const empty = structureView();
  empty.concepts = [];
  empty.relations = [];
  empty.initial_learning_path = [];
  empty.document_tree.sections = [];
  await mockKnowledgeMapApi(page, empty);
  await page.route(
    `**${apiMapPath}`,
    (route) =>
      json(
        route,
        {
          schema: "api-error/v1",
          request_id: materialId,
          reason_code: "STORAGE_UNAVAILABLE",
          retryable: true,
          message: "Request could not be completed.",
        },
        503,
      ),
    { times: 1 },
  );
  await page.goto(mapPath);
  await expect(page.getByRole("heading", { name: "無法讀取知識地圖", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重新讀取", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "知識地圖目前是空的", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看處理狀態", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
});

test("parallel relations retain separate labels, paths and details in both directions", async ({
  page,
}) => {
  const view = structureView();
  view.relations = ["prerequisite", "part_of", "application", "example", "contrast"].map(
    (type, index) => ({
      ...view.relations[0],
      type,
      relation_id: `relation:sha256:${(index + 20).toString(16).padStart(64, "0")}`,
      source_concept_id: index % 2 ? secondConcept : firstConcept,
      target_concept_id: index % 2 ? firstConcept : secondConcept,
      learner_reason: `Distinct grounded explanation for ${type}.`,
    }),
  );
  await mockKnowledgeMapApi(page, view);
  await page.goto(mapPath);
  const assertSeparate = async () => {
    await expect(page.locator(".concept-flow-edge")).toHaveCount(5);
    await expect
      .poll(() =>
        page
          .locator(".concept-flow-edge .react-flow__edge-path")
          .evaluateAll((paths) => new Set(paths.map((path) => path.getAttribute("d"))).size),
      )
      .toBe(5);
    await expect
      .poll(() =>
        page.locator(".concept-flow-edge .react-flow__edge-textbg").evaluateAll((labels) => {
          const boxes = labels.map((label) => label.getBoundingClientRect());
          return (
            boxes.length === 5 &&
            boxes.every((box) => box.width > 0 && box.height > 0) &&
            boxes.every((a, i) =>
              boxes
                .slice(i + 1)
                .every(
                  (b) =>
                    a.right <= b.left ||
                    b.right <= a.left ||
                    a.bottom <= b.top ||
                    b.bottom <= a.top,
                ),
            )
          );
        }),
      )
      .toBe(true);
  };
  await assertSeparate();
  await expect(page.locator(".focus-context-content .relation-list")).toHaveCount(0);
  const relationDialog = page.getByRole("dialog", { name: "關係詳情", exact: true });
  for (const relation of view.relations) {
    await page.locator(`.concept-flow-edge.is-${relation.type} .react-flow__edge-textbg`).click();
    await expect(relationDialog).toContainText(relation.learner_reason);
    await expect(relationDialog.locator(".relation-direction strong")).toHaveText([
      view.concepts.find((concept) => concept.concept_id === relation.source_concept_id)!.label,
      view.concepts.find((concept) => concept.concept_id === relation.target_concept_id)!.label,
    ]);
    await assertSeparate();
    if (relation.type === "prerequisite") {
      await relationDialog.getByRole("button", { name: /目標概念/ }).click();
      await expect(
        page
          .getByRole("dialog", { name: "概念詳情" })
          .getByRole("heading", { name: "Array", exact: true }),
      ).toBeVisible();
    }
    await page.keyboard.press("Escape");
    await expect(page.locator(`.concept-flow-edge.is-${relation.type}`)).toBeFocused();
  }
  await openMapConcept(page, "Array");
  await assertSeparate();
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 900, height: 800 });
  await assertSeparate();
  await page.locator(".concept-flow-edge.is-application").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "關係詳情" })).toContainText(
    "Distinct grounded explanation for application.",
  );
});

for (const viewport of [
  { width: 1536, height: 1024 },
  { width: 390, height: 844 },
]) {
  for (const sourceIdentity of ["id", "name", "unknown"] as const) {
    test(`concept sources group by ${sourceIdentity} and alias search works at ${viewport.width}px`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      const view = workspaceView(2);
      const concept = view.concepts[0];
      concept.label = "主機";
      concept.aliases = ["Host"];
      const evidence = [0, 1, 2, 3].map((index) => ({
        ...concept.claims[0].evidence[0],
        evidence_id: `evidence:sha256:${String(index + 1).repeat(64)}`,
        // 原始頁碼刻意相同，確認多來源入口依 normalized_page 分組。
        page: 1,
        ...(sourceIdentity === "id" ? { source_id: index === 2 ? materialId : artifactId } : {}),
        ...(sourceIdentity === "unknown"
          ? {}
          : {
              source_name: sourceIdentity === "id" || index !== 2 ? "Network.pptx" : "Other.pptx",
              normalized_page: index === 3 ? 2 : 1,
            }),
        quote: "Extraction text should not appear in the concept panel.",
      }));
      concept.claims = [
        { ...concept.claims[0], text: "能參與網路通訊的端點。", evidence: evidence.slice(0, 1) },
        {
          ...concept.claims[0],
          claim_id: firstClaim,
          text: "主機包括電腦與行動裝置。",
          evidence: evidence.slice(1),
        },
      ];
      view.concepts.slice(1).forEach((item) => {
        item.claims[0].evidence = [
          { ...item.claims[0].evidence[0], evidence_id: `evidence:sha256:${"9".repeat(64)}` },
        ];
      });
      view.relations[0].evidence_refs = evidence.map((item) => item.evidence_id);
      await mockKnowledgeMapApi(page, view);
      await page.goto(mapPath);
      const search = page.getByRole("searchbox", { name: "搜尋概念或關鍵字" });
      await search.fill("Host");
      await expect(page.locator(".map-search-results strong")).toHaveText(["主機"]);
      await search.press("Enter");
      const detail = page.getByRole("dialog", { name: "概念詳情", exact: true });
      await expect(detail.getByRole("heading", { name: "主機", exact: true })).toBeVisible();
      await expect(detail.getByRole("heading", { name: "教材來源", exact: true })).toBeVisible();
      await expect(detail.getByRole("region", { name: /^教材重點 / })).toHaveCount(2);
      await expect(detail.getByRole("button", { name: "開始學習", exact: true })).toBeEnabled();
      await expect(detail).not.toContainText(
        /對照教材原文|教材中的其他名稱|延伸探索|Extraction text|Host|sha256/,
      );
      // 有來源識別時合併同來源同 PDF 頁；未知來源則保留全部 Evidence。
      const representatives =
        sourceIdentity === "unknown" ? evidence : [evidence[0], evidence[2], evidence[3]];
      const links = detail
        .getByRole("region", { name: "教材來源", exact: true })
        .getByRole("button");
      await expect(links).toHaveCount(representatives.length);
      for (const [index, representative] of representatives.entries()) {
        const resolution = page.waitForRequest((request) => {
          const path = new URL(request.url()).pathname;
          return path.startsWith(`${apiMapPath}/evidence/`) && path.endsWith("/source");
        });
        await links.nth(index).click();
        const source = page.getByRole("dialog", { name: "教材來源", exact: true });
        expect(new URL((await resolution).url()).pathname).toBe(
          `${apiMapPath}/evidence/${encodeURIComponent(representative.evidence_id)}/source`,
        );
        await expect(
          source.getByRole("heading", {
            name: representative.source_name ?? "Synthetic.pdf",
            exact: true,
          }),
        ).toBeVisible();
        await expect(source.getByRole("link", { name: "開啟 PDF 來源頁" })).toHaveAttribute(
          "href",
          `/v1/artifacts/${artifactId}#page=${representative.normalized_page ?? representative.page}`,
        );
        await page.keyboard.press("Escape");
        await expect(source).toHaveCount(0);
        await expect(links.nth(index)).toBeFocused();
      }
      expect(
        await detail.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
      ).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await page.keyboard.press("Escape");
      await expect(detail).toHaveCount(0);
      await expect(search).toBeFocused();
      const edge = await openFocusRelation(page);
      const relation = page.getByRole("dialog", { name: "關係詳情", exact: true });
      await expect(relation).toContainText(view.relations[0].learner_reason);
      await expect(relation.getByRole("button", { name: /PDF 第|查看第|原始教材第/ })).toHaveCount(
        representatives.length,
      );
      await relation.getByRole("button", { name: "關閉關係詳情" }).click();
      await expect(edge).toBeFocused();
      await openMapConcept(page);
      await detail.getByRole("button", { name: "關閉概念詳情" }).click();
      await expect(page.locator(".concept-flow-node.is-focus")).toBeFocused();
    });
  }
}

for (const width of [1536, 390])
  test(`converted source preserves original download and PDF preview at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1024 });
    const view = structureView();
    await mockKnowledgeMapApi(page, view);
    const evidenceId = view.concepts[0].claims[0].evidence[0].evidence_id;
    let reads = 0;
    await page.route(
      `**${apiMapPath}/evidence/${encodeURIComponent(evidenceId)}/source`,
      (route) => {
        expect(route.request().method()).toBe("GET");
        reads++;
        return json(route, {
          schema: "evidence-source/v1",
          format: "docx",
          original_name: "notes.docx",
          original_url: `/v1/artifacts/${artifactId}/download`,
          preview_url: `/v1/artifacts/${artifactId}#page=1`,
          normalized_page: 1,
          accuracy: "ambiguous",
          origin_locators: [{ paragraph: 2 }],
          label: "轉換後第 1 頁",
        });
      },
    );
    await page.goto(mapPath);
    await page.getByRole("button", { name: "適應畫面", exact: true }).click();
    await page.getByRole("button", { name: "教材概念：Stack", exact: true }).click();
    await page.getByRole("button", { name: "查看第 1 頁來源", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "教材來源", exact: true });
    await expect(dialog).toContainText("轉換後第 1 頁");
    await expect(dialog).toContainText("可能對應多處");
    await expect(dialog.getByRole("link", { name: "下載原檔" })).toHaveAttribute(
      "href",
      `/v1/artifacts/${artifactId}/download`,
    );
    await expect(dialog.getByRole("link", { name: "開啟 PDF 來源頁" })).toHaveAttribute(
      "href",
      `/v1/artifacts/${artifactId}#page=1`,
    );
    await expect(dialog).toBeInViewport();
    expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(
      true,
    );
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: "概念詳情", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "查看第 1 頁來源", exact: true })).toBeFocused();
    expect(reads).toBe(1);
  });
