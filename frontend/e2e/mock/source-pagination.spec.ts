import { expect, test } from '@playwright/test';
import { materialId, structureView, mockKnowledgeMapApi, json } from '../fixtures/knowledge-map';

const researchId = '55555555-5555-4555-8555-555555555555';
const topicId = '77777777-7777-4777-8777-777777777777';
const source = (i: number) => ({ id: `source-${i}`, kind: i % 2 ? 'paper' : 'web', title: `Agent 來源 ${i}`,
  authors: 'Fixture', year: 2026, url: 'https://example.org/agent', doi: null, license: 'CC-BY-4.0',
  license_url: null, version: '1', eligible: true, reason: 'fixture', state: 'candidate' });

for (const mode of ['research', 'topic']) test(`${mode} paginates sources and retains selection across pages`, async ({ page }) => {
  await mockKnowledgeMapApi(page, structureView());
  const work: any = { research_id: researchId, material_id: mode === 'research' ? materialId : null,
    query: 'Agent', mode: 'review', approved_scope: { title: 'Agent', goals: ['工具呼叫'], topics: ['Agent'] }, status: 'selecting', candidates: Array.from({ length: 23 }, (_, i) => source(i)),
    selection: [], cursor: null, error_code: null, run_id: null, is_current_revision: true };
  let requests = 0;
  await page.route(`**/v1/research/${researchId}`, r => json(r, work));
  await page.route(`**/v1/research/${researchId}/actions`, r => { requests++; return json(r, work); });
  const topic = () => ({ topic_id: topicId, request: 'Agent', version: 3, status: 'approved', error_code: null,
    proposal: { title: 'Agent', goals: ['工具呼叫'], topics: ['Agent'] }, research_id: researchId, research: work });
  await page.route('**/v1/topics', r => json(r, { topics: [] }));
  await page.route(`**/v1/topics/${topicId}`, r => json(r, topic()));
  await page.goto(mode === 'research' ? `/materials/${materialId}/research/${researchId}` : `/topics/${topicId}`);
  const pager = page.getByRole('navigation', { name: '來源分頁' });
  await expect(page.locator('.research-candidates article')).toHaveCount(10);
  await page.getByRole('checkbox', { name: '選取 Agent 來源 0', exact: true }).check();
  await pager.getByRole('button', { name: '第 2 頁', exact: true }).click();
  await page.getByRole('checkbox', { name: '選取 Agent 來源 19', exact: true }).check();
  await pager.getByRole('button', { name: '上一頁', exact: true }).click();
  await expect(page.getByRole('checkbox', { name: '選取 Agent 來源 0', exact: true })).toBeChecked();
  await pager.getByRole('button', { name: '第 3 頁', exact: true }).click();
  await expect(pager.getByRole('button', { name: '第 3 頁', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('.research-candidates article')).toHaveCount(3);
  await expect(pager.getByRole('button', { name: '下一頁', exact: true })).toBeDisabled();
  await page.getByRole('checkbox', { name: '選取 Agent 來源 22', exact: true }).check();
  // 研究結束後只能選取現有來源，不再有手動補查的按鈕，翻頁與勾選也不送出請求。
  await expect(page.getByRole('button', { name: '依缺口繼續研究', exact: true })).toHaveCount(0);
  expect(requests).toBe(0);
  await page.getByLabel('只看已選', { exact: true }).check();
  await expect(page.locator('.research-candidates article')).toHaveCount(3);
  await expect(page.locator('.research-candidates input:checked')).toHaveCount(3);
  await page.getByLabel('只看已選', { exact: true }).uncheck();
  await pager.getByRole('button', { name: '第 3 頁', exact: true }).click();
  await page.getByLabel('來源類型', { exact: true }).selectOption('paper');
  await expect(pager.getByRole('button', { name: '第 1 頁', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(pager).toContainText('已載入 11 份');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await pager.scrollIntoViewIfNeeded();
});
