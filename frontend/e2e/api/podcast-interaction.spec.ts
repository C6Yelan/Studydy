import {expect,test} from '@playwright/test';
test.skip(process.env.STUDYDY_E2E_PODCAST_INTERACTION!=='true','Requires isolated Podcast API/DB fixture');
const data=JSON.parse(process.env.STUDYDY_E2E_PODCAST_DATA??'{}');

test('real API keeps the original transcript and internal captions without retired UI',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');
  await page.getByLabel('Email',{exact:true}).fill('learner_test@example.com');
  await page.getByLabel('密碼',{exact:true}).fill('Synthetic test password 42');
  await page.getByRole('button',{name:'登入',exact:true}).click();
  await expect(page.getByRole('heading',{name:'歡迎回來！',level:1,exact:true})).toBeVisible();
  await page.goto(`/podcasts/${data.podcast}`);
  await expect(page.getByText('依建立時的教材版本保存')).toBeVisible();
  await expect(page.getByRole('region',{name:'逐字稿',exact:true})).toContainText('堆疊');
  await expect(page.getByRole('button',{name:'問目前播放這一段',exact:true})).toHaveCount(0);
  const timeline=await (await page.request.get(`/v1/podcasts/${data.podcast}/episodes/0/timeline`)).json();
  expect(timeline.source_resolver).toContain(encodeURIComponent(data.revision).replaceAll('%3A',':'));
  const vtt=await (await page.request.get(`/v1/podcasts/${data.podcast}/episodes/0/subtitles`)).text();
  expect(vtt).toContain('WEBVTT');expect(vtt).toContain('-->');
  expect(vtt.match(/ --> /g)?.length).toBeGreaterThan(timeline.segments.length);
  expect(timeline.segments).toHaveLength(1);
  await expect(page.getByRole('button',{name:'進入既有測驗',exact:true})).toHaveCount(0);
  await expect(page.getByText('字幕',{exact:true})).toHaveCount(0);
  expect(errors).toEqual([]);
});
