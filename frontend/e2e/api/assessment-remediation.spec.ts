import { expect, test, type Page } from "@playwright/test";

test.skip(
  process.env.STUDYDY_E2E_REMEDIATION !== "true",
  "Requires isolated remediation API/DB fixture",
);
const data = JSON.parse(process.env.STUDYDY_E2E_REMEDIATION_DATA ?? "{}");
const study = `/materials/${data.material}/runs/${data.run}/knowledge-structures/${encodeURIComponent(data.revision)}/study-sessions/${data.session}`;
// 完整 API 流程只跑一個代表尺寸；手機與版面由 study-* mock 測試驗證。
const viewport = { width: 1536, height: 900 };
const passedMessage = "可檢測的重點皆已完成。";

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill("learner_test@example.com");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "歡迎回來！今天想探索哪座島？", exact: true, level: 1 }),
  ).toBeVisible();
}

const card = (page: Page, n: number) =>
  page.getByRole("article", { name: `第 ${n} 題`, exact: true });

test("wrong points directly form groups and survive lost create responses", async ({
  page,
  browser,
}) => {
  await page.setViewportSize(viewport);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await login(page);
  await page.goto(study);
  await page.getByRole("button", { name: "開始學習", exact: true }).click();
  await expect(page.locator(".assessment-set-item")).toHaveCount(1);
  const rootUrl = page.url();
  for (let n = 1; n <= 3; n++) {
    await card(page, n)
      .getByRole("radio", { name: n === 1 ? /\bcode0\b/ : new RegExp(`wrong${n - 1}a`) })
      .check();
    if (n < 3) await page.getByRole("button", { name: "下一題", exact: true }).click();
  }
  await page.getByRole("button", { name: "交卷", exact: true }).click();
  const reviews = page.locator(".feedback-card.is-incorrect .feedback-evidence");
  await expect(reviews).toHaveCount(2);
  await expect(reviews.nth(0)).toContainText("Signal 1 uses code1.");
  await expect(reviews.nth(1)).toContainText("Signal 2 uses code2.");
  await expect(page.getByRole("button", { name: "補強這 2 個重點", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "補強這 2 個重點", exact: true })).toBeVisible();
  let lostCreate = false;
  await page.route("**/assessment-sets/*/remediation", async (route) => {
    if (lostCreate) {
      await route.continue();
      return;
    }
    lostCreate = true;
    const response = await route.fetch();
    expect(response.status()).toBe(202);
    await route.abort();
  });
  // 第一次交卷會跳出新徽章（初次作答、第一份題組），先關閉才能繼續操作。
  await page.getByRole("dialog", { name: "獲得新徽章" }).getByRole("button", { name: "太好了" }).click();
  await page.getByRole("button", { name: "補強這 2 個重點", exact: true }).click();
  await expect(page.getByRole("button", { name: "重新讀取題組", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重新讀取題組", exact: true }).click();
  await page.getByRole("button", { name: "接續補強", exact: true }).click();
  await expect(page.locator(".assessment-set-item")).toHaveCount(1);
  expect(page.url()).not.toBe(rootUrl);
  await expect(page.locator(".question-paper-heading")).toContainText("Signals");
  await card(page, 1)
    .getByRole("radio", { name: /\bcode1\b/ })
    .check();
  await page.getByRole("button", { name: "下一題", exact: true }).click();
  await card(page, 2)
    .getByRole("radio", { name: /wrong2a/ })
    .check();
  await page.getByRole("button", { name: "交卷", exact: true }).click();
  await expect(page.getByRole("button", { name: "補強這 2 個重點", exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "補強這 2 個重點", exact: true }).click();
  await expect(page.locator(".assessment-set-item")).toHaveCount(1);
  await card(page,1).getByRole("radio",{name:/\bcode1\b/}).check();
  await page.getByRole("button", { name: "下一題", exact: true }).click();
  await card(page,2).getByRole("radio",{name:/\bcode2\b/}).check();
  await page.getByRole("button",{name:"交卷",exact:true}).click();
  await page.getByRole("button",{name:"補強這 1 個重點",exact:true}).click();
  await expect(page.locator(".assessment-set-item")).toHaveCount(1);
  // 上一次交卷讓 Signals 的錯誤率降到一半以下，進入補強題組時讀到新徽章（突破難點），先關閉提示。
  await page.getByRole("dialog", { name: "獲得新徽章" }).getByRole("button", { name: "太好了" }).click();
  const childUrl=page.url();
  await card(page,1).getByRole("radio",{name:/\bcode2\b/}).check();
  await page.getByRole("button",{name:"交卷",exact:true}).click();
  await expect(page.getByText(passedMessage,{exact:true})).toBeVisible();

  const context = await browser.newContext({ viewport });
  const fresh = await context.newPage();
  await login(fresh);
  await fresh.goto(childUrl);
  await expect(fresh.getByText(passedMessage, { exact: true })).toBeVisible();
  const progress = await (
    await fresh.request.get(`/v1/study-sessions/${data.session}/progress`)
  ).json();
  expect(progress.assessment_cycles[0].outcome).toBe("passed");
  expect(progress.event_watermark).toBe(8);
  expect(
    progress.concept_states.find((c: { concept_id: string }) => c.concept_id === data.concept)
      .completed_claim_ids.length,
  ).toBe(3);
  // 升級與徽章由先領取的瀏覽器顯示，兩個瀏覽器誰先領不固定；出現時關閉。
  await fresh.addLocatorHandler(fresh.locator("dialog.level-up"), async (dialog) => {
    await dialog.getByRole("button", { name: "太好了" }).click();
  }, { noWaitAfter: true });
  await fresh
    .getByRole("complementary", { name: "作答紀錄" })
    .locator("li button")
    .filter({ hasText: "初篩" })
    .click();
  await expect(fresh).toHaveURL(rootUrl);
  await expect(fresh.locator(".assessment-set-item")).toHaveCount(3);
  await fresh.getByRole("button", { name: "繼續下一個概念", exact: true }).click();
  await expect(fresh).toHaveURL(new RegExp(study + "/assessment-sets/"));
  await expect(
    fresh.locator(".question-paper-heading"),
  ).toBeVisible();
  await expect(fresh.locator(".assessment-set-item")).toHaveCount(1);
  expect(errors).toEqual([]);
  await context.close();
});
