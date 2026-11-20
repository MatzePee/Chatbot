const { chromium } = require('playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
const ROOT=path.resolve(__dirname,'..');
const A='11111111-1111-4111-8111-111111111111',B='22222222-2222-4222-8222-222222222222';
(async()=>{
 const script=fs.readFileSync(path.join(ROOT,'app/static/character-workspace.js'),'utf8');
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const context=await browser.newContext();
 const records=[];
 await context.route('**/*',async route=>{
  const req=route.request(),u=new URL(req.url());records.push({path:u.pathname,owner:u.searchParams.get('character_id'),method:req.method()});
  if(u.pathname==='/static/character-workspace.js')return route.fulfill({contentType:'application/javascript',body:script});
  if(u.pathname==='/api/v1/characters/mode')return route.fulfill({json:{enabled:true}});
  if(u.pathname==='/api/v1/characters')return route.fulfill({json:[{id:A,name:'Alpha'},{id:B,name:'Beta'}]});
  if(u.pathname==='/api/v1/views')return route.fulfill({json:[{name:u.searchParams.get('character_id')===A?'Only Alpha':'Only Beta'}]});
  return route.fulfill({contentType:'text/html',body:`<!doctype html><meta charset="utf-8"><style>body{display:flex;flex-direction:column;height:100vh;margin:0}nav{flex:1}main{flex:1;min-height:0;overflow:auto}</style><title>Workspace test</title><script src="/static/character-workspace.js"></script><main><a id="library" href="/autoposter/#/library">Bibliothek</a><form method="get" action="/chats"><input name="q" value="test"><button>Suchen</button></form><output id="result"></output></main>`});
 });
 const a=await context.newPage(),b=await context.newPage();
 await Promise.all([a.goto('http://offline.test/?character_id='+A),b.goto('http://offline.test/?character_id='+B)]);
 for(const [page,owner,label] of [[a,A,'Only Alpha'],[b,B,'Only Beta']]){
  await page.waitForSelector('[data-character-selector]');
  assert.equal(await page.locator('[data-character-selector]').inputValue(),owner);
  assert.equal(await page.locator('[data-character-create]').isVisible(),false,'Create form starts collapsed');
  const result=await page.evaluate(async()=>await (await fetch('/api/v1/views')).json());
  assert.equal(result[0].name,label);
  assert.equal(await page.evaluate(()=>localStorage.length),0);
  await page.locator('#library').click();
  assert.equal(new URL(page.url()).searchParams.get('character_id'),owner);
 }
 for(const width of [320,390,1280]){
  await a.setViewportSize({width,height:850});
  await a.waitForSelector('[data-character-selector]');
  const overflow=await a.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  assert.equal(overflow,false,`overflow at ${width}`);
  const barHeight=await a.locator('.character-workspace').evaluate(el=>el.getBoundingClientRect().height);
  assert(barHeight<200,`Character bar must not grow into body flex space at ${width}: ${barHeight}`);
  await a.screenshot({path:path.resolve(ROOT,'../ui-character-'+width+'.png'),fullPage:true});
 }
 await a.locator('[data-character-selector]').selectOption(B);
 await a.waitForURL(url=>url.searchParams.get('character_id')===B);
 assert.equal(new URL(b.url()).searchParams.get('character_id'),B);
 assert(records.some(r=>r.path==='/api/v1/views'&&r.owner===A));
 assert(records.some(r=>r.path==='/api/v1/views'&&r.owner===B));
 console.log(JSON.stringify({ok:true,tabs:2,widths:[320,390,1280],requests:records.length}));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
