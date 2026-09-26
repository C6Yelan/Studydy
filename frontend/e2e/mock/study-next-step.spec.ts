import { expect, test, type Page } from "@playwright/test";
import { studyLayoutFixture } from "../fixtures/study-layout.mjs";

const unavailable = {
  schema: "api-error/v1",
  request_id: "00000000-0000-4000-8000-000000000099",
  reason_code: "STORAGE_UNAVAILABLE",
  retryable: true,
  message: "Request could not be completed.",
};

async function holdGuidanceApply(page: Page) {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/guidance/apply", async (route) => {
    await gate;
    await route.fallback();
  });
  return release;
}

for (const width of [1366, 390]) {
  test(`advance uses guidance and stays in the session at ${width}px`, async ({ page }) => {
    await page.setViewportSize({
      width,
      height: width === 390 ? 844 : 768,
    });
    const label = "使用者端與多階段網路服務中的請求及回應角色";
    const fixture = await studyLayoutFixture(page, "completed", {
      navigation: true,
      kind: "remediation",
      wrong: [],
      nextLabel: label,
    });
    await fixture.open();
    const next = page.getByRole("button", { name: `下一個觀念：${label}`, exact: true });
    await expect(next).toBeVisible();
    await expect(
      page.getByRole("button", { name: /查看原始初篩題組|回到地圖繼續學習/ }),
    ).toHaveCount(0);
    await expect(page.locator(".assessment-cycle .primary-button")).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    const release = await holdGuidanceApply(page);
    const before = page.url();
    await next.click();
    await expect(
      page.getByRole("button", { name: "正在前往下一個觀念…", exact: true }),
    ).toBeDisabled();
    await expect(page).toHaveURL(before);
    release();
    await expect(page).toHaveURL(new RegExp(fixture.path + "$"));
    await expect(page.getByRole("heading", { name: label, level: 1, exact: true })).toBeVisible();
    await expect(page.locator(".current-concept-card")).toContainText(label);
    await expect(page.getByRole("button", { name: "開始本輪 6 題", exact: true })).toBeVisible();
    const requests = fixture.requests.filter((r) => r.path.endsWith("/guidance/apply"));
    expect(requests).toHaveLength(1);
    expect(JSON.parse(requests[0].body!)).toEqual({
      schema: "guidance-apply/v1",
      guidance_revision: "learner-guidance:sha256:" + "1".padStart(64, "0"),
    });
  });
}

test("complete applies authority before showing completion", async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "completed", {
    navigation: true,
    nextAction: "complete",
    wrong: [],
  });
  await fixture.open();
  const release = await holdGuidanceApply(page);
  await page.getByRole("button", { name: "完成本次學習", exact: true }).click();
  await expect(page.getByRole("button", { name: "正在完成…", exact: true })).toBeDisabled();
  await expect(page.getByRole("heading", { name: "學習已完成", exact: true })).toHaveCount(0);
  release();
  await expect(page.getByRole("heading", { name: "學習已完成", exact: true })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(fixture.path + "$"));
  await expect(page.getByRole("button", { name: /下一個觀念|完成本次學習/ })).toHaveCount(0);
});

for (const [name, stage, scenario] of [
  ["wrong points", "completed", { wrong: [2] }],
  ["active remediation", "ready", { kind: "remediation" }],
  ["preparing remediation", "preparing", { kind: "remediation" }],
  ["another active set", "completed", { wrong: [], preparingHistory: true }],
] as const)
  test(`${name} does not offer advance`, async ({ page }) => {
    const fixture = await studyLayoutFixture(page, stage, { navigation: true, ...scenario });
    await fixture.open();
    await expect(page.getByRole("button", { name: /下一個觀念|完成本次學習/ })).toHaveCount(0);
    if (name === "wrong points")
      await expect(page.getByRole("button", { name: "開始補強 1 題", exact: true })).toBeVisible();
  });

test("failed apply keeps result and allows retry with the same guidance", async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "completed", { navigation: true, wrong: [] });
  await fixture.open();
  const before = page.url();
  const revisions: string[] = [];
  await page.route("**/guidance/apply", async (route) => {
    revisions.push(route.request().postDataJSON().guidance_revision);
    if (revisions.length === 1)
      return route.fulfill({
        status: 503,
        json: unavailable,
      });
    await route.fallback();
  });
  await page.getByRole("button", { name: "下一個觀念：使用者端", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page).toHaveURL(before);
  await page.getByRole("button", { name: "下一個觀念：使用者端", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "使用者端", level: 1, exact: true }),
  ).toBeVisible();
  expect(revisions[1]).toBe(revisions[0]);
});

test("stale guidance refreshes the decision without automatic apply", async ({ page }) => {
  const fixture = await studyLayoutFixture(page, "completed", { navigation: true, wrong: [] });
  await fixture.open();
  const before = page.url();
  const revisions: string[] = [];
  await page.route("**/guidance/apply", async (route) => {
    revisions.push(route.request().postDataJSON().guidance_revision);
    if (revisions.length === 1) {
      fixture.setGuidance("complete", 2);
      return route.fulfill({
        status: 409,
        json: { ...unavailable, reason_code: "LEARNER_GUIDANCE_STALE" },
      });
    }
    await route.fallback();
  });
  await page.getByRole("button", { name: "下一個觀念：使用者端", exact: true }).click();
  await expect(page.getByRole("button", { name: "完成本次學習", exact: true })).toBeVisible();
  await expect(page).toHaveURL(before);
  expect(revisions).toHaveLength(1);
  await page.getByRole("button", { name: "完成本次學習", exact: true }).click();
  await expect(page.getByRole("heading", { name: "學習已完成", exact: true })).toBeVisible();
  expect(revisions[1]).toBe("learner-guidance:sha256:" + "2".padStart(64, "0"));
});

test("incomplete cycle follows the backend next action without claiming a pass", async ({
  page,
}) => {
  const fixture = await studyLayoutFixture(page, "completed", {
    navigation: true,
    wrong: [],
    unavailable: 1,
  });
  await fixture.open();
  await expect(page.getByText("本輪有未作答或未檢測的重點。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "下一個觀念：使用者端", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "使用者端", level: 1, exact: true }),
  ).toBeVisible();
});
