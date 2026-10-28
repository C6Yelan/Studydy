import { answerPaper } from "../fixtures/answer-paper";
import { expect, test, type Page } from "@playwright/test";
import type { AssessmentSetView } from "../../src/api/contracts";
import { readRoute } from "../../src/app/routes";
import { studyLayoutFixture } from "../fixtures/study-layout.mjs";

async function preparationLayout(page: Page, width: number) {
  await page.locator(".study-material-summary > summary").click();
  await expect(page.locator(".current-concept-card")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

async function inlinePreparing(page: Page, phase = "preparing") {
  await expect(page.locator(".study-learning-grid")).toHaveClass(new RegExp(`is-${phase}-mode`));
  await expect(page.locator(".current-concept-card, .study-rail")).toHaveCount(0);
  await expect(page.locator(".study-session-page")).toHaveCount(1);
  await expect(
    page.locator(
      ".study-current-action > .assessment-set-panel > .assessment-set-header.is-preparing",
    ),
  ).toBeVisible();
  expect(readRoute(new URL(page.url()).pathname).route.name).toBe("study-session");
  await expect(page.getByRole("button", { name: /取消本組測驗|取消本輪/ })).toHaveCount(0);
  await expect(page.locator(".assessment-set-panel")).not.toContainText(
    /verified|generating|worker|verifier|model|正在準備第|出題與檢查|排入/i,
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

for (const viewport of [
  { width: 1366, height: 768 },
  { width: 390, height: 844 },
]) {
  test(`study preparation, six questions, results and history at ${viewport.width}px`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const fixture = await studyLayoutFixture(page);
    await fixture.open();
    const start = page.getByRole("button", { name: "開始學習", exact: true });
    await expect(start).toBeEnabled();
    await preparationLayout(page, viewport.width);
    await start.scrollIntoViewIfNeeded();
    await expect(start).toBeInViewport();
    const sources = page.getByRole("region", { name: "教材來源", exact: true }).getByRole("button");
    await expect(sources).toHaveCount(1);
    await sources.click();
    const source = page.getByRole("dialog", { name: "教材來源", exact: true });
    await expect(source.getByRole("link", { name: "開啟 PDF 來源頁" })).toHaveAttribute(
      "href",
      /#page=1$/,
    );
    await page.keyboard.press("Escape");
    await expect(sources).toBeFocused();
    await page.locator(".study-material-summary > summary").click();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    fixture.setPreparedCount(0);
    await page.route("**/study-start", async (route) => {
      await gate;
      await route.fallback();
    });
    await start.click();
    await expect(page.getByRole("button", { name: "開始學習", exact: true })).toBeDisabled();
    release();
    await inlinePreparing(page);
    const bar = page.getByRole("progressbar", { name: "準備進度" });
    await expect(page.getByText("已準備 0 / 6 題", { exact: true })).toBeVisible();
    await expect(bar).not.toHaveAttribute("aria-valuenow");
    fixture.setPreparedCount(2);
    await expect(bar).toHaveAttribute("aria-valuenow", "2");
    await expect(bar).toHaveAttribute("aria-valuemax", "6");
    await expect(bar).toHaveAttribute("aria-valuetext", "已準備 2 / 6 題");
    const card = page.locator(".assessment-set-header.is-preparing");
    await expect(card.locator(".preparation-note")).toHaveText(
      "完成後會自動顯示題目；也可以先離開，稍後再回來。",
    );
    expect((await card.boundingBox())!.width).toBeCloseTo(
      (await page.locator(".study-workspace").boundingBox())!.width,
      0,
    );
    await expect(card.getByRole("button", { name: "查看知識地圖", exact: true })).toBeVisible();
    const preparingUrl = page.url();
    // 輪詢延遲時，resume 內的完整題組已可呈現進度，不閃現教材與 rail。
    let releaseRead!: () => void;
    const readGate = new Promise<void>((resolve) => {
      releaseRead = resolve;
    });
    await page.route("**/v1/study-sessions/*/assessment-sets/*", async (route) => {
      await readGate;
      await route.fallback();
    });
    await page.addInitScript(() => {
      Object.assign(window, { readingFlash: false });
      new MutationObserver(() => {
        if (document.querySelector(".current-concept-card, .study-rail"))
          Object.assign(window, { readingFlash: true });
      }).observe(document, { childList: true, subtree: true });
    });
    await page.goto("about:blank");
    await page.goto(preparingUrl);
    await inlinePreparing(page);
    expect(
      await page.evaluate(() => (window as unknown as { readingFlash: boolean }).readingFlash),
    ).toBe(false);
    await expect(bar).toHaveAttribute("aria-valuenow", "2");
    await expect(bar).toHaveAttribute("aria-valuemax", "6");
    releaseRead();
    await expect(bar).toHaveAttribute("aria-valuenow", "2");
    await expect(bar).toHaveAttribute("aria-valuemax", "6");
    await expect(page).toHaveURL(preparingUrl);
    fixture.setPreparedCount(6);
    await expect(bar).toHaveAttribute("aria-valuenow", "6");
    fixture.setStage("ready");
    await expect(page.locator(".assessment-set-item")).toHaveCount(1);
    await expect(page.locator(".study-learning-grid")).toHaveClass(/is-question-mode/);
    await expect(page.locator(".current-concept-card")).not.toBeVisible();
    const readyResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/workspace?") &&
        response.request().method() === "GET",
    );
    await page.reload();
    const readySet: AssessmentSetView = (await (await readyResponse).json()).selected_set;
    await expect(page.locator(".assessment-set-item")).toHaveCount(1);
    // 最後一題選第四個選項，刻意不同於 fixture 預載結果的第二個選項。
    const choices = [0, 0, 0, 0, 0, 3];
    const submission = page.waitForRequest((request) => request.url().endsWith("/submissions"));
    await answerPaper(page, choices);
    await page.getByRole("button", { name: "交卷", exact: true }).click();
    const submitted = await submission;
    expect(submitted.method()).toBe("POST");
    expect(submitted.headers()["idempotency-key"]).toMatch(/\S/);
    expect(submitted.postDataJSON()).toEqual({
      schema: "assessment-set-submission/v1",
      expected_set_version: readySet.set_version,
      answers: readySet.items.map((item, index) => ({
        assessment_revision: item.assessment!.assessment_revision,
        question_id: item.assessment!.question_id,
        selected_option_id: item.assessment!.options[choices[index]].option_id,
      })),
    });
    await expect(page.locator(".study-learning-grid")).toHaveClass(/is-result-mode/);
    await expect(page.locator(".current-concept-card")).not.toBeVisible();
    await expect(page.locator(".feedback-card")).toHaveCount(6);
    await page.getByRole("complementary", { name: "作答紀錄" }).locator("li button").last().click();
    await expect(page).toHaveURL(new RegExp(fixture.historyPath.split("/").at(-1)! + "$"));
    await expect(page.locator(".study-learning-grid")).toHaveClass(/is-result-mode/);
    expect(
      fixture.requests.filter((request) => request.path.endsWith("/study-start")),
    ).toHaveLength(1);
    expect(
      fixture.requests.filter((request) => request.path.endsWith("/submissions")),
    ).toHaveLength(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  });

  test(`study retry and partial publish stay in the assessment panel at ${viewport.width}px`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const fixture = await studyLayoutFixture(page, "partial_ready");
    await fixture.open();
    const publish = page.getByRole("button", { name: "先做已準備的 4 題", exact: true });
    await expect(publish).toBeEnabled();
    await inlinePreparing(page, "intervention");
    await page.getByRole("button", { name: "再試一次", exact: true }).click();
    await expect(page.getByText("已準備 2 / 6 題", { exact: true })).toBeVisible();
    await inlinePreparing(page);
    fixture.setStage("partial_ready");
    await expect(publish).toBeVisible();
    await publish.click();
    await expect(page.locator(".study-learning-grid")).toHaveClass(/is-question-mode/);
    await expect(page.locator(".assessment-set-item")).toHaveCount(1);
    expect(fixture.requests.filter((request) => request.path.endsWith("/retry"))).toHaveLength(1);
    expect(
      fixture.requests.filter((request) => request.path.endsWith("/publish-partial")),
    ).toHaveLength(1);
    await answerPaper(page, [0, 0, 0, 0]);
    await page.getByRole("button", { name: "交卷", exact: true }).click();
    await expect(page.locator(".feedback-card")).toHaveCount(4);
    await expect(page.locator(".assessment-cycle")).toContainText("本輪有未作答或未檢測的重點。 未作答 0 個重點 · 未檢測 2 個重點。");
  });
}

test("generation failure and no-safe keep clear states and readable material", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  const fixture = await studyLayoutFixture(page, "failed");
  await fixture.open();
  await expect(page.getByRole("button", { name: "再試一次", exact: true })).toBeVisible();
  await inlinePreparing(page, "intervention");
  fixture.setStage("no-safe");
  await fixture.open();
  await expect(page.getByRole("heading", { name: "此概念目前無法提供可靠檢測", exact: true })).toBeVisible();
  await page.locator(".study-material-summary > summary").click();
  await expect(page.locator(".current-concept-card")).toBeVisible();
});

test("create failure restores entry and retry retains its idempotency key", async ({ page }) => {
  const fixture = await studyLayoutFixture(page);
  const keys: string[] = [];
  await page.route("**/study-start", async (route) => {
    keys.push(route.request().headers()["idempotency-key"]);
    if (keys.length === 1)
      return route.fulfill({
        status: 503,
        json: {
          schema: "api-error/v1",
          request_id: "00000000-0000-4000-8000-000000000099",
          reason_code: "STORAGE_UNAVAILABLE",
          retryable: true,
          message: "Request could not be completed.",
        },
      });
    return route.fallback();
  });
  await fixture.open();
  const start = page.getByRole("button", { name: "開始學習", exact: true });
  await start.click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(start).toBeEnabled();
  await expect(page.getByRole("heading", { name: "尚未開始這個概念" })).toBeVisible();
  await start.click();
  await expect(page.getByText("已準備 2 / 6 題", { exact: true })).toBeVisible();
  await inlinePreparing(page);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
});

test("in-progress set offers submission without cancellation", async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "in_progress");
  await fixture.open();
  await expect(page.locator(".assessment-set-item")).toHaveCount(1);
  await expect(page.getByRole("button", { name: /取消本組測驗|取消本輪/ })).toHaveCount(0);
  await answerPaper(page, [0, 0, 0, 0, 0, 0]);
});
