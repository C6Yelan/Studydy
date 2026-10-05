// 只播放 benchmark.py 的合成 MP4；不連產品 API、不送模型請求。
import { chromium } from '../../frontend/node_modules/playwright/index.mjs';
import { createServer } from 'node:http';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
const directory=resolve(process.argv[2]??'.studydy-runtime/podcast-beats/benchmark');
const files=new Map([30,60].map(fps=>[`/${fps}.mp4`,readFileSync(join(directory,`reveal-trace-${fps}-2.mp4`))]));
const server=createServer((request,response)=>{
  const body=files.get(request.url);
  if(!body){response.writeHead(404);response.end();return}
  const range=request.headers.range?.match(/^bytes=(\d+)-(\d*)$/);
  const start=range?Number(range[1]):0,end=range?.[2]?Math.min(Number(range[2]),body.length-1):body.length-1;
  response.writeHead(range?206:200,{'Content-Type':'video/mp4','Access-Control-Allow-Origin':'*','Accept-Ranges':'bytes',
    'Content-Length':end-start+1,...(range?{'Content-Range':`bytes ${start}-${end}/${body.length}`}:{})});
  response.end(body.subarray(start,end+1));
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({headless:true});
const report=[];
try {
  const page=await browser.newPage();
  for(const fps of [60,30]) {
    await page.setContent(`<video crossorigin="anonymous" muted src="http://127.0.0.1:${server.address().port}/${fps}.mp4"></video>`);
    await page.locator('video').evaluate(v=>v.readyState>=2?Promise.resolve():new Promise(r=>v.addEventListener('loadeddata',r,{once:true})));
    for(const rate of [.5,1,2]) {
      const value=await page.locator('video').evaluate(async(v,rate)=>{
        v.pause();v.playbackRate=rate;v.currentTime=2.7;
        await new Promise(r=>v.addEventListener('seeked',r,{once:true}));
        const samples=[];
        const completed=new Promise((resolve,reject)=>{
          const timeout=setTimeout(()=>reject(Error('media frame timeout')),12000);
          const frame=(now,m)=>{
            samples.push({wall:now,media:m.mediaTime,clock:v.currentTime,frames:m.presentedFrames});
            if(m.mediaTime>=4){v.pause();clearTimeout(timeout);resolve(samples)}
            else v.requestVideoFrameCallback(frame);
          };
          v.requestVideoFrameCallback(frame);
        });
        await v.play();await completed;
        const first=samples[0],last=samples.at(-1);
        return {samples:samples.length,presented_fps:(last.frames-first.frames)*1000/(last.wall-first.wall),
          max_media_clock_delta:Math.max(...samples.map(s=>Math.abs(s.media-s.clock))),
          source_frame_steps:[...new Set(samples.slice(1).map((s,i)=>Number((s.media-samples[i].media).toFixed(4))))]};
      },rate);
      report.push({fps,rate,...value});
    }
    const seeks=await page.locator('video').evaluate(async(v)=>{
      const canvas=document.createElement('canvas');canvas.width=1920;canvas.height=1080;const context=canvas.getContext('2d');
      const results=[];v.pause();
      for(const time of [2.9,3.05,6.05,1,3.5]) {
        const done=new Promise(r=>v.addEventListener('seeked',r,{once:true}));v.currentTime=time;await done;
        context.drawImage(v,0,0,1920,1080);const rgb=[...context.getImageData(1200,320,1,1).data].slice(0,3);
        results.push({time,rgb,revealed:rgb[0]<240});
      }
      return results;
    });
    if(seeks.some(s=>s.revealed!==(s.time>=3)))throw Error(`reveal/seek mismatch at ${fps}fps`);
    report.push({fps,seeks});
  }
  const decision='Keep 60fps: measured half-speed presentation drops from about 30 to 15 frames/s at 30fps, so motion equivalence is not established despite stable render savings.';
  writeFileSync(join(directory,'browser-summary.json'),JSON.stringify({synthetic:true,browser:'Chromium headless',measurements:report,decision},null,2));
  const summary=JSON.parse(readFileSync(join(directory,'summary.json'),'utf8'));summary.decision=decision;
  writeFileSync(join(directory,'summary.json'),JSON.stringify(summary,null,2));
  console.log(JSON.stringify(report.filter(r=>r.rate),null,2));
} finally {await browser.close();server.close()}
