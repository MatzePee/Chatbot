const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const root=path.resolve(__dirname,'..'),out=path.resolve(root,'../evidence/settings-reorg'),render=path.join(out,'render');
const owners=JSON.parse(fs.readFileSync(path.join(render,'owners.json'))),writes=[],errors=[],scenarios=[];
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});try{
const context=await browser.newContext();await context.route('**/*',async route=>{
 const req=route.request(),u=new URL(req.url()),p=u.pathname,oid=u.searchParams.get('character_id');
 assert.equal(u.hostname,'offline.test');const json=x=>route.fulfill({json:x});
 if(p==='/api/v1/characters/mode')return json({enabled:true});if(p==='/api/v1/characters')return json(owners);
 if(p==='/settings/shared/ai'){writes.push({oid,fields:Object.fromEntries(new URLSearchParams(req.postData()))});return route.fulfill({contentType:'text/html',body:'<p id="saved">Saved</p>'});}
 if(p==='/api/v1/settings/integrations'&&req.method()!=='GET'){writes.push({oid,payload:req.postDataJSON()});return json(req.postDataJSON());}
 const fixtures={'/api/v1/settings/openrouter/models':{models:[],cached:true},'/api/v1/auth/me':{role:'admin'},'/api/v1/channels':[],'/api/v1/personas':[], '/api/v1/settings':{dry_run:true,global_pause:true},'/api/v1/settings/integrations':{auto_plan_days:14,openrouter_monthly_budget_usd:100},'/api/v1/maintenance/report':{},'/settings/shared/ai-usage': {total_usd:0,budget_usd:100,by_task:[]}, '/api/v1/llm/usage':{total_usd:0,budget_usd:100,by_task:[]},'/api/v1/settings/image-comment':{},'/api/v1/settings/visibility-aging':{channels:[]},'/api/v1/settings/post-retention':{accounts:[]},'/api/v1/settings/ppv-restore':{accounts:[]},'/api/deployment-status':{},'/api/update-check':{},'/api/v1/system/character-upgrade':{phase:'legacy'},'/settings/time-preview':{block:'synthetic'}};
 if(Object.hasOwn(fixtures,p))return json(fixtures[p]);
 if(p==='/settings/shared')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(render,oid+'-'+(u.searchParams.get('section')||'shared')+'.html'))});
 let f;if(p.startsWith('/static/'))f=path.join(root,'app',p);if(p.startsWith('/autoposter/js/'))f=path.join(root,'autoposter/ui',p.slice('/autoposter/'.length));
 if(f&&fs.existsSync(f))return route.fulfill({contentType:f.endsWith('.js')?'application/javascript':'text/css',body:fs.readFileSync(f)});
 throw Error('Unexpected '+p);
});
const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
for(const width of [320,390,1280])for(const owner of owners)for(const section of ['shared','autochat','autopost']){
 await page.setViewportSize({width,height:900});await page.goto(`http://offline.test/settings/shared?section=${section}&character_id=${owner.id}`);
 if(section==='autopost')await page.locator('#autopost-settings section').last().waitFor();
 assert.equal(await page.locator('[data-settings-scope]').innerText(),section==='shared'?'Gilt für alle Charaktere':'Gilt für: '+owner.name);
 if(section==='autopost'){
  assert.equal(await page.getByRole('heading',{name:'OpenRouter verbinden',exact:true}).count(),0);
  assert.equal(await page.getByRole('heading',{name:'Plattform-Vorgaben',exact:true}).count(),0);
  assert.equal(await page.getByRole('heading',{name:'System',exact:true}).count(),0);
  assert.equal(await page.locator('#autopost-settings a[href*="#ai"]').count(),1);
 }
 if(section==='autochat'){
  assert.equal(await page.locator('[name=fanvue_client_secret]').count(),1);
  assert.equal(await page.locator('[name=vision_model]').count(),0);
 }
 assert.equal(await page.locator('form form').count(),0);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),section+' overflow '+width);
 await page.screenshot({path:path.join(out,`${section}-${width}-${owner.name}.png`),fullPage:true});
 scenarios.push({width,owner:owner.name,section});
}
const owner=owners[0];await page.goto(`http://offline.test/settings/shared?character_id=${owner.id}`);
await page.locator('[name=vision_model]').fill('qwen/qwen3-vl-235b-a22b-instruct');
await page.locator('[name=openrouter_model_vision]').fill('openai/gpt-5.6-luna-pro');
await page.locator('[name=openrouter_api_key]').fill('');await page.getByRole('button',{name:'KI-Einstellungen speichern',exact:true}).click();await page.locator('#saved').waitFor();
assert.equal(writes.at(-1).oid,owner.id);assert.equal(writes.at(-1).fields.openrouter_api_key,'');assert.notEqual(writes.at(-1).fields.vision_model,writes.at(-1).fields.openrouter_model_vision);
assert(!Object.hasOwn(writes.at(-1).fields,'fanvue_client_secret'));
assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify({scenarios,writes,errors,visual_review:false},null,2));console.log(JSON.stringify({passed:true,scenarios:scenarios.length}));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
