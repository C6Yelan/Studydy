import { expect, test } from "@playwright/test";

test.skip(process.env.STUDYDY_E2E_CARDS !== "true", "Requires isolated concept cards fixture");
const cases = JSON.parse(process.env.STUDYDY_E2E_CARDS_DATA ?? "[]") as {
  material: string; run: string; revision: string; labels: string[];
}[];

test("persisted decks reopen exact canonical cards and never mutate learning", async ({page}) => {
  await page.goto("/");
  await page.getByLabel("Email", {exact:true}).fill("learner_test@example.com");
  await page.getByLabel("密碼", {exact:true}).fill("Synthetic test password 42");
  await page.getByRole("button", {name:"登入", exact:true}).click();
  await expect(page.getByRole("heading", {name:"歡迎回來！", level:1, exact:true})).toBeVisible();
  for (const row of cases) {
    const listPath = `/materials/${row.material}/card-sets`;
    await page.goto(listPath);
    await page.getByRole("button",{name:"＋ 建立圖卡組",exact:true}).click();
    await page.getByLabel("圖卡組名稱",{exact:true}).fill("期中重點");
    for (const label of row.labels) await page.getByRole("checkbox", {name:label, exact:true}).check();
    const responseTask = page.waitForResponse(response => response.url().endsWith("/cards"));
    await page.getByRole("button", {name:"建立圖卡組", exact:true}).click();
    const response = await responseTask;
    expect(response.status()).toBe(200);
    const data = await response.json();const cards=data.cards;
    expect(data.card_set.material_id).toBe(row.material);
    expect(data.card_set.knowledge_structure_revision).toBe(row.revision);
    expect(cards.selection.concept_ids).toEqual(data.card_set.concept_ids);
    const studyUrl=page.url();
    const canonicalResponse=await page.request.get(`/v1/materials/${row.material}/knowledge-structures/${encodeURIComponent(row.revision)}`);
    const canonical=await canonicalResponse.json();
    expect(cards.cards).toEqual(canonical.concepts.filter((c:{label:string})=>row.labels.includes(c.label)));
    expect(cards.status).toEqual(canonical.status);
    await page.getByRole("button",{name:`翻卡：${row.labels[0]}，查看重點`,exact:true}).click();
    for(const claim of cards.cards[0].claims) await expect(page.getByText(claim.text,{exact:true})).toBeVisible();
    await expect(page.locator(".card-relations")).toHaveCount(0);
    await page.getByRole("button",{name:"查看教材來源",exact:true}).click();
    const sources=page.getByRole("dialog",{name:"圖卡教材來源",exact:true});
    const sourceTask = page.waitForResponse(response => response.url().includes("/evidence/") && response.url().endsWith("/source"));
    await sources.locator(".card-evidence").first().getByRole("button").click();
    const sourceResponse=await sourceTask;expect(sourceResponse.status()).toBe(200);
    expect(decodeURIComponent(sourceResponse.url())).toContain(`/${row.revision}/evidence/${cards.cards[0].claims[0].evidence[0].evidence_id}/source`);
    const resolved=await sourceResponse.json();
    const sourceDialog=page.getByRole("dialog",{name:"教材來源",exact:true});
    await expect(sourceDialog.getByRole("link",{name:"開啟 PDF 來源頁"})).toHaveAttribute("href",resolved.preview_url);
    const pdf=await page.request.get(resolved.preview_url.split("#")[0]);expect(pdf.status()).toBe(200);
    expect(pdf.headers()["content-type"]).toContain("application/pdf");
    await page.keyboard.press("Escape");await page.keyboard.press("Escape");
    if(row.labels.length>1) {
      await page.getByRole("button",{name:"下一張 →",exact:true}).click();
      await expect(page.getByRole("status",{name:"複習進度"})).toHaveText(`第 2 / ${row.labels.length} 張`);
      await page.reload();await expect(page.getByRole("status",{name:"複習進度"})).toHaveText(`第 2 / ${row.labels.length} 張`);
      await page.getByRole("button",{name:"← 上一張",exact:true}).click();
      await expect(page.getByRole("status",{name:"複習進度"})).toHaveText(`第 1 / ${row.labels.length} 張`);
      await page.getByRole("button",{name:"下一張 →",exact:true}).click();
    }
    await page.getByRole("button",{name:"完成複習",exact:true}).click();
    await page.getByRole("button",{name:"繼續複習",exact:true}).click();
    await expect(page).toHaveURL(studyUrl);
    await page.getByRole("button",{name:"重新開始",exact:true}).click();
    await expect(page.getByRole("status",{name:"複習進度"})).toHaveText(`第 1 / ${row.labels.length} 張`);
    await page.getByRole("button",{name:"返回我的圖卡組",exact:true}).click();
    await page.getByRole("button",{name:"編輯",exact:true}).click();
    await page.getByLabel("圖卡組名稱",{exact:true}).fill("更新重點");
    if(row.labels.length>1) await page.getByRole("checkbox",{name:row.labels[1],exact:true}).uncheck();
    await page.getByRole("button",{name:"儲存圖卡組",exact:true}).click();
    await expect(page.getByRole("heading",{name:"更新重點",exact:true})).toBeVisible();
    await page.reload();await expect(page.getByRole("status",{name:"複習進度"})).toHaveText("第 1 / 1 張");
    await page.getByRole("button",{name:"完成複習",exact:true}).click();
    await page.getByRole("button",{name:"刪除",exact:true}).click();
    await page.getByRole("button",{name:"確認刪除",exact:true}).click();
    await expect(page.getByRole("heading",{name:"還沒有圖卡組",exact:true})).toBeVisible();
  }
});
