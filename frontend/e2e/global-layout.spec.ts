import { expect, test } from "@playwright/test";

const id = "11111111-1111-4111-8111-111111111111";
for (const width of [1920, 1536, 1366, 1024, 900, 768, 390, 320]) {
  test(`standard frames center within main and preserve stats at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 1080 });
    const unexpected: string[] = [];
    await page.route("**/v1/**", route => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/v1/session/refresh") return route.fulfill({ json: { schema: "learner-identity/v1", learner_id: "33333333-3333-4333-8333-333333333333" } });
      if (path === "/v1/session") return route.fulfill({ json: { schema: "learner-identity/v1", learner_id: id } });
      if (path === "/v1/source-capabilities") return route.fulfill({json:{schema:"source-capabilities/v1",formats:[{extension:".pdf",media_type:"application/pdf",max_bytes:104857600}],quality_notice:"PDF"}});
      if (path === "/v1/materials") return route.fulfill({ json: { schema: "material-library/v1", materials: [] } });
      unexpected.push(path); return route.abort();
    });
    for (const path of ["/", "/materials", "/upload"]) {
      await page.goto(path);
      const frame = page.locator(".app-main > section");
      await expect(frame).toBeVisible();
      if (path === "/") await expect(page.getByRole("region", { name: "學習總覽" })).toBeVisible();
      const main = (await page.locator(".app-main").boundingBox())!, box = (await frame.boundingBox())!;
      expect(Math.abs((box.x - main.x) - (main.x + main.width - box.x - box.width))).toBeLessThanOrEqual(4);
      const usable = await page.locator(".app-main").evaluate(el => {
        const css = getComputedStyle(el);
        return el.getBoundingClientRect().width - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight);
      });
      expect(box.width).toBeCloseTo(Math.min(usable, 1600), 0);
      if (path === "/upload") expect((await page.locator(".file-drop").boundingBox())!.width).toBeLessThanOrEqual(880);
      expect((await frame.locator("h1").boundingBox())!.y).toBeCloseTo(box.y, 0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
      expect(await frame.locator("h1, h2, p, button, strong").evaluateAll(elements => elements.filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent))).toEqual([]);
      await page.screenshot({ path: info.outputPath(`${path.replaceAll("/", "") || "home"}.png`), fullPage: true });
    }
    expect(unexpected).toEqual([]);
  });
}

test("full-page failure uses the reading frame and keyboard retry without writes", async ({ page }, info) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  const writes: string[] = [];
  await page.route("**/v1/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/v1/session/refresh") return route.fulfill({ json: { schema: "learner-identity/v1", learner_id: "33333333-3333-4333-8333-333333333333" } });
    if (path === "/v1/session") return route.fulfill({ json: { schema: "learner-identity/v1", learner_id: id } });
    if (route.request().method() !== "GET") writes.push(path);
    return route.fulfill({ status: 503, json: { schema: "api-error/v1", request_id: id, reason_code: "STORAGE_UNAVAILABLE", retryable: true, message: "Unavailable" } });
  });
  await page.goto("/materials");
  const state = page.locator(".state-view--page");
  await expect(state.getByRole("heading", { level: 1 })).toHaveText("無法讀取教材");
  expect((await state.boundingBox())!.width).toBeLessThanOrEqual(880);
  await state.getByRole("button", { name: "重新讀取" }).focus(); await page.keyboard.press("Enter");
  await expect(state).toBeVisible(); expect(writes).toEqual([]);
  await page.screenshot({ path: info.outputPath("library-failure.png"), fullPage: true });
});
