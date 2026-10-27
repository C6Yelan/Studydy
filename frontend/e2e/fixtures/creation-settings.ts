import { expect, type Page } from "@playwright/test";
import type { StartingLevel } from "../../src/api/contracts";

export async function mockCreationSettings(page: Page) {
  const state = { level: null as StartingLevel | null, goal: null as { target_date: string; target_percent: 60 | 80 | 100 } | null,
    writes: [] as { method: string; path: string; body: any }[], failNext: false, published: false };
  await page.route(/\/v1\/materials\/[^/]+\/(starting-level|goal)(\?|$)/, route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method();
    const materialId = path.split('/')[3];
    if (method !== 'GET') {
      state.writes.push({ method, path, body: request.postDataJSON() });
      if (state.failNext || state.published && method === 'DELETE') {
        state.failNext = false;
        return route.fulfill({ status: state.published ? 409 : 503, json: { schema: 'api-error/v1', request_id: materialId,
          reason_code: state.published ? 'LEARNING_GOAL_PUBLISHED' : 'STORAGE_UNAVAILABLE', retryable: !state.published, message: 'Request could not be completed.' } });
      }
      if (path.endsWith('/starting-level')) { expect(method).toBe('PUT'); state.level = request.postDataJSON().starting_level; }
      else if (method === 'DELETE') state.goal = null;
      else { expect(method).toBe('PUT'); const body = request.postDataJSON(); state.goal = { target_date: body.target_date, target_percent: body.target_percent }; }
    }
    return route.fulfill({ json: path.endsWith('/starting-level')
      ? { schema: 'starting-level/v1', material_id: materialId, starting_level: state.level }
      : { schema: 'learning-goal/v2', material_id: materialId, knowledge_structure_revision: null, run_id: null,
        today: '2026-10-27', pending_goal: state.goal, goal: null } });
  });
  return state;
}
