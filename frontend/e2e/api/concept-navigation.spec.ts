import { expect, test, type Page } from "@playwright/test";

test.skip(
  process.env.STUDYDY_E2E_CONCEPT_NAVIGATION !== "true",
  "Requires isolated concept navigation fixture",
);
const navigationData = JSON.parse(process.env.STUDYDY_E2E_NAVIGATION_DATA ?? "{}");
const origin = process.env.STUDYDY_E2E_BASE_URL ?? "http://127.0.0.1:4173";
const mapPath = `/materials/${navigationData.material}/runs/${navigationData.run}/knowledge-structures/${encodeURIComponent(navigationData.revision)}`;
const studyPath = `${mapPath}/study-sessions/${navigationData.session}`;

async function openConcept(page: Page, label: string) {
  await page.getByRole("button", { name: "學習導覽", exact: true }).click();
  await page
    .getByRole("navigation", { name: "學習導覽", exact: true })
    .getByRole("button")
    .filter({ has: page.getByText(label, { exact: true }) })
    .click();
  await expect(page.getByRole("dialog", { name: "概念詳情", exact: true })).toBeVisible();
}

test("unfinished A does not block B and switching back resumes A", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 900 });
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill("learner_test@example.com");
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "歡迎回來！", level: 1, exact: true }),
  ).toBeVisible();
  await page.goto(studyPath);
  await page.getByRole("button", { name: "開始本輪 1 題", exact: true }).click();
  await expect(page.getByText("0 / 1 題", { exact: true })).toBeVisible();
  const firstSetUrl = page.url();
  const firstSetId = firstSetUrl.split("/").at(-1)!;

  await page
    .getByRole("navigation", { name: "學習工作區導覽" })
    .getByRole("button", { name: "知識地圖", exact: true })
    .click();
  await openConcept(page, "Other topic");
  await page
    .getByRole("region", { name: "學習入口" })
    .getByRole("button", { name: "從這個概念繼續", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Other topic", level: 1, exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "開始本輪 1 題", exact: true })).toBeVisible();
  const assessmentSetsPath = `/v1/study-sessions/${navigationData.session}/assessment-sets`;
  const pendingSetResponse = await page.request.get(`${assessmentSetsPath}/${firstSetId}`);
  const pendingSet = await pendingSetResponse.json();
  expect(pendingSet.status).toBe("preparing");

  // 另一視窗剛建立相同觀念的題組：接續它，而不是重建或停在衝突提示。
  const createSecondSetResponse = await page.request.post(assessmentSetsPath, {
    headers: { Origin: origin, "Idempotency-Key": "another-window" },
    data: { schema: "assessment-set-create/v1", target_concept_id: navigationData.second },
  });
  expect(createSecondSetResponse.status()).toBe(202);
  const secondSetId = (await createSecondSetResponse.json()).set_id;
  await page.getByRole("button", { name: "開始本輪 1 題", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/assessment-sets/${secondSetId}$`));
  const setListResponse = await page.request.get(assessmentSetsPath);
  const setList = await setListResponse.json();
  expect(new Set(setList.active_set_ids)).toEqual(new Set([firstSetId, secondSetId]));

  await page.request.post("/v1/__test/navigation/release", { headers: { Origin: origin } });
  await expect(page.locator(".assessment-set-item")).toHaveCount(1);
  const readySetResponse = await page.request.get(`${assessmentSetsPath}/${firstSetId}`);
  const readySet = await readySetResponse.json();
  expect(readySet.status).toBe("ready");
  await page.getByRole("radio", { name: /EXTERNAL/ }).check();
  await page.getByRole("button", { name: "交卷並查看結果", exact: true }).click();
  await expect(
    page.getByText("本輪檢測通過，僅代表這次檢測範圍的結果。", { exact: true }),
  ).toBeVisible();
  const unansweredSetResponse = await page.request.get(`${assessmentSetsPath}/${firstSetId}`);
  const unansweredSet = await unansweredSetResponse.json();
  expect(unansweredSet.answered_count).toBe(0);

  await page.getByRole("button", { name: "下一個觀念：Signals", exact: true }).click();
  await expect(page).toHaveURL(firstSetUrl);
  await expect(page.getByRole("radio", { name: /\bcode0\b/ })).toBeEnabled();
  const resumedSetResponse = await page.request.get(`${assessmentSetsPath}/${firstSetId}`);
  const resumedSet = await resumedSetResponse.json();
  expect(resumedSet.assessment_revisions).toEqual(readySet.assessment_revisions);
  expect(resumedSet.answered_count).toBe(0);
  await expect(page.getByText("題組狀態已更新，請重新讀取後繼續。", { exact: true })).toHaveCount(
    0,
  );
});
