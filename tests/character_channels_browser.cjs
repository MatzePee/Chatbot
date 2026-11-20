// Real Chromium, real channel renderer, synthetic APIs, every request fulfilled offline.
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const root=path.resolve(__dirname,'..'),out=path.resolve(root,'../evidence');
const A='11111111-1111-4111-8111-111111111111',B='22222222-2222-4222-8222-222222222222';
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const context=await browser.newContext(); const requests=[], errors=[];
 const html=`<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><script src="/static/character-workspace.js"></script><link rel="stylesheet" href="/autoposter/app.css"><link rel="stylesheet" href="/static/workspace.css"></head><body><main id="test-root" style="padding:8px"></main><div id="toasts"></div><div id="modals"></div><script type="module">import render from '/autoposter/js/pages/channels.js';document.querySelector('#test-root').append(await render());</script></body></html>`;
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname,owner=url.searchParams.get('character_id');
  requests.push({path:p,owner,method:req.method()});
  if(p==='/api/v1/characters/mode') return route.fulfill({json:{enabled:true}});
  if(p==='/api/v1/characters') return route.fulfill({json:[{id:A,name:'Alpha'},{id:B,name:'Beta'}]});
  if(p==='/api/v1/personas') return route.fulfill({json:[]});
  if(p==='/api/v1/auth/mode') return route.fulfill({json:{auth_enabled:false}});
  if(p==='/api/v1/auth/me') return route.fulfill({json:{id:'synthetic-admin',display_name:'Synthetic',role:'admin'}});
  if(p==='/api/v1/settings') return route.fulfill({json:{dry_run:true,global_pause:true}});
  if(p==='/api/creatorpilot/health' || p==='/health') return route.fulfill({json:{ok:true,preview:false}});
  if(p==='/autoposter/') return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'autoposter/ui/index.html'),'utf8')});
  if(p==='/api/v1/channels' && req.method()==='GET') return route.fulfill({json:[{
   id:'synthetic-channel',platform:'x',display_name:owner===A?'Alpha account':'Beta account',handle:'@synthetic',color:'#666666',
   health:'ok',is_active:true,is_connected:true,quota_used_today:0,persona_id:null,uses_own_app:true,oauth_client_id:'synthetic',has_client_secret:true,
   policy:{posts_per_day:2,daily_api_quota:100,allowed_time_windows:[{dow:[0,1],from:'09:00',to:'22:00'}],image_time_windows:[],text_time_windows:[]}
  }]});
  if(p.startsWith('/api/v1/channels/synthetic-channel')) return route.fulfill({json:{ok:true}});
  let local;
  if(p.startsWith('/autoposter/js/')) local=path.join(root,'autoposter/ui/js',p.slice('/autoposter/js/'.length));
  else if(p.startsWith('/static/')) local=path.join(root,'app/static',p.slice('/static/'.length));
  else if(p==='/autoposter/app.css') local=path.join(root,'autoposter/ui/app.css');
  if(local && fs.existsSync(local)) return route.fulfill({contentType:local.endsWith('.js')?'application/javascript':local.endsWith('.css')?'text/css':'text/plain',body:fs.readFileSync(local)});
  if(p==='/settings/shared') return route.fulfill({contentType:'text/html',body:html});
  throw Error('Unexpected offline request '+req.method()+' '+url);
 });
 const evidence=[];
 for(const width of [320,390,1280]) for(const owner of [A,B]){
  const page=await context.newPage({viewport:{width,height:900}});page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://offline.test/settings/shared?section=channels&character_id='+owner);
  await page.getByRole('button',{name:'Pausieren',exact:true}).waitFor();
  assert.equal(await page.getByText(owner===A?'Alpha account':'Beta account',{exact:true}).count(),1);
  await page.getByRole('button',{name:'Pausieren',exact:true}).click();
  await page.waitForEvent('domcontentloaded');
  assert.equal(new URL(page.url()).searchParams.get('character_id'),owner,'channel reload must retain document owner');
  await page.getByRole('button',{name:'Einstellungen',exact:true}).click();
  const dialog=page.locator('#modals .modal');await dialog.waitFor();
  const dimensions=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
  assert(dimensions.scroll<=dimensions.width,'mobile overflow '+JSON.stringify(dimensions));
  await page.screenshot({path:path.join(out,`channels-${width}-${owner[0]}.png`),fullPage:true});
  await page.keyboard.press('Escape');
  await dialog.waitFor({state:'detached'});
  await page.goto('http://offline.test/autoposter/?character_id='+owner+'#/channels');
  await page.waitForURL(url=>url.pathname==='/settings/shared');
  assert.equal(new URL(page.url()).searchParams.get('character_id'),owner,'legacy SPA settings redirect must retain owner');
  evidence.push({width,owner,paused:true,modalCancelled:true,dimensions});await page.close();
 }
 assert.equal(errors.length,0,errors.join('\n'));
 assert(requests.filter(x=>x.method!=='GET').every(x=>x.owner===A||x.owner===B));
 fs.writeFileSync(path.join(out,'channels-browser.json'),JSON.stringify({evidence,requests,errors},null,2));
 console.log(JSON.stringify({ok:true,workflows:evidence.length,widths:[320,390,1280]}));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
