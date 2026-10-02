import { expect, test, type Page } from "@playwright/test";
import { materialId, runId, structureRevision, structureView, mockKnowledgeMapApi, json } from "../fixtures/knowledge-map";

const mapPath = `/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`;
const cardsPath = `/v1/materials/${materialId}/knowledge-structures/${encodeURIComponent(structureRevision)}/concept-cards`;
const literal = "if (count != 0) {\n    value = 1.0 / count; // required condition\n}\nE=m*c^2; threshold <= 0.001; '\\0'\n" + "const long_literal = '" + "x".repeat(600) + "';";

function project(view: ReturnType<typeof structureView>, ids: string[]) {
  const cards = view.concepts.filter(c => ids.includes(c.concept_id));
  const relations = view.relations.filter(r => ids.includes(r.source_concept_id) || ids.includes(r.target_concept_id)).map(r => ({
    ...r,
    source_label: view.concepts.find(c => c.concept_id === r.source_concept_id)!.label,
    target_label: view.concepts.find(c => c.concept_id === r.target_concept_id)!.label,
    evidence: r.evidence_refs.map(id => view.concepts.flatMap(c => c.claims.flatMap(c => c.evidence)).find(e => e.evidence_id === id)!),
  }));
  return {
    schema: "concept-cards/v1",
    selection: {
      material_id: materialId, content_material_id: view.material_id, knowledge_structure_revision: view.knowledge_structure_revision,
      concept_ids: cards.map(c => c.concept_id), claim_ids: [...new Set(cards.flatMap(c => c.claims.map(c => c.claim_id)))],
      relation_ids: relations.map(r => r.relation_id), policy: "manual-published-order/v1",
    },
    source_resolver: view.source_resolver, status: view.status, excluded_pages: view.excluded_pages, cards, relations,
  };
}

async function setup(page: Page, view = structureView()) {
  await mockKnowledgeMapApi(page, view);
  const writes: string[] = [];
  page.on("request", request => {
    if (new URL(request.url()).pathname.startsWith("/v1/") && request.method() !== "GET" && !request.url().includes("session/refresh")) writes.push(request.url());
  });
  await page.route(`**${cardsPath}?*`, route => {
    expect(route.request().method()).toBe("GET");
    const ids = new URL(route.request().url()).searchParams.getAll("concept_id");
    return json(route, project(view, ids));
  });
  await page.goto(mapPath);
  await page.getByRole("button", {name: "選取觀念圖卡"}).click();
  return writes;
}

async function open(page: Page, labels = ["Stack"]) {
  for (const label of labels) await page.getByRole("checkbox", {name: new RegExp(label)}).check();
  await page.getByRole("button", {name: "開啟圖卡", exact: true}).click();
  await expect(page.getByRole("heading", {name: "已發布關係"})).toBeVisible();
}

test("single and multi selection use keyboard, preserve direction and exact sources without writes", async ({page}) => {
  const writes = await setup(page);
  const checkbox = page.getByRole("checkbox", {name: "Stack"});
  await expect(page.getByRole("button", {name: "開啟圖卡", exact: true})).toBeDisabled();
  await checkbox.focus(); await page.keyboard.press("Space");
  await expect(checkbox).toBeChecked();
  await expect(page.getByText("已選取", {exact:true})).toBeVisible();
  await page.getByRole("button", {name:"開啟圖卡", exact:true}).click();
  const dialog = page.getByRole("dialog", {name:"觀念圖卡", exact:true});
  await expect(dialog.getByRole("article", {name:"Stack 觀念圖卡"})).toContainText("A stack follows LIFO order.");
  await expect(dialog).toContainText("來源概念：Stack → 目標概念：Array");
  await expect(dialog).toContainText("Stack must be learned before Array traversal.");
  await expect(dialog).toContainText("此關係包含未選取的概念");
  await dialog.locator(".card-evidence summary").first().click();
  await dialog.getByRole("button", {name:"查看第 1 頁來源"}).click();
  const source = page.getByRole("dialog", {name:"教材來源", exact:true});
  await expect(source.getByRole("link", {name:"開啟 PDF 來源頁"})).toHaveAttribute("href", /#page=1$/);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await page.getByRole("button", {name:"調整選取"}).click();
  await page.getByRole("checkbox", {name:/Array/}).check();
  await page.getByRole("button", {name:"開啟圖卡", exact:true}).click();
  await expect(dialog.locator(".concept-card-grid h3")).toHaveText(["Stack", "Array"]);
  await expect(dialog.getByText("此關係包含未選取的概念。", {exact:true})).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", {name:"選取觀念圖卡"})).toBeFocused();
  await expect(page).toHaveURL(new RegExp("knowledge-structures/"));
  expect(writes).toEqual([]);
});

for (const width of [1440, 390]) {
  test(`unrelated cards, quality, long prose and code remain readable at ${width}px`, async ({page}) => {
    await page.setViewportSize({width, height:844});
    const view = structureView();
    view.relations = [];
    view.status = {processing:"partial", quality:"needs_review", decision:"review", reason_codes:["CLAIMS_REJECTED"]};
    Object.assign(view, {excluded_pages:[{page_ref:`page:sha256:${"3".repeat(64)}`, page:3, stage:"evidence", reason_code:"NO_USABLE_EVIDENCE"}]});
    view.concepts[0].claims[0].text = literal;
    view.concepts[0].claims[0].evidence[0].kind = "code";
    view.concepts[0].claims[0].evidence[0].quote = literal;
    const prose = "必要條件不能省略；數值 0.001 與否定皆須保留。".repeat(40);
    view.concepts[1].claims[0].text = prose;
    const extra = structuredClone(view.concepts[1].claims[0]);
    extra.claim_id = `claim:sha256:${"f".repeat(64)}`;
    extra.text = "Second published claim.";
    view.concepts[1].claims.push(extra);
    const writes = await setup(page, view);
    await open(page, ["Array", "Stack"]);
    const dialog = page.getByRole("dialog", {name:"觀念圖卡", exact:true});
    await expect(dialog.locator(".concept-card-grid h3")).toHaveText(["Stack", "Array"]);
    await expect(dialog).toContainText("needs_review");
    await expect(dialog).toContainText("CLAIMS_REJECTED");
    await expect(dialog.getByText("CLAIMS_REJECTED", {exact:true})).toBeVisible();
    await expect(dialog.getByText("第 3 頁未納入：NO_USABLE_EVIDENCE。請回查原教材。", {exact:true})).toBeVisible();
    await expect(dialog).toContainText("所選概念之間沒有已發布關係");
    expect(await dialog.locator(".card-literal").first().textContent()).toBe(literal);
    expect(await dialog.locator(".card-text").filter({hasText:prose}).textContent()).toBe(prose);
    await dialog.getByText("教材重點 2（共 2 項）", {exact:true}).click();
    await expect(dialog.getByText(extra.text, {exact:true})).toBeVisible();
    const fits = await dialog.evaluate(e => e.scrollWidth <= e.clientWidth + 1 && e.getBoundingClientRect().right <= innerWidth);
    expect(fits).toBe(true);
    const pre = dialog.locator(".card-literal").first();
    expect(await pre.evaluate(e => e.scrollWidth > e.clientWidth)).toBe(true);
    await pre.focus(); await page.keyboard.press("ArrowRight");
    await expect.poll(() => pre.evaluate(e => e.scrollLeft)).toBeGreaterThan(0);
    await page.screenshot({path:`test-results/concept-cards-${width}.png`});
    await page.keyboard.press("Escape");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(writes).toEqual([]);
  });
}

test("explicit contrast is shown only as its canonical relation and reason", async ({page}) => {
  const view = structureView();
  view.relations[0].type = "contrast";
  view.relations[0].learner_reason = "Published contrast reason.";
  await setup(page, view); await open(page, ["Stack", "Array"]);
  const relations = page.getByRole("region", {name:"已發布關係"});
  await expect(relations).toContainText("對照（contrast）");
  await expect(relations).toContainText("Published contrast reason.");
  await expect(relations).not.toContainText("優點");
});

test("unavailable revision and source failures are explicit and retryable", async ({page}) => {
  await setup(page);
  let unavailable = true;
  await page.route(`**${cardsPath}?*`, route => unavailable ? route.fulfill({status:404, json:{schema:"api-error/v1", request_id:runId, reason_code:"RESOURCE_NOT_FOUND", retryable:false, message:"Request could not be completed."}}) : route.fallback());
  await page.getByRole("checkbox", {name:"Stack"}).check();
  await page.getByRole("button", {name:"開啟圖卡", exact:true}).click();
  await expect(page.getByRole("alert")).toContainText("若版本已不可用");
  await expect(page.locator(".concept-card-grid")).toHaveCount(0);
  unavailable = false;
  await page.getByRole("button", {name:"重試開啟圖卡"}).click();
  await expect(page.locator(".concept-card-grid")).toBeVisible();
  await page.route("**/evidence/*/source", route => route.fulfill({status:404, json:{schema:"api-error/v1", request_id:runId, reason_code:"RESOURCE_NOT_FOUND", retryable:false, message:"Request could not be completed."}}));
  await page.locator(".card-evidence summary").first().click();
  await page.getByRole("button", {name:"查看第 1 頁來源"}).click();
  await expect(page.getByRole("alert")).toContainText("找不到這筆資料");
});

test("insufficient evidence stays visible", async ({page}) => {
  const view = structureView();
  view.relations = [];
  view.status.quality = "needs_review";
  view.concepts[0].claims[0].evidence = [];
  await setup(page, view); await open(page);
  await expect(page.getByText("證據不足：此項目沒有可回查的來源。", {exact:true})).toBeVisible();
  await expect(page.locator(".card-quality")).toContainText("needs_review");
});

test("an absent relation reason is disclosed without replacement text", async ({page}) => {
  const view = structureView();
  view.relations[0].learner_reason = "";
  await setup(page, view); await open(page);
  await expect(page.getByText("教材未提供關係理由。", {exact:true})).toBeVisible();
  await expect(page.getByRole("region", {name:"已發布關係"})).toContainText("先備（prerequisite）");
});

for (const action of ["close", "navigation", "account"] as const) {
  test(`late card response is discarded after ${action}`, async ({page}) => {
    await setup(page);
    let release!: () => void;
    const gate = new Promise<void>(resolve => {release = resolve;});
    let requested = false;
    await page.route(`**${cardsPath}?*`, async route => {
      requested = true; await gate;
      await json(route, project(structureView(), [structureView().concepts[0].concept_id]));
    });
    await page.getByRole("checkbox", {name:"Stack"}).check();
    await page.getByRole("button", {name:"開啟圖卡", exact:true}).click();
    await expect.poll(() => requested).toBe(true);
    await expect(page.getByText("正在讀取固定版本圖卡…", {exact:true})).toBeVisible();
    if (action === "close") {
      await page.getByRole("button", {name:"返回知識地圖"}).click();
      await page.getByRole("button", {name:"選取觀念圖卡"}).click();
    } else if (action === "navigation") {
      await page.evaluate(() => {history.pushState(null,"","/materials"); dispatchEvent(new PopStateEvent("popstate"));});
    } else {
      await page.evaluate(() => {const channel = new BroadcastChannel("studydy-account"); channel.postMessage("identity-changed"); channel.close();});
      await expect(page).toHaveURL(/\/login$/);
    }
    release();
    await expect(page.locator(".concept-card-grid")).toHaveCount(0);
    if (action === "close") await expect(page.getByRole("checkbox", {name:"Stack"})).not.toBeChecked();
    else await expect(page.getByRole("dialog", {name:"觀念圖卡", exact:true})).toHaveCount(0);
  });
}
