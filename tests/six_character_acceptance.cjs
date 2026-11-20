// Real Chromium, actual rendered settings + real modules; all HTTP routed offline.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const root=path.resolve(__dirname,'..'),out=path.resolve(root,'../evidence/six-character-acceptance'),render=path.join(out,'render');
const owners=JSON.parse(fs.readFileSync(path.join(render,'owners.json')));
const logs=[],errors=[],scenarios=[],writes=[];
let available=owners.slice(0,5),uploadOwner=null,releaseUpload,uploadStarted;
const pendingUpload=new Promise(r=>uploadStarted=r),uploadRelease=new Promise(r=>releaseUpload=r);
const state=Object.fromEntries(owners.map((o,i)=>[o.id,{client:`synthetic-app-${i}`,secret:true}]));
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try{
 const context=await browser.newContext();
 await context.route('**/*',async route=>{
  const req=route.request(),u=new URL(req.url()),p=u.pathname,owner=u.searchParams.get('character_id');
  logs.push({path:p,owner,method:req.method()});
  if(u.hostname!=='offline.test')throw Error('External request '+u.origin);
  const json=body=>route.fulfill({json:body});
  if(p==='/api/v1/characters/mode')return json({enabled:true});
  if(p==='/api/v1/characters'){
   if(req.method()==='POST'){assert.equal(req.postDataJSON().name,'Fern');available=owners;return route.fulfill({status:201,json:owners[5]});}
   return json(available);
  }
  if(p==='/settings/shared' || p==='/settings'){
   const section=p==='/settings'?'autochat':u.searchParams.get('section')||'shared';
   assert(state[owner],'missing/foreign HTML owner');
   let html=fs.readFileSync(path.join(render,`${owner}-${section}.html`),'utf8');
   html=html.replace(/name="fanvue_client_id" value="[^"]*"/,`name="fanvue_client_id" value="${state[owner].client}"`);
   if(!state[owner].secret)html=html.replace(/name="fanvue_client_secret" type="password" value="[^"]*"/,'name="fanvue_client_secret" type="password" value=""');
   return route.fulfill({contentType:'text/html',body:html});
  }
  if(p==='/settings/shared/fanvue'){
   assert(state[owner]); const data=new URLSearchParams(req.postData());writes.push({path:p,owner,fields:[...data.keys()]});
   state[owner].client=data.get('fanvue_client_id');if(data.has('clear_fanvue_client_secret'))state[owner].secret=false;
   return route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><p id="saved">Gespeichert</p>'});
  }
  if(p==='/fixture-upload')return route.fulfill({contentType:'text/html',body:`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/autoposter/app.css"><script src="/static/character-workspace.js"></script><main style="padding:8px"></main><div id="toasts"></div><div id="modals"></div><script type="module">import {createDropZone} from '/autoposter/js/upload.js';document.querySelector('main').append(createDropZone().root);</script>`});
  if(p==='/api/v1/media/upload'){
   uploadOwner=owner;uploadStarted();await uploadRelease;
   return route.fulfill({status:201,json:{created:[],duplicates:[],errors:[],summary:{created:1,duplicates:0,errors:0,assigned:0}}});
  }
  if(p==='/settings/time-preview')return json({block:'Synthetic offline time preview'});
  if(p==='/api/v1/settings/integrations')return json(JSON.parse(fs.readFileSync(path.join(render,owner+'-integrations.json'))));
  const fixtures={
   '/api/v1/system/character-upgrade':{phase:'legacy'},'/api/v1/auth/me':{role:'admin'},'/api/v1/auth/mode':{auth_enabled:false},
   '/api/v1/settings':{dry_run:true,global_pause:true},'/api/v1/channels':[], '/api/v1/personas':[],
   '/api/v1/maintenance/report':{unassigned:0,untagged:0,archive_candidates:0,failed_assets:0,orphan_set_items:0,near_duplicates:[]},
   '/settings/shared/ai-usage': {total_usd:0,budget_usd:100,by_task:[]}, '/api/v1/llm/usage':{total_usd:0,budget_usd:30,by_task:[]},'/api/v1/settings/image-comment':{enabled:false,text:'',url:''},
   '/api/v1/settings/openrouter/models':{models:[],cached:true},'/api/v1/settings/visibility-aging':{enabled:false,days:7,active:false,status:'idle',channels:[]},
   '/api/v1/settings/post-retention':{active:false,automatic_active:false,accounts:[]},'/api/v1/settings/ppv-restore':{active:false,accounts:[]},
   '/api/deployment-status':{},'/api/update-check':{latest:null,update_available:false},'/api/creatorpilot/health':{ok:true},'/health':{ok:true}
  };
  if(Object.hasOwn(fixtures,p))return json(fixtures[p]);
  let local;
  if(p.startsWith('/static/'))local=path.join(root,'app',p);
  if(p.startsWith('/autoposter/js/'))local=path.join(root,'autoposter/ui',p.slice('/autoposter/'.length));
  if(p==='/autoposter/app.css')local=path.join(root,'autoposter/ui/app.css');
  if(local&&fs.existsSync(local))return route.fulfill({contentType:local.endsWith('.js')?'application/javascript':'text/css',body:fs.readFileSync(local)});
  throw Error('Unexpected request '+req.method()+' '+p);
 });
 const a=await context.newPage(),b=await context.newPage();
 for(const p of [a,b])p.on('pageerror',e=>errors.push(e.message));
 await a.goto('http://offline.test/settings/shared?character_id='+owners[0].id);
 await a.locator('[data-character-selector]').waitFor();
 await a.locator('[data-character-management]').waitFor({state:'visible'});
 await a.getByRole('textbox',{name:'Neuer Character-Name'}).fill('Fern');
 await a.getByRole('button',{name:'Leer und deaktiviert erstellen'}).click();
 await a.waitForFunction(()=>document.querySelectorAll('[data-character-selector] option').length===6);
 await a.locator('[data-character-selector]').selectOption(owners[5].id);
 await a.waitForURL(u=>u.searchParams.get('character_id')===owners[5].id);
 // Creation remains in settings; switching is an explicit user action.
 await a.locator('[data-character-selector]').waitFor();assert.equal(await a.locator('[data-character-selector] option').count(),6);
 for(const width of [1280,320,390]){
  await a.setViewportSize({width,height:900});
  for(const owner of owners){
   await a.goto('http://offline.test/settings/shared?character_id='+owner.id);
   await a.locator('[data-character-selector]').waitFor();
   await a.goto('http://offline.test/settings/shared?section=autochat&character_id='+owner.id);
   assert.equal(await a.locator('[name=fanvue_client_id]').inputValue(),state[owner.id].client);
   assert.equal(await a.locator('[name=fanvue_client_secret]').inputValue(),'********');
   assert.equal(await a.locator('[data-character-management]').count(),0,'Owner page must not expose global character management');
   assert.equal(await a.locator('.workspace-topbar [data-character-create]').count(),0);
   const geometry=await a.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,selector:document.querySelector('[data-character-selector]').getBoundingClientRect().toJSON()}));
   assert(geometry.scroll<=width,JSON.stringify(geometry));assert(geometry.selector.width>40);
   await a.screenshot({path:path.join(out,`settings-${width}-${owner.name}.png`),fullPage:true});
   scenarios.push({flow:'six-owner-settings-geometry',width,owner:owner.name,geometry});
  }
 }
 const A=owners[0].id,B=owners[1].id;
 await Promise.all([a.goto('http://offline.test/settings/shared?section=autochat&character_id='+A),b.goto('http://offline.test/settings/shared?section=autochat&character_id='+B)]);
 await a.locator('[data-character-selector]').waitFor();await b.locator('[data-character-selector]').waitFor();
 await a.locator('[name=fanvue_client_id]').fill('unsaved-stale-A');
 await a.locator('[data-character-selector]').selectOption(B);
 await a.waitForURL(u=>u.searchParams.get('character_id')===B);
 assert.equal(await a.locator('[name=fanvue_client_id]').inputValue(),state[B].client);
 assert.equal(new URL(b.url()).searchParams.get('character_id'),B);
 await a.locator('[name=fanvue_client_secret]').fill('');
 await a.getByRole('button',{name:'Fanvue-Einstellungen speichern',exact:true}).click();
 await a.locator('#saved').waitFor();assert(state[B].secret);
 assert.equal(new URL(a.url()).searchParams.get('character_id'),B);
 await a.goto('http://offline.test/settings/shared?section=autochat&character_id='+B);
 await a.locator('[name=clear_fanvue_client_secret]').check();
 await a.getByRole('button',{name:'Fanvue-Einstellungen speichern',exact:true}).click();
 await a.locator('#saved').waitFor();
 await a.goto('http://offline.test/settings/shared?section=autochat&character_id='+B);
 await a.waitForFunction(()=>document.querySelector('[name=fanvue_client_secret]')?.value==='');
 assert.equal(state[B].secret,false);assert.equal(state[A].secret,true);
 for(const section of ['autochat','autopost']){
  await b.locator('.settings-tabs').getByRole('link',{name:section==='autochat'?'AutoChat':'AutoPost',exact:true}).click();
  await b.waitForURL(u=>u.searchParams.get('section')===section);
  assert.equal(new URL(b.url()).searchParams.get('character_id'),B);
  if(section==='autochat')await b.locator('[name=system_prompt]').waitFor({state:'attached'});
  else await b.locator('#autopost-settings section').last().waitFor();
  const geometry=await b.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert(geometry.scroll<=geometry.width);
  await b.screenshot({path:path.join(out,section+'-actual.png'),fullPage:true});
 }
 await a.goto('http://offline.test/fixture-upload?character_id='+A);
 await a.locator('input[type=file]').setInputFiles({name:'synthetic.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jfFoAAAAASUVORK5CYII=','base64')});
 await a.getByRole('button',{name:'Hochladen',exact:true}).click();await pendingUpload;
 await a.locator('[data-character-selector]').selectOption(B);
 await a.waitForURL(u=>u.searchParams.get('character_id')===B);releaseUpload();
 assert.equal(uploadOwner,A,'pending upload request cannot become B');
 assert.equal(new URL(b.url()).searchParams.get('character_id'),B);
 assert.equal(errors.length,0,errors.join('\n'));
 scenarios.push({flow:'create-sixth',passed:true},{flow:'two-tabs-stale-form-discarded',passed:true},{flow:'blank-retain-explicit-clear-one-owner',passed:true},{flow:'actual-settings-tab-owner',passed:true},{flow:'actual-uploader-delayed-switch',originalOwner:A,passed:true});
 fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify({scenarios,logs,writes,errors,visual_review:false},null,2));
 console.log(JSON.stringify({passed:true,scenarios:scenarios.length,screenshots:20,visual_review:false}));
 }finally{fs.writeFileSync(path.join(out,'browser-attempt.json'),JSON.stringify({logs,writes,errors,scenarios},null,2));await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
