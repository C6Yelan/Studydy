import { expect, test } from "@playwright/test";

test.skip(process.env.STUDYDY_E2E_CARDS !== "true", "Requires isolated concept cards fixture");
const cases = JSON.parse(process.env.STUDYDY_E2E_CARDS_DATA ?? "[]") as {
  material: string; run: string; revision: string; labels: string[];
}[];

test("published cards and exact sources work through the real API with and without a study session", async ({page}) => {
  await page.goto("/");
  await page.getByLabel("Email", {exact:true}).fill("learner_test@example.com");
  await page.getByLabel("密碼", {exact:true}).fill("Synthetic test password 42");
  await page.getByRole("button", {name:"登入", exact:true}).click();
  await expect(page.getByRole("heading", {name:"歡迎回來！", level:1, exact:true})).toBeVisible();
  for (const row of cases) {
    const mapPath = `/materials/${row.material}/runs/${row.run}/knowledge-structures/${encodeURIComponent(row.revision)}`;
    await page.goto(mapPath);
    await page.getByRole("button", {name:"選取觀念圖卡"}).click();
    for (const label of row.labels) await page.getByRole("checkbox", {name:label, exact:true}).check();
    const responseTask = page.waitForResponse(response => response.url().includes("/concept-cards?"));
    await page.getByRole("button", {name:"開啟圖卡", exact:true}).click();
    const response = await responseTask;
    expect(response.status()).toBe(200);
    const cards = await response.json();
    expect(cards.selection.material_id).toBe(row.material);
    expect(cards.selection.knowledge_structure_revision).toBe(row.revision);
    const dialog = page.getByRole("dialog", {name:"觀念圖卡", exact:true});
    await expect(dialog.locator(".concept-card-grid h3")).toHaveText(row.labels);
    expect(cards.cards.map((c: {label:string}) => c.label)).toEqual(row.labels);
    const mapResponse = await page.request.get(`/v1/materials/${row.material}/knowledge-structures/${encodeURIComponent(row.revision)}`);
    const canonical = await mapResponse.json();
    expect(cards.cards).toEqual(canonical.concepts.filter((c: {label:string}) => row.labels.includes(c.label)));
    expect(cards.status).toEqual(canonical.status);
    await dialog.locator(".card-evidence summary").first().click();
    const sourceTask = page.waitForResponse(response => response.url().includes("/evidence/") && response.url().endsWith("/source"));
    await dialog.locator(".card-evidence").first().getByRole("button").click();
    const sourceResponse = await sourceTask;
    expect(sourceResponse.status()).toBe(200);
    expect(decodeURIComponent(sourceResponse.url())).toContain(`/${row.revision}/evidence/${cards.cards[0].claims[0].evidence[0].evidence_id}/source`);
    const resolved = await sourceResponse.json();
    const sourceDialog = page.getByRole("dialog", {name:"教材來源", exact:true});
    await expect(sourceDialog.getByRole("link", {name:"開啟 PDF 來源頁"})).toHaveAttribute("href", resolved.preview_url);
    const pdf = await page.request.get(resolved.preview_url.split("#")[0]);
    expect(pdf.status()).toBe(200);
    expect(pdf.headers()["content-type"]).toContain("application/pdf");
    await page.keyboard.press("Escape");
    await page.getByRole("button", {name:"返回知識地圖"}).click();
    await expect(page).toHaveURL(new RegExp(`/knowledge-structures/${encodeURIComponent(row.revision)}$`));
  }
});
