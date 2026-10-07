// 僅用於開發比較；目標來自固定樣本，IP 由 Python 全數驗證並固定。
import { chromium } from '../../frontend/node_modules/playwright/index.mjs';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
const [input, output] = process.argv.slice(2);
const { samples, pins, directory, proxy } = JSON.parse(await readFile(input, 'utf8'));
const sha = value => createHash('sha256').update(value).digest('hex');
const rules = [...Object.entries(pins).map(([host, ip]) => `MAP ${host} ${ip}`), 'EXCLUDE 127.0.0.1', 'MAP * ~NOTFOUND'].join(',');
const browser = await chromium.launch({headless:true, proxy:{server:proxy,bypass:'<-loopback>'}, env:{PATH:process.env.PATH,HOME:process.env.HOME}, args:[
  `--host-resolver-rules=${rules}`, '--proxy-bypass-list=<-loopback>', '--disable-quic', '--disable-background-networking',
  '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
]});
const results = [];
try {
  for (const sample of samples) {
    const started = performance.now();
    const row = {id:sample.id,method:'C',outcome:'failed',final_url:sample.url,content_type:null,content_length:null,title:null,identity_match:null,license:'unknown',license_source:null,extraction_quality:'none',failure_reason:null,chain:[],blocked_requests:[]};
    const context = await browser.newContext({serviceWorkers:'block',acceptDownloads:false});
    let requests = 0, wireBytes = 0, budgetFailure = null;
    const stop = reason => { budgetFailure = reason; void context.close(); };
    const timer = setTimeout(() => stop('TIME_LIMIT'), 30000);
    await context.routeWebSocket('**/*', socket => socket.close());
    await context.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      let redirects = 0;
      for (let previous = request.redirectedFrom(); previous; previous = previous.redirectedFrom()) redirects++;
      const allowed = url.protocol === 'https:' && (!url.port || url.port === '443') && !url.username && !url.password && Object.hasOwn(pins,url.hostname) && !/[\x00-\x20]/.test(request.url());
      if (!allowed || redirects >= 5 || ++requests > 60 || ['image','media','font'].includes(request.resourceType())) {
        row.blocked_requests.push({url:request.url(),reason:!allowed?'TARGET_POLICY':redirects>=5?'REDIRECT_LIMIT':requests>60?'REQUEST_LIMIT':'NON_TEXT_RESOURCE'});
        return route.abort();
      }
      await route.continue();
    });
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    cdp.on('Network.dataReceived', event => { wireBytes += event.dataLength; if (wireBytes > 8*1024*1024) stop('BYTE_LIMIT'); });
    page.on('popup', popup => void popup.close());
    page.on('response', response => {
      if (response.request().isNavigationRequest()) row.chain.push({url:response.url(),status:response.status(),content_type:response.headers()['content-type'] ?? ''});
      const length = Number(response.headers()['content-length'] ?? 0);
      if (length > 8*1024*1024) stop('BYTE_LIMIT');
    });
    try {
      const response = await page.goto(sample.url,{waitUntil:'domcontentloaded',timeout:20000});
      row.final_url = page.url();
      row.http_status = response?.status() ?? null;
      row.content_type = response?.headers()['content-type'] ?? '';
      if (!response || response.status() !== 200) throw new Error(`HTTP_${response?.status() ?? 'NONE'}`);
      if (sample.selector) await page.locator(sample.selector).first().waitFor({timeout:4000});
      row.title = await page.title();
      if (/just a moment|access denied|verify.*human|captcha/i.test(row.title)) throw new Error('CHALLENGE_STOP');
      const body = await response.body();
      const dom = await page.content();
      const extracted = await page.evaluate(selector => {
        const nodes = selector ? [...document.querySelectorAll(selector)] : [...document.querySelectorAll('main,[role="main"]')];
        return (nodes.length ? nodes.map(n => n.innerText ?? n.textContent).join('\n') : (document.body?.innerText ?? document.documentElement.textContent)).slice(0,200001);
      }, sample.selector ?? null);
      if (extracted.length > 200000 || Buffer.byteLength(dom) > 8*1024*1024) throw new Error('TEXT_LIMIT');
      if (body.length>8*1024*1024) throw new Error('BYTE_LIMIT');
      row.content_length = body.length;
      row.body_sha256 = sha(body); row.dom_sha256 = sha(dom); row.text_sha256 = sha(extracted);
      row.text_length = extracted.length;
      row.markers_found = sample.markers.filter(marker => extracted.toLowerCase().includes(marker.toLowerCase()));
      row.identity_match = sample.title && row.title ? row.title.toLowerCase().includes(sample.title.toLowerCase()) : null;
      row.extraction_quality = sample.markers.length && row.markers_found.length === sample.markers.length ? 'markers-present' : 'incomplete-or-unassessed';
      row.license_source = sample.license_url && sample.license_verified && dom.includes(sample.license_link_marker) ? sample.license_url : null;
      row.license = row.license_source ? sample.license : 'unknown';
      row.outcome = 'retrieved';
      await writeFile(`${directory}/${sample.id}-C.body`,body);
      await writeFile(`${directory}/${sample.id}-C.dom`,dom);
      await writeFile(`${directory}/${sample.id}-C.txt`,extracted);
    } catch (error) { row.outcome = 'failed'; row.failure_reason = budgetFailure ?? String(error.message).slice(0,400); }
    finally { clearTimeout(timer); await context.close(); }
    row.elapsed_ms = Math.round(performance.now()-started); row.observed_network_bytes=wireBytes;
    results.push(row);
    await writeFile(output,JSON.stringify(results,null,2));
  }
} finally { await browser.close(); }
