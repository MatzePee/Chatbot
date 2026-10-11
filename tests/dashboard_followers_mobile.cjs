// Offline Chromium: real dashboard/DOM helpers/styles; synthetic metrics only.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const repo=path.resolve(__dirname,'..'),out=process.env.FOLLOWERS_RENDER_OUTPUT || '/root/mobile-followers-render';
const name='Sehr_langer_Accountname_mit_vielen_Zeichen_und_ohne_Trennstellen';
const status='Nicht verfügbar – X-Tarif, Berechtigungen oder Token prüfen. '+ 'LangerFehlerOhneLeerzeichen'.repeat(6)+' <img src=x onerror="window.xss=true">';
const accounts=[{name,current:1234567890,yesterday:{count:1234567880,growth:10},days7:{count:null,growth:null},days30:{count:1234567990,growth:-100},sampled_at:'2026-10-11T10:00:00Z',stale:true,status},
{name:'<script>window.xss=true</script>',current:null,yesterday:{count:null,growth:null},days7:{count:0,growth:null},days30:{count:0,growth:0},sampled_at:null,stale:false,status:'Keine Messung verfügbar'}];
const dashboard={inventory:{channels:[],unassigned_assets:0},lifecycle_counts:{},upcoming_posts:[],channel_health:[],notifications:[],recent_failures:[]};
(async()=>{
 fs.mkdirSync(out,{recursive:true});const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage();const errors=[],unexpected=[],results=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',async route=>{
  const url=new URL(route.request().url()),p=url.pathname;
  if(url.hostname!=='followers.test'){unexpected.push(url.href);return route.abort()}
  let body,type='text/javascript';
  if(p==='/') {type='text/html';body=`<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/autoposter/app.css"><link rel="stylesheet" href="/static/workspace.css"><body><main class="page" id="fixture"></main><script type="module">import render from '/autoposter/js/pages/dashboard.js';const page=await render();window.rendered=page.outerHTML;const section=[...page.querySelectorAll('section')].find(n=>n.querySelector('h2')?.textContent==='X-Follower · Stand und Wachstum');document.getElementById('fixture').append(section);window.ready=true;</script></body></html>`}
  else if(p==='/autoposter/js/api.js')body=`export const api={dashboard:async()=>(${JSON.stringify(dashboard)}),xFollowers:async()=>(${JSON.stringify(accounts)}),jobs:async()=>({jobs:[]})};`;
  else {let file=p.startsWith('/autoposter/')?path.join(repo,'autoposter/ui',p.slice(12)):p.startsWith('/static/')?path.join(repo,'app',p):null;
   if(!file || !fs.existsSync(file)){unexpected.push(p);return route.abort()}
   body=fs.readFileSync(file);type=p.endsWith('.css')?'text/css':'text/javascript';
  }return route.fulfill({status:200,contentType:type,body});
 });
 await page.goto('http://followers.test/');await page.waitForFunction(()=>window.ready);
 for(const width of [320,375,390,430,1280]){
  await page.setViewportSize({width,height:1100});
  const section=page.locator('#fixture > section'),mobile=width<761;
  // Collect old-code failures at all widths instead of stopping after the first.
  const checks=await section.evaluate((el,{mobile,name,status})=>{
   const visible=n=>!!(n && n.getClientRects().length);
   const cards=[...el.querySelectorAll('.x-followers-account')];const table=el.querySelector('table');
   const overflowing=[el,...el.querySelectorAll('*')].filter(n=>visible(n) && n.scrollWidth>n.clientWidth+1 && n.clientWidth>0).map(n=>n.tagName+'.'+n.className);
   return {noOverflow:!overflowing.length,overflowing,cards:cards.filter(visible).length,tableVisible:visible(table),tiles:cards.filter(visible).map(n=>n.querySelectorAll('.x-followers-metric').length),safe:!el.querySelector('img,script'),fullName:el.textContent.includes(name),fullStatus:el.textContent.includes(status)};
  },{mobile,name,status});
  await section.screenshot({path:path.join(out,`followers-${width}.png`)});
  const ax=await section.ariaSnapshot();fs.writeFileSync(path.join(out,`followers-${width}-aria.yml`),ax);
  const axNameOccurrences=ax.split('\n').filter(line=>line.includes(name) && (mobile ? /^\s*- heading /.test(line) : /^\s*- cell /.test(line))).length;
  const documentFits=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
  const expectedHeaders=['Account','Aktuell','Gestern (Δ)','Vor 7 Tagen (Δ)','Vor 30 Tagen (Δ)','Messzeit / Status'];
  assert.deepEqual(await section.locator('th').allTextContents(),expectedHeaders,'Desktop columns must stay unchanged');
  for(const value of [name,status,'1234567890','1234567880 (+10)','1234567990 (-100)','Keine Historie','Keine Messung','0 (Wachstum unbekannt)','0 (+0)','Zeitpunkt unbekannt','veraltet']) assert(ax.replaceAll('\\"','"').includes(value),`Visible/accessibility data lost at ${width}: ${value}`);
  assert.equal(ax.includes('- table:'),!mobile,'Only the active representation reaches screen readers');
  results.push({width,...checks,documentFits,axNameOccurrences,pass:documentFits && (!mobile || checks.noOverflow) && checks.cards===(mobile?2:0) && checks.tableVisible===!mobile && (!mobile || checks.tiles.every(n=>n===4)) && checks.safe && checks.fullName && checks.fullStatus && axNameOccurrences===1});
 }
 assert.equal(await page.evaluate(()=>window.xss===true),false);
 // Empty response still uses the existing honest empty state.
 await page.route('**/autoposter/js/api.js*',route=>route.fulfill({contentType:'text/javascript',body:`export const api={dashboard:async()=>(${JSON.stringify(dashboard)}),xFollowers:async()=>[],jobs:async()=>({jobs:[]})};`}));
 await page.reload();await page.waitForFunction(()=>window.ready);assert.match(await page.locator('#fixture').innerText(),/Keine X-Accounts\./);
 await browser.close();
 const report={results,errors,unexpected,screenshots:results.map(r=>path.join(out,`followers-${r.width}.png`))};fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);assert(results.every(r=>r.pass),'Responsive follower cards / overflow / single accessible representation failed');
})().catch(e=>{console.error(e);process.exit(1)});
