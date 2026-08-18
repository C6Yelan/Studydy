import { expect, test } from "@playwright/test";


function safePdf(pageCount = 1): Buffer {
  const objects: string[] = [];
  const pageObjectNumbers = Array.from({ length: pageCount }, (_, index) => index + 3);
  const contentObject = 3 + pageCount;
  const fontObject = contentObject + 1;
  objects.push("<< /Type /Catalog /Pages 2 0 R >>");
  objects.push(`<< /Type /Pages /Kids [${pageObjectNumbers.map((number) => `${number} 0 R`).join(" ")}] /Count ${pageCount} >>`);
  for (let index = 0; index < pageCount; index += 1) {
    objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 600] /Resources << /Font << /F1 ${fontObject} 0 R >> >> /Contents ${contentObject} 0 R >>`);
  }
  const commands = "BT /F1 16 Tf 40 540 Td (Study Flow Topic) Tj 0 -40 Td /F1 11 Tf (Grounded study flow explanation.) Tj ET";
  objects.push(`<< /Length ${Buffer.byteLength(commands)} >>\nstream\n${commands}\nendstream`);
  objects.push("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>");
  const offsets: number[] = [];
  let pdf = "%PDF-1.4\n";
  for (const [index, object] of objects.entries()) {
    offsets.push(Buffer.byteLength(pdf));
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
  }
  const crossReferenceOffset = Buffer.byteLength(pdf);
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  pdf += offsets.map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("");
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${crossReferenceOffset}\n%%EOF\n`;
  return Buffer.from(pdf);
}


test("real upload、create v2、poll、Map v2 與 session PDF page locator", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");
  await page.getByLabel("選擇 PDF 教材").setInputFiles({
    name: "material.pdf",
    mimeType: "application/pdf",
    buffer: safePdf(),
  });
  const created = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/v1/material-processing-runs"
      && response.request().method() === "POST"
      && response.status() === 202,
  );
  await page.getByRole("button", { name: "上傳並分析完整教材" }).click();
  const run = await (await created).json();
  expect(run.schema).toBe("material-processing-run/v2");
  await expect(page.getByRole("heading", { name: /處理完成，等待複核|部分頁面已排除，等待複核/ })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "開啟複核地圖" }).click();
  await expect(page.getByRole("heading", { name: "教材概念與 Evidence 複核" })).toBeVisible();
  await expect(page.getByText("第 1 頁 · paragraph")).toBeVisible();
  await page.evaluate(() => {
    const originalOpen = window.open.bind(window);
    Object.assign(window, { __studydyOpenedUrl: "" });
    window.open = (url, target, features) => {
      Object.assign(window, { __studydyOpenedUrl: String(url) });
      return originalOpen(url, target, features);
    };
  });
  const sourceResponse = page.context().waitForEvent("response", { predicate: (response) =>
    new URL(response.url()).pathname.startsWith("/v1/artifacts/") && response.status() === 200,
  });
  await page.getByRole("button", { name: "開啟來源 PDF 第 1 頁" }).click();
  await sourceResponse;
  const openedUrl = await page.evaluate(() => String((window as Window & { __studydyOpenedUrl: string }).__studydyOpenedUrl));
  expect(openedUrl).toContain("/v1/artifacts/");
  expect(openedUrl).toContain("#page=1");
});


test("real 33-page run reaches truthful failed terminal", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");
  await page.getByLabel("選擇 PDF 教材").setInputFiles({
    name: "too-many-pages.pdf",
    mimeType: "application/pdf",
    buffer: safePdf(33),
  });
  await page.getByRole("button", { name: "上傳並分析完整教材" }).click();
  await expect(page.getByRole("heading", { name: "教材處理失敗" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("MATERIAL_PAGE_LIMIT_EXCEEDED")).toBeVisible();
});


test("old downstream API and UI routes fail closed", async ({ page }) => {
  await page.goto("/");
  const apiStatus = await page.evaluate(async () => {
    const response = await fetch("/v1/materials/00000000-0000-4000-8000-000000000000/assessments/old");
    return response.status;
  });
  expect(apiStatus).toBe(404);
  await page.goto("/materials/00000000-0000-4000-8000-000000000000/runs/00000000-0000-4000-8000-000000000000/assessments/old");
  await expect(page.getByRole("heading", { name: "上傳完整教材，逐頁建立複核地圖" })).toBeVisible();
  await expect(page).toHaveURL("/");
});
