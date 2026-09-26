import { expect, test, type Page } from "@playwright/test";

test.skip(
  process.env.STUDYDY_E2E_ASSESSMENT_SET !== "true",
  "Requires isolated assessment set API/DB fixture",
);
const data = JSON.parse(process.env.STUDYDY_E2E_SET_DATA ?? "{}");
const origin = process.env.STUDYDY_E2E_BASE_URL ?? "http://127.0.0.1:4173";
const studyPath = `/materials/${data.material}/runs/${data.run}/knowledge-structures/${encodeURIComponent(data.revision)}/study-sessions/${data.session}`;
const viewport = { width: 1536, height: 900 };

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill("learner_test@example.com");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "歡迎回來！", level: 1, exact: true }),
  ).toBeVisible();
}

const card = (page: Page, n: number) =>
  page.getByRole("article", { name: `第 ${n} 題`, exact: true });

test("whole paper submits once and restores persisted results after response loss", async ({
  page,
  browser,
}) => {
  await page.setViewportSize(viewport);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await login(page);
  await page.goto(studyPath);
  await page.getByRole("button", { name: "開始本輪 3 題", exact: true }).click();
  await expect(page).toHaveURL(/assessment-sets\/[0-9a-f-]+$/);
  const roundPath = new URL(page.url()).pathname;
  const setId = roundPath.split("/").at(-1)!;
  await expect(page.getByText("0 / 3 題", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "正在準備本輪練習", exact: true })).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "準備進度" })).toBeVisible();
  await page.reload();
  await expect(page.getByText("0 / 3 題", { exact: true })).toBeVisible();
  await page.request.post("/v1/__test/sets/release", { headers: { Origin: origin } });
  await expect(page.locator(".assessment-set-item")).toHaveCount(3);
  const endpoint = `/v1/study-sessions/${data.session}/assessment-sets/${setId}`;
  const snapshot = await (await page.request.get(endpoint)).json();
  expect(snapshot.target_concept_id).toBe(data.concept);
  expect(JSON.stringify(snapshot)).not.toMatch(
    /"correct_option_id"|"prepared_document"|"private_answer_document"|"generation_provenance"/,
  );
  await expect(page.locator(".feedback-card")).toHaveCount(0);
  const submit = page.getByRole("button", { name: "交卷並查看結果", exact: true });
  await expect(submit).toBeDisabled();
  await card(page, 1)
    .getByRole("radio", { name: /\bcode0\b/ })
    .check();
  await card(page, 2)
    .getByRole("radio", { name: /\bcode1\b/ })
    .check();
  await expect(submit).toBeDisabled();
  expect((await (await page.request.get(endpoint)).json()).answered_count).toBe(0);
  await page.reload();
  await expect(page.locator(".assessment-set-item")).toHaveCount(3);
  await expect(page.locator(".assessment-paper input:checked")).toHaveCount(0);
  const restored = await (await page.request.get(endpoint)).json();
  expect(restored.items.map((i: { assessment: unknown }) => i.assessment)).toEqual(
    snapshot.items.map((i: { assessment: unknown }) => i.assessment),
  );
  await card(page, 1)
    .getByRole("radio", { name: /wrong0a/ })
    .check();
  await card(page, 1)
    .getByRole("radio", { name: /\bcode0\b/ })
    .check();
  await card(page, 2)
    .getByRole("radio", { name: /\bcode1\b/ })
    .check();
  await card(page, 3)
    .getByRole("radio", { name: /wrong2a/ })
    .check();
  await expect(submit).toBeEnabled();
  let posts = 0;
  let release!: () => void;
  const submissions: { version: number; key: string }[] = [];
  // 交卷恢復情境由 Python fixture 指定，與畫面尺寸分開。
  const conflict = data.scenario === "version-conflict";
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/assessment-sets/*/submissions", async (route) => {
    posts++;
    const body = route.request().postDataJSON();
    expect(body.answers).toHaveLength(3);
    submissions.push({
      version: body.expected_set_version,
      key: route.request().headers()["idempotency-key"],
    });
    await gate;
    if (conflict && posts === 1) {
      const changed = await page.request.post(`/v1/__test/sets/${setId}/version`, {
        headers: { Origin: origin },
      });
      expect(changed.ok()).toBe(true);
      await route.continue();
      return;
    }
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    await route.abort("failed");
  });
  await submit.click();
  await expect(page.getByRole("button", { name: "正在交卷…", exact: true })).toBeDisabled();
  await expect(card(page, 1).getByRole("radio").first()).toBeDisabled();
  await expect(page.locator(".feedback-card")).toHaveCount(0);
  release();
  if (conflict) {
    await expect(
      page.getByText("題組已同步，答案選取已保留。確認後可再次交卷。", { exact: true }),
    ).toBeVisible();
    await expect(card(page, 1).getByRole("radio", { name: /\bcode0\b/ })).toBeChecked();
    await expect(card(page, 1).getByRole("radio", { name: /\bcode0\b/ })).toBeEnabled();
    await page.getByRole("button", { name: "重新交卷", exact: true }).click();
    await expect.poll(() => submissions.length).toBe(2);
    expect(submissions[1].version).toBeGreaterThan(submissions[0].version);
    expect(submissions[1].key).not.toBe(submissions[0].key);
  }
  await page.getByRole("button", { name: "查回交卷結果", exact: true }).click();
  await expect(page.locator(".feedback-card")).toHaveCount(3);
  await page.locator(".assessment-answer-review > summary").click();
  await expect(page.locator(".feedback-card").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "交卷並查看結果", exact: true })).toHaveCount(0);
  const done = await (await page.request.get(endpoint)).json();
  expect(done.status).toBe("completed");
  expect(done.answered_count).toBe(3);
  expect(done.passed_count).toBe(2);
  expect(posts).toBe(conflict ? 2 : 1);
  expect(errors).toEqual([]);
  const context = await browser.newContext({ viewport });
  const fresh = await context.newPage();
  await login(fresh);
  await fresh.goto(roundPath);
  await expect(fresh.locator(".feedback-card")).toHaveCount(3);
  await fresh.locator(".assessment-answer-review > summary").click();
  await expect(fresh.locator(".feedback-card").first()).toBeVisible();
  expect(
    (await (await fresh.request.get(endpoint)).json()).items.map(
      (i: { feedback: { answer_event_id: string } }) => i.feedback.answer_event_id,
    ),
  ).toEqual(
    done.items.map((i: { feedback: { answer_event_id: string } }) => i.feedback.answer_event_id),
  );
  const progress = await (
    await fresh.request.get(`/v1/study-sessions/${data.session}/progress`)
  ).json();
  expect(
    progress.concept_states.find((i: { concept_id: string }) => i.concept_id === data.concept)
      .status,
  ).not.toBe("mastered");
  await context.close();
});
