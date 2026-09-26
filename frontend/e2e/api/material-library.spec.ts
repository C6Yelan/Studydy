import { expect, test, type Page } from "@playwright/test";
import type { MaterialLibraryView } from "../../src/api/contracts";

const browserOrigin = process.env.STUDYDY_E2E_BASE_URL ?? "http://127.0.0.1:4173";

test.skip(process.env.STUDYDY_E2E_LIBRARY !== "true", "Requires the local library API/DB fixture");

async function login(page: Page, email: string) {
  await page.goto("/");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("密碼", { exact: true }).fill("Synthetic test password 42");
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "歡迎回來！", level: 1, exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: /^(教材庫|我的教材)$/, exact: true }).click();
  await expect(page.getByRole("heading", { name: "我的教材", exact: true })).toBeVisible();
}

test("fresh profiles reopen the current head and read exact historical versions", async ({
  browser,
}) => {
  const original = await browser.newContext();
  const page = await original.newPage();
  await login(page, "learner_test@example.com");
  const first = page.getByRole("article", { name: "堆疊講義.pdf", exact: true });
  await expect(first).toContainText("知識地圖建立失敗");
  await expect(page.getByText("B 的私人教材.pdf", { exact: true })).toHaveCount(0);
  await first.getByRole("button", { name: "開啟知識地圖", exact: true }).click();
  await expect(page.getByRole("button", { name: "教材概念：Stack", exact: true })).toBeVisible();
  const newerPath = new URL(page.url()).pathname;
  await page.reload();
  await expect(page.getByRole("button", { name: "教材概念：Stack", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "登出", exact: true }).click();
  await expect(page.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  await original.close();

  // 全新 cookie jar/storage，只用帳密和教材名稱導航，不注入 UUID 或已知網址。
  const fresh = await browser.newContext();
  const freshPage = await fresh.newPage();
  await login(freshPage, "learner_test@example.com");
  // 只允許公開登入身分提示；教材定位、內容與憑證仍不得存入 localStorage。
  expect(await freshPage.evaluate(() => Object.keys(localStorage))).toEqual([
    "studydy.session-hint",
  ]);
  expect(
    await freshPage.evaluate(() => JSON.parse(localStorage.getItem("studydy.session-hint")!)),
  ).toEqual({
    schema: "learner-identity/v1",
    learner_id: expect.any(String),
  });
  await freshPage.getByRole("button", { name: "開啟知識地圖", exact: true }).click();
  await expect(freshPage).toHaveURL(`${browserOrigin}${newerPath}`);
  await expect(
    freshPage.getByRole("button", { name: "教材概念：Stack", exact: true }),
  ).toBeVisible();
  // 工作區顯示目前 head；歷史版本仍須能由 API 精確讀取，不能以相同概念名稱冒充版本驗證。
  const library: MaterialLibraryView = await (
    await fresh.request.get(`${browserOrigin}/v1/materials`)
  ).json();
  const material = library.materials.find((item) => item.display_name === "堆疊講義.pdf")!;
  expect(material.available_structures).toHaveLength(2);
  const head = material.available_structures.find(
    (structure) => structure.knowledge_structure_revision === material.head_revision,
  )!;
  expect(head).toBeDefined();
  const headPath = `/materials/${material.material_id}/runs/${head.run_id}/knowledge-structures/${encodeURIComponent(head.knowledge_structure_revision)}`;
  expect(newerPath).toBe(headPath);
  for (const structure of material.available_structures) {
    const revision = structure.knowledge_structure_revision;
    const response = await fresh.request.get(
      `${browserOrigin}/v1/materials/${material.material_id}/knowledge-structures/${encodeURIComponent(revision)}`,
    );
    expect(response.status()).toBe(200);
    expect((await response.json()).knowledge_structure_revision).toBe(revision);
    const run = await fresh.request.get(
      `${browserOrigin}/v1/material-processing-runs/${structure.run_id}`,
    );
    expect(run.status()).toBe(200);
    expect((await run.json()).output_binding.knowledge_structure_revision).toBe(revision);
    await freshPage.goto(
      `/materials/${material.material_id}/runs/${structure.run_id}/knowledge-structures/${encodeURIComponent(revision)}`,
    );
    await expect(
      freshPage.getByRole("button", { name: "教材概念：Stack", exact: true }),
    ).toBeVisible();
    await expect(freshPage).toHaveURL(`${browserOrigin}${headPath}`);
  }
  await freshPage.getByRole("button", { name: /^(教材庫|我的教材)$/, exact: true }).click();
  const pdfUrl = `/v1/artifacts/${material.source_artifact_id}`;
  const pdf = await fresh.request.get(`${browserOrigin}${pdfUrl}`);
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["cache-control"]).toBe("private, no-store");
  expect((await pdf.body()).subarray(0, 4).toString()).toBe("%PDF");
  await freshPage
    .getByRole("article", { name: "堆疊講義.pdf", exact: true })
    .getByRole("button", { name: "開啟知識地圖", exact: true })
    .click();
  await expect(freshPage).toHaveURL(`${browserOrigin}${newerPath}`);
  await expect(
    freshPage.getByRole("button", { name: "教材概念：Stack", exact: true }),
  ).toBeVisible();
  await freshPage.getByRole("button", { name: "登出", exact: true }).click();
  await expect(freshPage.getByRole("heading", { name: "登入您的帳戶" })).toBeVisible();
  await login(freshPage, "library_b@example.com");
  await expect(
    freshPage.getByRole("article", { name: "B 的私人教材.pdf", exact: true }),
  ).toBeVisible();
  await expect(freshPage.getByText("堆疊講義.pdf", { exact: true })).toHaveCount(0);
  expect((await fresh.request.get(`${browserOrigin}${pdfUrl}`)).status()).toBe(404);
  await freshPage.goBack();
  await expect(freshPage.getByRole("button", { name: "教材概念：Stack", exact: true })).toHaveCount(
    0,
  );
  await fresh.close();
});
