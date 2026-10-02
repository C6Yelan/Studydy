import { expect, test } from "@playwright/test";
import { materialId, runId, structureRevision, structureView } from "../fixtures/knowledge-map";
import { apiPath, listPath, studyPath, cardSetId, savedSet, projectedSet, mockCardSets } from "../fixtures/card-sets";

for (const width of [1440,390]) {
  test(`card set management and one-card study are usable at ${width}px`, async ({page}) => {
    await page.setViewportSize({width,height:900});
    const {requests}=await mockCardSets(page);
    await page.goto(listPath);
    await expect(page.getByRole("heading",{name:"還沒有圖卡組"})).toBeVisible();
    await page.getByRole("button",{name:"＋ 建立圖卡組",exact:true}).click();
    await page.getByLabel("圖卡組名稱",{exact:true}).fill("期中複習");
    const search=page.getByLabel("選擇內容",{exact:true});
    await search.fill("Stack");
    await expect(page.getByRole("checkbox")).toHaveCount(1);
    await page.getByRole("checkbox",{name:"Stack",exact:true}).focus();await page.keyboard.press("Space");
    await search.fill("Array");await page.getByRole("checkbox",{name:"Array",exact:true}).check();
    await expect(page.getByText("已選 2 個概念",{exact:true})).toBeVisible();
    await expect(page.getByText("已選取",{exact:true})).toHaveCount(0);
    await page.getByRole("button",{name:"建立圖卡組",exact:true}).click();
    await expect(page).toHaveURL(new RegExp(`${studyPath}$`));
    const front=page.getByRole("button",{name:"翻卡：Stack，查看重點",exact:true});
    await expect(front).toBeVisible();
    await expect(page.getByText("A stack follows LIFO order.",{exact:true})).toHaveCount(0);
    await expect(page.locator(".flashcard-face")).toHaveCount(1);
    await front.focus();await page.keyboard.press("Enter");
    await expect(page.getByText("A stack follows LIFO order.",{exact:true})).toBeVisible();
    await expect(page.getByText("Stack must be learned before Array traversal.",{exact:true})).toHaveCount(0);
    await page.getByRole("button",{name:"查看教材來源",exact:true}).click();
    const sources=page.getByRole("dialog",{name:"圖卡教材來源",exact:true});
    await sources.getByRole("button",{name:"查看第 1 頁來源"}).click();
    await expect(page.getByRole("dialog",{name:"教材來源",exact:true}).getByRole("link",{name:"開啟 PDF 來源頁"})).toHaveAttribute("href",/#page=1$/);
    await page.keyboard.press("Escape");await page.keyboard.press("Escape");
    await expect(page.getByRole("button",{name:"查看教材來源",exact:true})).toBeFocused();
    await page.getByRole("button",{name:"下一張 →",exact:true}).click();
    await expect(page.getByRole("status",{name:"複習進度"})).toHaveText("第 2 / 2 張");
    await expect(page.getByRole("button",{name:"翻卡：Array，查看重點"})).toBeVisible();
    await page.reload();await expect(page.getByRole("status",{name:"複習進度"})).toHaveText("第 2 / 2 張");
    await page.getByRole("button",{name:"← 上一張",exact:true}).click();
    await expect(page.getByRole("status",{name:"複習進度"})).toHaveText("第 1 / 2 張");
    await page.getByRole("button",{name:"下一張 →",exact:true}).click();
    await page.getByRole("button",{name:"重新開始",exact:true}).click();
    await expect(page.getByRole("status",{name:"複習進度"})).toHaveText("第 1 / 2 張");
    await page.screenshot({path:`test-results/flashcard-front-${width}.png`});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.getByRole("button",{name:"下一張 →",exact:true}).click();
    await page.getByRole("button",{name:"完成複習",exact:true}).click();
    await page.getByRole("button",{name:"編輯",exact:true}).click();
    await page.getByLabel("圖卡組名稱",{exact:true}).fill("更新卡組");
    await page.getByRole("checkbox",{name:"Array",exact:true}).uncheck();
    await page.getByRole("button",{name:"儲存圖卡組",exact:true}).click();
    await expect(page.getByRole("status",{name:"複習進度"})).toHaveText("第 1 / 1 張");
    await page.getByRole("button",{name:"完成複習",exact:true}).click();
    await page.reload();await expect(page.getByRole("heading",{name:"更新卡組",exact:true})).toBeVisible();
    await page.getByRole("button",{name:"刪除",exact:true}).click();
    await page.getByRole("button",{name:"確認刪除",exact:true}).click();
    await expect(page.getByRole("heading",{name:"還沒有圖卡組"})).toBeVisible();
    expect(requests.filter(r=>r.method!=="GET").every(r=>r.path.includes("/card-sets"))).toBe(true);
  });
}

test("quality, literal-safe back and source details remain readable on mobile", async ({page})=>{
  await page.setViewportSize({width:390,height:844});
  const view=structureView();view.relations=[];
  view.status={processing:"partial",quality:"needs_review",decision:"review",reason_codes:["LITERALS_RESTORED_FROM_SOURCE","RELATIONS_REJECTED","SOURCE_REVIEW_SUGGESTED"]};
  const literal="if (count != 0) {\n    value = 1.0 / count;\n}\nE=m*c^2; threshold <= 0.001; '\\0'\n"+"x".repeat(700);
  view.concepts[0].claims[0].text=literal;view.concepts[0].claims[0].evidence[0].kind="code";
  view.concepts[0].claims[0].evidence[0].quote=literal;
  const prose="必要條件不可省略；保留數值 0.001 與否定。".repeat(100);
  const paragraph=structuredClone(view.concepts[0].claims[0]);paragraph.claim_id=`claim:sha256:${"f".repeat(64)}`;
  paragraph.text=prose;paragraph.evidence[0].kind="paragraph";paragraph.evidence[0].quote=prose;
  view.concepts[0].claims.push(paragraph);
  await mockCardSets(page,view,savedSet(view));await page.goto(studyPath);
  await expect(page.getByText("教材有內容待確認",{exact:true})).toBeVisible();
  await page.getByRole("button",{name:"翻卡：Stack，查看重點"}).click();
  await expect(page.getByText("此教材有內容待確認，複習時建議一併查看來源。",{exact:true})).toBeVisible();
  await expect(page.getByText(/LITERALS_RESTORED_FROM_SOURCE/)).not.toBeVisible();
  const code=page.locator(".card-literal");expect(await code.textContent()).toBe(literal);
  expect(await code.evaluate(e=>e.scrollWidth>e.clientWidth)).toBe(true);
  expect(await page.locator(".flashcard-claims .card-text").textContent()).toBe(prose);
  expect(await page.locator(".flashcard-answer").evaluate(e=>e.scrollHeight>e.clientHeight)).toBe(true);
  await code.focus();await page.keyboard.press("ArrowRight");await expect.poll(()=>code.evaluate(e=>e.scrollLeft)).toBeGreaterThan(0);
  await page.getByText("檢視品質詳情",{exact:true}).click();
  await expect(page.getByText(/LITERALS_RESTORED_FROM_SOURCE/)).toBeVisible();
  await page.screenshot({path:"test-results/flashcard-back-mobile.png"});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await expect(page.locator(".card-relations")).toHaveCount(0);
});

test("map shortcut opens saved sets rather than a checkbox modal",async({page})=>{
  await mockCardSets(page,structureView(),savedSet());
  await page.goto(`/materials/${materialId}/runs/${runId}/knowledge-structures/${encodeURIComponent(structureRevision)}`);
  await page.getByRole("button",{name:"觀念圖卡",exact:true}).click();
  await expect(page).toHaveURL(new RegExp(`${listPath}$`));
  await expect(page.getByRole("heading",{name:"我的圖卡組",exact:true})).toBeVisible();
  await expect(page.getByRole("checkbox")).toHaveCount(0);
});

test("read, source and version failures are explicit and recoverable",async({page})=>{
  const {rows}=await mockCardSets(page,structureView(),savedSet());
  let fail=true;
  await page.route(`**${apiPath}/${cardSetId}/cards`,route=>fail?route.fulfill({status:404,json:{schema:"api-error/v1",request_id:cardSetId,reason_code:"RESOURCE_NOT_FOUND",retryable:false,message:"Request could not be completed."}}):route.fallback());
  await page.goto(studyPath);await expect(page.getByRole("heading",{name:"無法讀取圖卡組"})).toBeVisible();
  fail=false;await page.getByRole("button",{name:"重新讀取",exact:true}).click();
  await page.getByRole("button",{name:"翻卡：Stack，查看重點"}).click();
  await page.route("**/evidence/*/source",route=>route.fulfill({status:404,json:{schema:"api-error/v1",request_id:cardSetId,reason_code:"RESOURCE_NOT_FOUND",retryable:false,message:"Request could not be completed."}}));
  await page.getByRole("button",{name:"查看教材來源",exact:true}).click();
  await page.getByRole("button",{name:"查看第 1 頁來源"}).click();await expect(page.getByRole("alert")).toBeVisible();
  await page.keyboard.press("Escape");
  rows.get(cardSetId)!.version++;
  await page.getByRole("button",{name:"下一張 →",exact:true}).click();
  await expect(page.getByRole("alert")).toContainText("已在其他頁面更新");
  await page.getByRole("button",{name:"重新讀取卡組",exact:true}).click();
  await expect(page.getByRole("status",{name:"複習進度"})).toHaveText("第 1 / 2 張");
});

for(const action of ["navigation","account"] as const) {
  test(`late saved-card responses are isolated after ${action}`,async({page})=>{
    await mockCardSets(page,structureView(),savedSet());
    let release!:()=>void;const gate=new Promise<void>(resolve=>release=resolve);let started=false;
    await page.route(`**${apiPath}/${cardSetId}/cards`,async route=>{started=true;await gate;await route.fulfill({json:projectedSet(savedSet())});});
    await page.goto(studyPath);await expect.poll(()=>started).toBe(true);
    if(action==="navigation") await page.evaluate(()=>{history.pushState(null,"","/materials");dispatchEvent(new PopStateEvent("popstate"));});
    else {await page.evaluate(()=>{const c=new BroadcastChannel("studydy-account");c.postMessage("identity-changed");c.close();});await expect(page).toHaveURL(/\/login$/);}
    release();await expect(page.locator(".flashcard-face")).toHaveCount(0);
  });
}

test("a late create response cannot navigate away from another page",async({page})=>{
  await mockCardSets(page);
  let release!:()=>void;const gate=new Promise<void>(resolve=>release=resolve);let started=false;
  await page.route(`**${apiPath}`,async route=>{
    if(route.request().method()!=="POST") return route.fallback();
    started=true;await gate;await route.fulfill({status:201,json:savedSet()});
  });
  await page.goto(listPath+"/new");await page.getByLabel("圖卡組名稱",{exact:true}).fill("Late");
  await page.getByRole("checkbox",{name:"Stack",exact:true}).check();
  await page.getByRole("button",{name:"建立圖卡組",exact:true}).click();
  await expect.poll(()=>started).toBe(true);
  await page.getByRole("button",{name:"取消",exact:true}).click();
  release();await expect(page).toHaveURL(new RegExp(`${listPath}$`));
  await expect(page.getByRole("heading",{name:"我的圖卡組",exact:true})).toBeVisible();
});
