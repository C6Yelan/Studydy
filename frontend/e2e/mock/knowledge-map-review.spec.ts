import { expect, test, type Page, type Locator } from "@playwright/test";
import {
  materialId,
  runId,
  sessionId,
  artifactId,
  structureRevision,
  secondConcept,
  firstClaim,
  structureView,
  run,
  session,
  progress,
  json,
  mockKnowledgeMapApi,
  workspaceView,
  learningMap,
  mockLearningMapApi,
} from "../fixtures/knowledge-map";

const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const progressPath = `/v1/study-sessions/${sessionId}/progress`;

async function mockOwnedReview(
  page: Page,
  view: ReturnType<typeof structureView>,
  readProgress: () => typeof progress,
) {
  await mockKnowledgeMapApi(page, view, readProgress);
  await page.route(`**/v1/materials/${materialId}`, (route) => {
    expect(route.request().method()).toBe("GET");
    return json(route, {
      schema: "material-library-item/v1",
      material_id: materialId,
      source_artifact_id: artifactId,
      display_name: "Data structures.pdf",
      size_bytes: 100,
      created_at: run.created_at,
      latest_attempt: run,
      available_structures: [
        {
          run_id: runId,
          knowledge_structure_revision: structureRevision,
          created_at: run.created_at,
          status: "succeeded",
        },
      ],
      study_sessions: [
        { ...session(), run_id: runId, current_concept_id: readProgress().current_concept_id },
      ],
    });
  });
}

test("複習重點 resumes the selected review concept through the existing study action", async ({
  page,
}) => {
  await mockOwnedReview(page, structureView(), () => ({
    ...progress,
    concept_states: progress.concept_states.map((state, index) =>
      index ? state : { ...state, status: "needs_review", weak_claim_ids: [firstClaim] },
    ),
  }));
  const writes: string[] = [];
  page.on("request", (request) => {
    if (
      new URL(request.url()).pathname.startsWith("/v1/study-sessions") &&
      request.method() !== "GET"
    )
      writes.push(request.url());
  });
  await page.goto(mapPath);
  await page.getByRole("tab", { name: "複習重點", exact: true }).click();
  await expect(page.locator(".review-context")).toContainText("Stack");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "繼續這個概念", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
  await expect(page.getByRole("heading", { name: "Stack", level: 1, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "開始本輪 1 題", exact: true })).toBeEnabled();
  expect(writes).toEqual([]);
});

for (const viewport of [
  { width: 1536, height: 1024 },
  { width: 1100, height: 800 },
  { width: 390, height: 844 },
]) {
  test(`review workspace weak claims sources and study target at ${viewport.width}px`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const view = structureView();
    const longText = "教材完整說明，保留必要的條件與定義。".repeat(30);
    const seed = view.concepts[1].claims[0];
    view.concepts[1].claims = Array.from({ length: 4 }, (_, i) => ({
      ...seed,
      claim_id: `claim:sha256:${String(i + 1).repeat(64)}`,
      text: i === 0 ? longText : `教材重點 ${i + 1}`,
      evidence: seed.evidence.map((item) => ({
        ...item,
        source_id: artifactId,
        source_name: "Synthetic.pdf",
        normalized_page: 2,
      })),
    }));
    view.concepts[1].claims[0].evidence.push({
      ...view.concepts[1].claims[0].evidence[0],
      evidence_id: `evidence:sha256:${"9".repeat(64)}`,
    });
    const state = structuredClone(progress);
    state.concept_states.forEach((item, i) =>
      Object.assign(item, {
        status: "needs_review",
        attempts: 3,
        correct_answers: 1,
        latest_is_correct: false,
        weak_claim_ids: i
          ? [view.concepts[i].claims[0].claim_id, view.concepts[i].claims[2].claim_id]
          : [view.concepts[i].claims[0].claim_id],
      }),
    );
    await mockOwnedReview(page, view, () => state);
    const writes: { method: string; path: string; body: unknown }[] = [];
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (path.startsWith("/v1/study-sessions") && request.method() !== "GET")
        writes.push({ method: request.method(), path, body: request.postDataJSON() });
    });
    await page.route(`**/v1/study-sessions/${sessionId}/focus`, (route) => {
      expect(route.request().method()).toBe("POST");
      state.current_concept_id = secondConcept;
      state.next_action.target_concept_id = secondConcept;
      state.next_action.target_claim_id = view.concepts[1].claims[0].claim_id;
      return json(route, { ...session(), current_concept_id: secondConcept });
    });
    await page.goto(mapPath);
    await page.getByRole("tab", { name: "複習重點", exact: true }).click();
    const list = page.getByRole("navigation", { name: "需要複習的概念" });
    await expect(list.getByRole("button")).toHaveCount(2);
    await expect(list).not.toContainText(longText);
    const url = page.url();
    const array = list.getByRole("button", { name: /Array/ });
    await array.focus();
    await page.keyboard.press("Enter");
    await expect(array).toHaveAttribute("aria-current", "true");
    await expect(array).toBeFocused();
    expect(page.url()).toBe(url);
    expect(writes).toEqual([]);
    await expect(page.locator(".review-context")).toHaveText("Array");
    await expect(page.locator(".review-workspace .map-learning-badge")).toHaveCount(0);
    await expect(page.locator(".review-list button").first()).toHaveText("Stack");
    await expect(array).toHaveText("Array");
    await expect(page.locator(".review-workspace")).not.toContainText(
      /最近一次作答|已掌握|看過重點後/,
    );
    await expect(page.locator(".review-actions dl")).toHaveCount(0);
    await expect(page.locator(".review-actions button")).toHaveCount(2);
    await expect(page.locator(".review-points > ol > li")).toHaveCount(2);
    await expect(page.locator(".review-points > ol > li > p")).toHaveText([longText, "教材重點 3"]);
    await expect(page.locator(".review-full-content")).toHaveCount(0);
    await expect(page.getByText("查看完整教材重點", { exact: true })).toHaveCount(0);
    const points = page.locator(".review-points > ol > li");
    const expectSourcePage = async (source: Locator, pageNumber: number) => {
      await expect(source).toHaveCount(1);
      await source.click();
      await expect(
        page
          .getByRole("dialog", { name: "教材來源" })
          .getByRole("link", { name: "開啟 PDF 來源頁" }),
      ).toHaveAttribute("href", `/v1/artifacts/${artifactId}#page=${pageNumber}`);
      await page.keyboard.press("Escape");
      await expect(source).toBeFocused();
    };
    for (const point of await points.all()) await expectSourcePage(point.getByRole("button"), 2);
    await list.getByRole("button", { name: "Stack", exact: true }).click();
    await expect(page.locator(".review-points > ol > li")).toHaveCount(1);
    await expect(page.locator(".review-points > ol > li > p")).toHaveText(
      "A stack follows LIFO order.",
    );
    await expectSourcePage(points.first().getByRole("button"), 1);
    await array.click();
    await expect(page.locator("#map-panel-review .primary-button")).toHaveCount(1);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    const boxes = await Promise.all(
      [".review-list", ".review-context", ".review-actions", ".review-points"].map((selector) =>
        page.locator(selector).boundingBox(),
      ),
    );
    const [listBox, contextBox, actionsBox, pointsBox] = boxes.map((box) => box!);
    if (viewport.width >= 1200) {
      expect(listBox.x + listBox.width).toBeLessThan(contextBox.x);
      expect(contextBox.x + contextBox.width).toBeLessThan(actionsBox.x);
    } else if (viewport.width > 900) {
      expect(listBox.x + listBox.width).toBeLessThan(contextBox.x);
      expect(contextBox.x).toBeCloseTo(pointsBox.x, 0);
      expect(pointsBox.x).toBeCloseTo(actionsBox.x, 0);
      expect(contextBox.y + contextBox.height).toBeLessThanOrEqual(pointsBox.y);
      expect(pointsBox.y + pointsBox.height).toBeLessThanOrEqual(actionsBox.y);
    } else {
      expect(listBox.y + listBox.height).toBeLessThanOrEqual(contextBox.y);
      expect(contextBox.y + contextBox.height).toBeLessThanOrEqual(pointsBox.y);
      expect(pointsBox.y + pointsBox.height).toBeLessThanOrEqual(actionsBox.y);
    }
    await page.getByRole("button", { name: "繼續這個概念", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/study-sessions/${sessionId}$`));
    await expect(page.getByRole("heading", { name: "Array", level: 1, exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "開始本輪 4 題", exact: true })).toBeEnabled();
    expect(writes).toEqual([
      {
        method: "POST",
        path: `/v1/study-sessions/${sessionId}/focus`,
        body: { schema: "study-session-focus/v1", current_concept_id: secondConcept },
      },
    ]);
  });
}

for (const count of [0, 30])
  test(`review ${count} weak concepts preserves membership and list scrolling`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1536, height: 1024 });
    const view = workspaceView(32, true);
    const current = view.concepts[0].concept_id;
    const state = structuredClone(progress);
    Object.assign(state, {
      current_concept_id: current,
      next_action: {
        ...progress.next_action,
        target_concept_id: current,
        target_claim_id: view.concepts[0].claims[0].claim_id,
      },
      concept_states: view.concepts.map((concept, i) => ({
        ...progress.concept_states[0],
        concept_id: concept.concept_id,
        label: concept.label,
        status: i < count ? "needs_review" : "learning",
        weak_claim_ids: i < count ? [concept.claims[0].claim_id] : [],
      })),
    });
    await mockOwnedReview(page, view, () => state);
    await page.goto(mapPath);
    await page.getByRole("tab", { name: "複習重點", exact: true }).click();
    if (!count) {
      await expect(page.locator(".review-empty")).toContainText("目前沒有需要複習的概念");
      await expect(page.locator(".review-workspace")).toHaveCount(0);
    } else {
      const list = page.locator(".review-list ul");
      await expect(list.getByRole("button")).toHaveText(
        view.concepts.slice(0, count).map((concept) => concept.label),
      );
      await expect.poll(() => list.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
      await list.hover();
      await page.mouse.wheel(0, 100_000);
      await expect.poll(() => list.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
      const last = list.getByRole("button", { name: view.concepts[count - 1].label, exact: true });
      await expect(last).toBeInViewport();
      await last.click();
      await expect(page.locator(".review-context h3")).toHaveText(view.concepts[count - 1].label);
      await expect(page.locator(".review-points > ol > li")).toHaveCount(1);
      await expect(page.locator(".review-points > ol > li > p")).toHaveText(
        view.concepts[count - 1].claims[0].text,
      );
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
    }
  });

for (const viewport of [
  { width: 1536, height: 1024 },
  { width: 390, height: 844 },
]) {
  // 摘要是否存在與進度狀態無關；三欄／兩欄重排由上方版面案例驗證。
  for (const progressState of ["available", "absent", "error"] as const)
    test(`map has no global learning summary with ${progressState} progress at ${viewport.width}px`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await mockLearningMapApi(page, learningMap(8), progressState !== "absent");
      if (progressState === "error")
        await page.route(`**${progressPath}`, (route) =>
          json(
            route,
            {
              schema: "api-error/v1",
              request_id: sessionId,
              reason_code: "STORAGE_UNAVAILABLE",
              retryable: true,
              message: "Request could not be completed.",
            },
            503,
          ),
        );
      await page.goto(mapPath);
      if (progressState === "available")
        await expect(page.locator(".map-learning-badge").first()).toBeVisible();
      for (const tabName of ["概念地圖", "複習重點"]) {
        const tab = page.getByRole("tab", { name: tabName, exact: true });
        await tab.click();
        await expect(tab).toHaveAttribute("aria-selected", "true");
        const panel = page.getByRole("tabpanel");
        await expect(panel).toHaveAttribute(
          "aria-labelledby",
          (await tab.getAttribute("id")) as string,
        );
        await expect(page.getByText("學習摘要", { exact: true })).toHaveCount(0);
        await expect(
          page.locator(".map-summary-container, .map-learning-summary, .summary-progress"),
        ).toHaveCount(0);
        await expect(page.locator(".map-facts")).toContainText("8概念");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
        const adjacent = await page
          .locator(".map-tabs")
          .evaluate((el) => el.nextElementSibling?.classList.contains("map-content"));
        expect(adjacent).toBe(true);
        if (tabName === "複習重點") {
          if (progressState === "available")
            await expect(page.locator(".review-workspace")).toBeVisible();
          else {
            await expect(page.locator(".review-empty")).toContainText(
              progressState === "error" ? "暫時無法顯示複習重點" : "練習後，幫你找出複習方向",
            );
            await expect(page.locator(".review-workspace")).toHaveCount(0);
          }
        }
      }
      if (progressState === "absent") {
        await page.getByRole("button", { name: "查看學習導覽", exact: true }).click();
        await expect(page.getByRole("tab", { name: "概念地圖", exact: true })).toHaveAttribute(
          "aria-selected",
          "true",
        );
      }
      await page.getByRole("tab", { name: "複習重點", exact: true }).focus();
      await page.keyboard.press("Home");
      await expect(page.getByRole("tab", { name: "概念地圖", exact: true })).toBeFocused();
    });
}

test("review starts its small progress read in parallel and never shows a false empty state", async ({
  page,
}) => {
  const view = structureView();
  const state = structuredClone(progress);
  Object.assign(state.concept_states[0], { status: "needs_review", weak_claim_ids: [firstClaim] });
  await mockOwnedReview(page, view, () => state);
  let releaseMap!: () => void;
  let releaseProgress!: () => void;
  let progressReads = 0;
  let resumes = 0;
  const mapGate = new Promise<void>((resolve) => {
    releaseMap = resolve;
  });
  const progressGate = new Promise<void>((resolve) => {
    releaseProgress = resolve;
  });
  await page.route(
    `**/v1/materials/${materialId}/knowledge-structures/${encodeURIComponent(structureRevision)}`,
    async (route) => {
      await mapGate;
      await json(route, view);
    },
  );
  await page.route(`**${progressPath}`, async (route) => {
    progressReads++;
    await progressGate;
    await json(route, state);
  });
  page.on("request", (request) => {
    if (request.url().includes("/resume?")) resumes++;
  });
  await page.goto(mapPath);
  await expect.poll(() => progressReads).toBe(1);
  releaseMap();
  await page.getByRole("tab", { name: "複習重點", exact: true }).click();
  await expect(page.getByText("正在讀取複習重點…", { exact: true })).toBeVisible();
  await expect(page.locator(".review-empty")).toHaveCount(0);
  releaseProgress();
  await expect(page.locator(".review-list button")).toHaveCount(1);
  expect(resumes).toBe(0);
});
