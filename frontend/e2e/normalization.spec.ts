import { expect, test, type Page } from "@playwright/test";
const material="11111111-1111-4111-8111-111111111111", source="22222222-2222-4222-8222-222222222222", normalization="33333333-3333-4333-8333-333333333333", artifact="44444444-4444-4444-8444-444444444444";
const text="Stacks\nA stack follows LIFO order.\n";
const capabilities={schema:"source-capabilities/v1",quality_notice:"PDF 優先；其他格式自動轉換，品質不保證。",formats:[
  {extension:".pdf",media_type:"application/pdf",max_bytes:104857600},{extension:".txt",media_type:"text/plain",max_bytes:104857600}]};
async function session(page:Page) {
  await page.route("**/v1/session",r=>r.fulfill({json:{schema:"learner-identity/v1",learner_id:material}}));
  await page.route("**/v1/session/refresh",r=>r.fulfill({json:{schema:"learner-identity/v1",learner_id:"33333333-3333-4333-8333-333333333333"}}));
  await page.route("**/v1/source-capabilities",r=>r.fulfill({json:capabilities}));
}
for(const width of [1536,390]) test(`single non-PDF normalization resumes without reupload at ${width}px`,async({page},info)=>{
  await page.setViewportSize({width,height:1024});await session(page);
  let status="running";let drafts=0,uploads=0,revisions=0;
  const sourceView=()=>({source_id:source,normalization_id:normalization,original_artifact_id:artifact,original_name:"notes.txt",media_type:"text/plain",status,
    normalized_artifact_id:status==="ready"?source:null,page_count:status==="ready"?1:null,error_code:status==="failed"?"NORMALIZATION_TIMEOUT":null});
  const item=()=>({schema:"material-library-item/v1",material_id:material,source_artifact_id:status==="ready"?source:null,
    display_name:"notes.txt",size_bytes:text.length,created_at:"2026-09-17T00:00:00Z",latest_attempt:null,available_structures:[],study_sessions:[],source:sourceView()});
  await page.route("**/v1/materials",r=>{drafts++;return r.fulfill({status:201,json:{schema:"material-draft/v1",material_id:material}});});
  await page.route(`**/v1/materials/${material}/sources`,r=>{if(r.request().method()==="POST")uploads++;return r.fulfill({json:{schema:"material-sources/v1",material_id:material,sources:[sourceView()]}});});
  await page.route(`**/v1/materials/${material}`,r=>r.fulfill({json:item()}));
  await page.route("**/v1/materials",r=>r.fulfill({json:{schema:"material-library/v1",materials:[item()]}}));
  await page.route(`**/v1/materials/${material}/revisions`,r=>{revisions++;return r.abort();});
  await page.goto("/upload");await expect(page.getByText("非 PDF 教材會先轉換為 PDF，請在下一步確認轉換內容。", {exact:true})).toHaveCount(0);
  await page.getByLabel("選擇教材檔案",{exact:true}).setInputFiles({name:"notes.txt",mimeType:"text/plain",buffer:Buffer.from(text)});
  await page.getByRole("button",{name:"上傳並確認來源"}).click();await expect(page).toHaveURL(new RegExp(`/materials/${material}/sources$`));
  await expect(page.locator(".source-row").getByRole("status")).toContainText("正在轉換");await page.reload();
  await expect(page.locator(".source-row").getByRole("status")).toContainText("正在轉換");expect(drafts).toBe(1);expect(uploads).toBe(1);expect(revisions).toBe(0);
  await page.screenshot({path:info.outputPath("normalizing.png"),fullPage:true});
  status="ready";await expect(page.getByRole("button",{name:"開始分析教材"})).toBeVisible();
  await expect(page.getByRole("link",{name:/预覽|預覽/})).toHaveAttribute("href",`/v1/artifacts/${source}`);
  await page.screenshot({path:info.outputPath("ready.png"),fullPage:true});
  await page.goto("/materials");await expect(page.getByRole("article")).not.toContainText("尚未建立知識地圖");
  await expect(page.getByRole("article").getByRole("link")).toHaveCount(0);
  await page.getByRole("button",{name:"管理「notes.txt」",exact:true}).click();
  await page.getByRole("button",{name:"管理教材",exact:true}).click();
  await expect(page.getByRole("link",{name:/下載原檔/})).toHaveAttribute("href",`/v1/artifacts/${artifact}/download`);
  expect(revisions).toBe(0);
});

test("failed conversion retries explicitly, keeps original, and never starts AI",async({page})=>{
  await session(page);let retries=0,posts=0;
  const job={source_id:source,normalization_id:normalization,original_artifact_id:artifact,original_name:"discrete-mathematics.txt",media_type:"text/plain",status:"failed",normalized_artifact_id:null,page_count:null,error_code:"UTF8_REQUIRED"};
  page.on('request',r=>{if(r.method()==='POST'&&!r.url().endsWith('/session/refresh'))posts++;});
  await page.route(`**/v1/materials/${material}/sources`,r=>r.fulfill({json:{schema:"material-sources/v1",material_id:material,sources:[job]}}));
  await page.route(`**/v1/materials/${material}`,r=>r.fulfill({json:{schema:"material-library-item/v1",source:job,material_id:material,source_artifact_id:null,display_name:"discrete-mathematics.txt",size_bytes:10,created_at:"2026-09-17T00:00:00Z",latest_attempt:null,available_structures:[],study_sessions:[]}}));
  await page.route(`**/v1/materials/${material}/sources/${normalization}/retry`,r=>{retries++;return r.fulfill({json:{schema:"material-sources/v1",material_id:material,sources:[job]}});});
  await page.goto(`/materials/${material}/sources`);await expect(page.locator(".source-row").getByRole("status")).toContainText("轉換失敗");
  await page.reload();await expect(page.getByRole("link",{name:"下載原檔"})).toBeVisible();expect(posts).toBe(0);
  await page.getByRole("button",{name:"重試轉換"}).click();expect(retries).toBe(1);expect(posts).toBe(1);
});

test("accepted deletion of a converting source survives reload and returns to library",async({page})=>{
  await session(page);let deleting=false,removed=false;
  const failure={schema:"api-error/v1",request_id:material,reason_code:"RESOURCE_NOT_FOUND",retryable:false,message:"Request could not be completed."};
  const job={source_id:source,normalization_id:normalization,original_artifact_id:artifact,original_name:"remove.txt",media_type:"text/plain",status:"running",normalized_artifact_id:null,page_count:null,error_code:null};
  await page.route(`**/v1/materials/${material}/sources`,r=>removed?r.fulfill({status:404,json:failure}):r.fulfill({json:{schema:"material-sources/v1",material_id:material,discard_requested:deleting,sources:[job]}}));
  await page.route(`**/v1/materials/${material}`,r=>{
    if(r.request().method()==='DELETE'){deleting=true;return r.fulfill({status:202,json:{schema:'material-discard/v1',material_id:material,state:'removing'}});}
    return removed?r.fulfill({status:404,json:failure}):r.fulfill({json:{schema:"material-library-item/v1",source:job,material_id:material,source_artifact_id:null,display_name:"remove.txt",size_bytes:20,created_at:"2026-09-17T00:00:00Z",latest_attempt:null,available_structures:[],study_sessions:[]}});
  });
  await page.route('**/v1/materials',r=>r.fulfill({json:{schema:'material-library/v1',materials:[]}}));
  await page.goto(`/materials/${material}/sources`);
  await page.getByRole('button',{name:'刪除教材',exact:true}).click();await expect(page.getByText(/目前已上傳的/)).toBeVisible();
  await page.getByRole('button',{name:'確認刪除',exact:true}).click();await page.reload();
  await expect(page.getByText('正在刪除教材…',{exact:true})).toBeVisible();removed=true;
  await expect(page).toHaveURL(/\/materials$/);await expect(page.getByRole('heading',{name:'我的教材',exact:true})).toBeVisible();
});

for (const width of [1536, 390]) test(`library moves file management out of cards at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 1024 });
  await session(page);
  const materials = [
    { name: "原生教材.pdf", status: null, media: "application/pdf" },
    { name: "課堂簡報.ppt", status: "ready", media: "application/vnd.ms-powerpoint" },
    { name: "課程講義.doc", status: "running", media: "application/msword" },
    { name: "離散數學筆記.txt", status: "failed", media: "text/plain" },
  ].map((file, index) => ({
    schema: "material-library-item/v1", material_id: `11111111-1111-4111-8111-11111111111${index}`,
    source_artifact_id: !file.status || file.status === "ready" ? artifact : null,
    display_name: file.name, size_bytes: 1024, created_at: "2026-09-17T00:00:00Z",
    latest_attempt: null, available_structures: [], study_sessions: [],
    source: file.status ? { source_id: source, normalization_id: normalization,
      original_artifact_id: artifact, original_name: file.name, media_type: file.media, status: file.status,
      normalized_artifact_id: file.status === "ready" ? source : null,
      page_count: file.status === "ready" ? 2 : null,
      error_code: file.status === "failed" ? "NORMALIZATION_TIMEOUT" : null } : undefined,
  }));
  await page.route("**/v1/materials", r => r.fulfill({ json: { schema: "material-library/v1", materials } }));
  await page.goto("/materials");
  await expect(page.getByRole("article")).toHaveCount(4);
  const converted = page.getByRole("article", { name: "課堂簡報.ppt", exact: true });
  await expect(page.getByRole("article").getByRole("link")).toHaveCount(0);
  await expect(converted).not.toContainText("尚未建立知識地圖");
  await expect(converted).not.toContainText(/教材轉換|轉換後 PDF|下載原檔/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("mixed-format-library.png"), fullPage: true });
  await converted.getByRole("button", { name: "建立知識地圖" }).click();
  await expect(page).toHaveURL(new RegExp(`/materials/${materials[1].material_id}/sources$`));
});

for (const viewport of [{width:1920,height:1080},{width:1536,height:1024},{width:1366,height:768},{width:390,height:844}]) {
  test(`material flow visual consistency at ${viewport.width}x${viewport.height}`, async ({page}, info) => {
    await page.setViewportSize(viewport);
    const expectApplicationFrame = async () => {
      const { usable, width } = await page.locator(".app-main").evaluate(main => {
        const css = getComputedStyle(main);
        return { usable: main.getBoundingClientRect().width - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight),
          width: main.firstElementChild!.getBoundingClientRect().width };
      });
      expect(width).toBeCloseTo(Math.min(usable, 1600), 0);
    };
    const unexpected: string[] = [];
    await page.route(/\/v[12]\//, r => { unexpected.push(r.request().url()); return r.abort(); });
    await session(page);
    let filename = "資料結構與演算法講義.docx";
    const job = () => ({source_id:source, normalization_id:normalization, original_artifact_id:artifact,
      original_name:filename, media_type:"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      status:"ready", normalized_artifact_id:source, page_count:12, error_code:null});
    const item = () => ({schema:"material-library-item/v1", material_id:material,
      source_artifact_id:source, display_name:filename, size_bytes:204800, created_at:"2026-09-17T00:00:00Z",
      latest_attempt:null, available_structures:[], study_sessions:[], source:job()});
    const run = {schema:"material-processing-run/v1", run_id:normalization, material_id:material,
      source_artifact_id:source, status:"running", progress_stage:"evidence", cancel_requested_at:null,
      completed_pages:3, total_pages:12, error_code:null, created_at:"2026-09-18T00:00:00Z",
      updated_at:"2026-09-18T00:01:00Z", completed_at:null, output_binding:null};
    await page.clock.setFixedTime(new Date("2026-09-18T00:02:00Z"));
    await page.route("**/v1/materials", r => r.fulfill({json:{schema:"material-library/v1", materials:[item()]}}));
    await page.route(`**/v1/materials/${material}`, r => r.fulfill({json:item()}));
    await page.route(`**/v1/materials/${material}/sources`, r => r.fulfill({json:{schema:"material-sources/v1",material_id:material,sources:[job()]}}));
    await page.route(`**/v1/material-processing-runs/${normalization}`, r => r.fulfill({json:run}));
    await page.goto("/upload");
    await expect(page.locator(".file-drop")).toContainText("PDF、TXT");
    await expectApplicationFrame();
    expect((await page.locator(".file-drop").boundingBox())!.width).toBeLessThanOrEqual(880);
    const uploadColumns = await page.locator(".upload-layout").evaluate(el => getComputedStyle(el).gridTemplateColumns);
    const uploadMascot = await page.locator(".upload-hero > img").boundingBox();
    await page.screenshot({path:info.outputPath("upload.png"),fullPage:true});
    await page.goto(`/materials/${material}/sources`);
    await expect(page.getByRole("link", {name:"預覽 PDF", exact:true})).toBeVisible();
    await expect(page.locator(".source-row").getByRole("status")).toHaveCount(0);
    await expectApplicationFrame();
    expect((await page.locator(".source-add-control").boundingBox())!.height).toBeLessThanOrEqual(44);
    await expect(page.locator(".source-page .primary-button")).toHaveCount(1);
    expect(await page.locator(".upload-layout").evaluate(el => getComputedStyle(el).gridTemplateColumns)).toBe(uploadColumns);
    const mascot = (await page.locator(".upload-hero > img").boundingBox())!;
    expect(mascot.width).toBe(uploadMascot!.width);
    expect(mascot.height).toBe(uploadMascot!.height);
    const copy = (await page.locator(".upload-hero > div").boundingBox())!;
    expect(copy.x + copy.width).toBeLessThan(mascot.x);
    const card = (await page.locator(".source-list-card").boundingBox())!;
    const rail = (await page.locator(".source-guide").boundingBox())!;
    const confirmation = (await page.locator(".source-list-footer").boundingBox())!;
    const cta = (await page.getByRole("button",{name:"開始分析教材"}).boundingBox())!;
    expect(cta.y).toBeGreaterThan(confirmation.y);
    expect(confirmation.y + confirmation.height - cta.y - cta.height).toBeLessThanOrEqual(33);
    if (viewport.width > 1200) { expect(rail.width).toBe(320); expect(rail.x - card.x - card.width).toBeCloseTo(24,0); }
    else { expect(rail.y).toBeGreaterThan(confirmation.y + confirmation.height); }
    const back = (await page.getByRole("button",{name:"返回教材庫"}).boundingBox())!;
    await expect(page.getByRole("button", {name:"刪除教材", exact:true})).toBeVisible();
    expect((await page.getByRole("button", {name:"刪除教材", exact:true}).boundingBox())!.y).toBeCloseTo(back.y, 0);
    const steps = page.locator(".source-guide li");
    await expect(steps.nth(1)).toHaveAttribute("aria-current","step");
    for (const [index, token] of [[0,"--success"],[1,"--studydy-blue"],[2,"--text-secondary"]] as const) {
      expect(await steps.nth(index).locator(":scope > span").evaluate((el, token) => {
        const expected = document.createElement("span"); expected.style.color = `var(${token})`; el.append(expected);
        const matches = getComputedStyle(el).color === getComputedStyle(expected).color; expected.remove(); return matches;
      }, token)).toBe(true);
    }
    await page.screenshot({path:info.outputPath("conversion.png"),fullPage:true});
    filename = `${"VeryLongFilenameWithoutSpaces".repeat(8)}.docx`;
    await page.reload(); await expect(page.locator(".source-name")).toHaveText(filename);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
    await page.screenshot({path:info.outputPath("conversion-long-name.png"),fullPage:true});
    filename = "資料結構與演算法講義.docx";
    for (const [name,path,selector] of [
      ["processing",`/materials/${material}/runs/${normalization}`,".processing-grid"],
      ["library","/materials",".library-item"],
    ]) {
      await page.goto(path); await expect(page.locator(selector)).toBeVisible();
      await expectApplicationFrame();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(viewport.width);
      await page.screenshot({path:info.outputPath(`${name}.png`),fullPage:true});
    }
    if (viewport.width === 1366) {
      await page.setViewportSize({width:1200,height:768}); await page.goto(`/materials/${material}/sources`);
      await expect(page.getByRole("link", {name:"預覽 PDF", exact:true})).toBeVisible();
    await expect(page.locator(".source-row").getByRole("status")).toHaveCount(0);
      const box = (await page.locator(".source-row").boundingBox())!;
      expect((await page.locator(".source-guide").boundingBox())!.y).toBeGreaterThan(box.y + box.height);
    }
    expect(unexpected).toEqual([]);
  });
}
