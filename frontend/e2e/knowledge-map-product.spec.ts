import { expect, test, type Page } from "@playwright/test";

import {
  mapRevision,
  mapView,
  materialId,
  runId,
  runView,
} from "./fixtures/learning";

async function routes(page: Page) {
  await page.route("**/v1/session/refresh", (route) => route.fulfill({ status: 204 }));
  await page.route(`**/v1/material-processing-runs/${runId}`, (route) =>
    route.fulfill({ status: 200, json: runView() }));
  await page.route("**/v1/materials/*/knowledge-maps/**", (route) =>
    route.fulfill({ status: 200, json: mapView() }));
}

test("Knowledge Map renders the grounded document tree without semantic edges", async ({ page }) => {
  await routes(page);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}`);
  await expect(page.getByRole("heading", { name: "知識地圖", exact: true })).toBeVisible();
  await expect(page.locator(".react-flow__node")).toHaveCount(5);
  await expect(page.locator(".react-flow__edge")).toHaveCount(4);
  await expect(page.locator(".concept-flow-edge.is-structural")).toHaveCount(4);
  await expect(page.locator(".react-flow__controls")).toBeVisible();
  await expect(page.getByText(/關係詳情|先備關係|互相關聯/)).toHaveCount(0);

  await page.getByLabel("焦點概念").selectOption({ label: "目標概念" });
  await expect(page.locator(".concept-flow-node.is-focus")).toContainText("目標概念");
  const viewport = page.locator(".react-flow__viewport");
  const beforeZoom = await viewport.getAttribute("style");
  await page.locator(".react-flow__controls-zoomin").click();
  await expect.poll(() => viewport.getAttribute("style")).not.toBe(beforeZoom);

  await page.getByRole("button", { name: /收合 1 個概念/ }).first().click();
  await expect(page.locator(".react-flow__node")).toHaveCount(4);
  await page.getByRole("button", { name: /展開 1 個概念/ }).first().click();
  await expect(page.locator(".react-flow__node")).toHaveCount(5);

  await page.getByRole("tab", { name: "學習順序" }).click();
  await expect(page.locator(".learning-path li")).toHaveCount(2);
  await page.getByRole("button", { name: /目標概念/ }).click();
  await expect(page.getByRole("tab", { name: "概念地圖" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".concept-flow-node.is-focus")).toContainText("目標概念");
});

test("mobile fallback keeps sections keyboard-accessible", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await routes(page);
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-maps/${encodeURIComponent(mapRevision)}`);
  await expect(page.getByLabel("教材概念階層清單")).toBeVisible();
  await expect(page.getByRole("button", { name: /收合/ }).first()).toBeVisible();
  const fallback = page.getByLabel("教材概念階層清單");
  await expect(fallback.getByRole("heading", { name: "先備概念" })).toBeVisible();
  await expect(fallback.getByRole("heading", { name: "目標概念" })).toBeVisible();
});
