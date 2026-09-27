import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';

test.skip(!process.env.STUDYDY_E2E_REMOVAL_MATERIAL, 'Requires isolated failed-analysis fixture');

test('remove failed initial source, refresh, upload a replacement and publish', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill('learner_test@example.com');
  await page.getByLabel('密碼', { exact: true }).fill('Synthetic test password 42');
  await page.getByRole('button', { name: '登入', exact: true }).click();
  await expect(page.getByRole('heading', { name: '歡迎回來！', level: 1, exact: true })).toBeVisible();
  const material = process.env.STUDYDY_E2E_REMOVAL_MATERIAL!;
  await page.goto(`/materials/${material}/sources`);
  const before = await (await page.request.get(`/v1/materials/${material}`)).json();
  const oldRun = before.latest_attempt.run_id;
  await expect(page.locator('.source-row')).toHaveCount(1);
  await page.getByRole('listitem', { name: 'Initial.pdf', exact: true })
    .getByRole('button', { name: '移除', exact: true }).click();
  await expect(page.locator('.source-row')).toHaveCount(0);
  await page.reload();
  await expect(page.locator('.source-row')).toHaveCount(0);
  const picker = page.getByLabel('選擇新增教材', { exact: true });
  await expect(picker).toBeEnabled();
  await picker.setInputFiles({
    name: 'Recovery.pdf', mimeType: 'application/pdf',
    buffer: readFileSync(process.env.STUDYDY_E2E_REMOVAL_PDF!),
  });
  await page.getByRole('button', { name: /上傳.*教材/ }).click();
  const start = page.getByRole('button', { name: '開始分析教材', exact: true });
  await expect(start).toBeEnabled({ timeout: 20000 });
  await start.click();
  await expect(page.getByRole('heading', { name: '教材整理完成', exact: true })).toBeVisible({ timeout: 20000 });
  const after = await (await page.request.get(`/v1/materials/${material}/sources`)).json();
  expect(after.sources).toHaveLength(1);
  expect(after.sources[0].original_name).toBe('Recovery.pdf');
  expect(after.sources[0].included).toBe(true);
  const old = await (await page.request.get(`/v1/material-processing-runs/${oldRun}`)).json();
  expect(old.status).toBe('failed');
  expect(old.error_code).toBe('SEMANTIC_OUTPUT_TRUNCATED');
  await page.getByRole('button', { name: '開啟知識地圖', exact: true }).click();
  await expect(page).toHaveURL(/\/knowledge-structures\//);
  await expect(page.locator('.react-flow__node').first()).toBeVisible();
  if (process.env.STUDYDY_FIX_SCREENSHOT) await page.screenshot({ path: process.env.STUDYDY_FIX_SCREENSHOT, fullPage: true });
});
