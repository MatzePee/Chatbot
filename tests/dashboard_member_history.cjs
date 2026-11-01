const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const repo=path.resolve(__dirname,'..'),out=process.env.ANALYTICS_OUTPUT||'/root/fanvue-analytics-screenshots';
const history=Array.from({length:30},(_,i)=>({date:`2026-10-${String(i+1).padStart(2,'0')}`,count:[4,5,10,11,12].includes(i)?null:i===0?0:100+i}));
const account=(name,current=129,hist=history)=>({name,current,history:hist,yesterday:{count:128,growth:1},days7:{count:null,growth:null},days30:{count:100,growth:29},sampled_at:'2026-10-30T05:00:00Z',stale:false,status:'OK'});
const x=[account('justsallyxo'),account('ohkaysally',0,history.map(p=>({...p,count:0})))];
const telegram=[account('<img src=x onerror="window.xss=true">'+ 'LangerName'.repeat(8)),account('Nicht verbunden',null,history.map(p=>({...p,count:null})))];
telegram[0].status='FehlerOhneTrennung'.repeat(15);telegram[1].status='Nicht verbunden – Zielkanal und Zugang prüfen';
const dashboard={inventory:{channels:[],unassigned_assets:0},lifecycle_counts:{},upcoming_posts:[],channel_health:[],notifications:[],recent_failures:[]};
(async()=>{fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({headless:true,args:['--no-sandbox']});const page=await browser.newPage();const errors=[],unexpected=[],results=[];page.on('pageerror',e=>errors.push(e.message));
await page.route('**/*',async route=>{const u=new URL(route.request().url()),p=u.pathname;let body,type='text/javascript';if(u.host!=='analytics.test'){unexpected.push(u.href);return route.abort()}
if(p==='/'){type='text/html';body=`<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/autoposter/app.css"><link rel="stylesheet" href="/static/workspace.css"><body><main id="fixture"></main><script type="module">import render from '/autoposter/js/pages/dashboard.js';const root=await render();for(const s of root.querySelectorAll('section'))if(/^(X-Follower|Telegram-Abonnenten)/.test(s.querySelector('h2')?.textContent||''))document.querySelector('main').append(s);window.ready=true;</script></body></html>`}
else if(p==='/autoposter/js/api.js')body=`export const api={dashboard:async()=>(${JSON.stringify(dashboard)}),xFollowers:async()=>(${JSON.stringify(x)}),telegramMembers:async()=>(${JSON.stringify(telegram)}),jobs:async()=>({jobs:[]})};`;
else {const f=p.startsWith('/autoposter/')?path.join(repo,'autoposter/ui',p.slice(12)):p.startsWith('/static/')?path.join(repo,'app',p):null;if(!f||!fs.existsSync(f)){unexpected.push(p);return route.abort()}body=fs.readFileSync(f);type=p.endsWith('.css')?'text/css':'text/javascript'}return route.fulfill({contentType:type,body})});
await page.goto('http://analytics.test');await page.waitForFunction(()=>window.ready);
assert.equal(await page.locator('main > section').count(),2,'Separate Telegram category required');
for(const width of [320,375,390,430,1280]){await page.setViewportSize({width,height:1200});const visible=page.locator('.account-history:visible');assert.equal(await visible.count(),4,'One chart below EACH evaluation');assert.equal(await page.locator('img').count(),0);assert.equal(await page.evaluate(()=>window.xss===true),false);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),JSON.stringify(await page.evaluate(()=>[...document.querySelectorAll('*')].filter(n=>n.getClientRects().length&&n.scrollWidth>n.clientWidth+1&&n.clientWidth>0).map(n=>[n.tagName,n.className,n.clientWidth,n.scrollWidth]))));
const charts=await visible.evaluateAll(nodes=>nodes.map(n=>({points:n.querySelectorAll('circle').length,segments:n.querySelectorAll('polyline').length,rows:n.querySelectorAll(':scope > details > table > tbody > tr').length,missing:n.textContent.includes('Keine Messung')})));
assert.deepEqual(charts.map(c=>c.points),[25,30,25,0]);assert.equal(charts[0].segments,3);assert(charts.every(c=>c.rows===30));
await page.screenshot({path:path.join(out,`analytics-${width}.png`),fullPage:true});fs.writeFileSync(path.join(out,`analytics-${width}-aria.yml`),await page.locator('main').ariaSnapshot());results.push({width,charts,documentFits:true})}
await page.setViewportSize({width:320,height:1200});
const edgeCases=await page.evaluate(async()=>{
 const {historyChart}=await import('/autoposter/js/account-history.js?v=analytics-20261019');
 return [[],[{date:'2026-10-01',count:null}],[{date:'2026-10-01',count:0}],[{date:'2026-10-01',count:999999999999999}]].map(history=>{
 const n=historyChart({name:'<svg onload=alert(1)>',history});document.querySelector('main').replaceChildren(n);n.querySelector('details').open=true;
 return {circles:n.querySelectorAll('circle').length,lines:n.querySelectorAll('polyline').length,safe:!n.querySelector('svg svg'),fits:document.documentElement.scrollWidth<=innerWidth,rows:n.querySelectorAll('tbody tr').length};
 });
});assert.deepEqual(edgeCases.map(x=>x.circles),[0,0,1,1]);assert(edgeCases.every(x=>x.lines===0&&x.safe&&x.fits));
assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({results,errors,unexpected},null,2));await browser.close();console.log('Offline Chromium analytics passed: '+results.length+' widths')})().catch(e=>{console.error(e);process.exit(1)});
