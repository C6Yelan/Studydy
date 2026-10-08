import {chromium} from '../../../frontend/node_modules/playwright/index.mjs';
import {writeFile} from 'node:fs/promises';
const output=process.argv[2];
const browser=await chromium.launch({headless:true});
try {
 const page=await browser.newPage({viewport:{width:1280,height:720}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:4194');
 await page.waitForFunction(()=>window.renderReady,{},{timeout:30000});
 await page.evaluate(()=>window.renderMovie());
 const result=await page.evaluate(()=>({result:window.renderResult,error:window.renderError}));
 await writeFile(output,JSON.stringify({...result,errors}));
 if(result.result!==0||errors.length)throw new Error('Motion Canvas render failed');
} finally {await browser.close();}
