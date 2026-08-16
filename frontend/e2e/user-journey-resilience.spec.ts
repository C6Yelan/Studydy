import { expect, test } from "@playwright/test";

const controlUrl = process.env.STUDYDY_E2E_CONTROL_URL!;
const controlToken = process.env.STUDYDY_E2E_CONTROL_TOKEN!;

function safePdf(): Buffer {
  const commands = "BT /F1 16 Tf 40 540 Td (Study Flow Topic) Tj 0 -40 Td /F1 11 Tf (Grounded study flow explanation.) Tj ET";
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 600] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    `<< /Length ${Buffer.byteLength(commands)} >>\nstream\n${commands}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
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

test("真backend restart後由URL恢復terminal、Map、Assessment與Learning State", async ({ page }) => {
  test.setTimeout(80_000);
  await page.goto("/");
  await page.getByLabel("資料結構").check();
  await page.getByLabel("選擇 PDF 教材").setInputFiles({
    name: "hardening.pdf",
    mimeType: "application/pdf",
    buffer: safePdf(),
  });
  const runCreated = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/v1/material-processing-runs" && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "上傳並開始處理" }).click();
  const createdRun = await (await runCreated).json();
  await expect(page.getByRole("heading", { name: /教材處理完成|教材部分完成/ })).toBeVisible({ timeout: 40_000 });
  const terminal = await page.evaluate(async (createdRunId) => {
    const response = await fetch(`/v1/material-processing-runs/${createdRunId}`);
    return response.json();
  }, createdRun.run_id);
  const exactBinding = terminal.output_binding;
  const assessmentPath = `/materials/${terminal.material_id}/runs/${terminal.run_id}/assessments/${encodeURIComponent(exactBinding.assessment_revision)}`;
  const mapPath = `/materials/${terminal.material_id}/runs/${terminal.run_id}/maps/${encodeURIComponent(exactBinding.knowledge_map_revision)}/paths/${encodeURIComponent(exactBinding.learning_path_revision)}`;
  const runPath = `/materials/${terminal.material_id}/runs/${terminal.run_id}`;

  await page.getByRole("button", { name: "開始學習評量" }).click();
  await page.getByRole("radio").last().check();
  await page.getByRole("button", { name: "送出作答" }).click();
  await expect(page.getByRole("heading", { name: "你的學習狀態" })).toBeVisible();
  const statePath = new URL(page.url()).pathname;

  const restartResponse = await fetch(`${controlUrl}/restart-backend`, {
    method: "POST",
    headers: { "X-Studydy-E2E-Token": controlToken },
    signal: AbortSignal.timeout(20_000),
  });
  expect(restartResponse.status).toBe(204);

  await page.reload();
  await expect(page.getByRole("heading", { name: "你的學習狀態" })).toBeVisible();
  await expect(page).toHaveURL(statePath);
  await page.goto(assessmentPath);
  await expect(page.getByRole("heading", { name: "依教材 Evidence 完成單選題" })).toBeVisible();
  await page.goto(mapPath);
  await expect(page.getByText("KNOWLEDGE MAP")).toBeVisible();
  await page.goto(runPath);
  await expect(page.getByRole("heading", { name: /教材處理完成|教材部分完成/ })).toBeVisible();
});
