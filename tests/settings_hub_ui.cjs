// Fully offline browser regression: actual templates/modules, synthetic API fixtures only.
// Run after SETTINGS_RENDER_OUTPUT=<dir> pytest tests/test_settings_hub.py.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict')
const repo = path.resolve(__dirname, '..'), render = process.env.SETTINGS_RENDER_OUTPUT
if (!render) throw Error('SETTINGS_RENDER_OUTPUT is required; never use production HTML/data')
const integrations = { openrouter_api_key_set: true, openrouter_ready: true,
  openrouter_model_caption:'fixture/caption', openrouter_model_text:'fixture/text',
  openrouter_model_vision:'fixture/vision', openrouter_model_fallback:'fixture/fallback',
  openrouter_monthly_budget_usd:30, x_api_tier:'basic', public_base_url:'https://fixture.test',
  fanvue_api_version:'v1', cross_channel_min_gap_minutes:7, inventory_warn_days:14,
  auto_plan_enabled:false, auto_plan_days:19, auto_plan_interval_hours:26 }
const channels = [{id:1,display_name:'Testkanal',platform:'x',auto_plan_enabled:true,is_active:true,is_connected:true,color:'#0071e3'}]
const fixtures = {
 '/api/v1/auth/me': {role:'admin',email:'fixture@test.invalid'},
 '/api/v1/settings': {dry_run:true,global_pause:true,port:8000,database:'synthetic.db',scheduler_mode:'off'},
 '/api/v1/settings/integrations': integrations,
 '/api/v1/maintenance/report': {unassigned:0,untagged:0,archive_candidates:0,failed_assets:0,orphan_set_items:0,near_duplicates:[]},
 '/api/v1/llm/usage': {total_usd:2,budget_usd:30,by_task:[]},
 '/api/v1/channels': channels,
 '/api/v1/settings/image-comment':{enabled:true,text:'Fixture comment',url:'https://fixture.test'},
 '/api/v1/settings/openrouter/models':{models:Object.values(integrations).filter(v => typeof v==='string' && v.startsWith('fixture/')).map(id=>({id,vision:true,free:true})),cached:true},
 '/api/deployment-status': {},
 '/api/update-check': {latest:null,update_available:false},
 '/settings/time-preview': {block:'Synthetic time preview'},
}
const writes=[], unexpected=[], errors=[]
async function main(){
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']})
 const page=await browser.newPage({viewport:{width:1440,height:1000}})
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/*',async route=>{
  const req=route.request(), url=new URL(req.url()), p=url.pathname
  if(url.hostname!=='settings.test'){unexpected.push(req.url());return route.abort()}
  let body, contentType='application/json'
  if(p==='/settings/time-preview') return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(fixtures[p])})
  if(req.method()!=='GET'){
   writes.push({path:p,method:req.method(),payload:req.postDataJSON?.()})
   if(p==='/api/v1/settings/integrations') body={...integrations,...req.postDataJSON()}
   else if(p==='/api/v1/settings/image-comment') body=req.postDataJSON()
   else if(p==='/api/v1/settings/integrations/clear') body={ok:true}
   else if(p==='/api/v1/maintenance/delete-planned') body={would_delete:3,kept_published:2,by_channel:{Testkanal:3}}
   else if(p==='/api/v1/channels/1') body={...channels[0],...req.postDataJSON()}
   else {unexpected.push(req.method()+' '+p);return route.abort()}
  }else if(fixtures[p])body=fixtures[p]
  else if(p==='/settings/shared' || p==='/settings'){
   const section=p==='/settings'?'autochat':url.searchParams.get('section')||'shared'
   body=fs.readFileSync(path.join(render,section+'.html'));contentType='text/html'
  }else if(p==='/baseline'){
   body=`<html><body><div id="toasts"></div><div id="modals"></div><div id="baseline"></div><script type="module">import render from '/autoposter/js/pages/settings.js';document.getElementById('baseline').appendChild(await render({user:{role:'admin',email:'fixture@test.invalid'}}));</script></body></html>`;contentType='text/html'
  }else{
   let f
   if(p.startsWith('/static/'))f=path.join(repo,'app',p)
   else if(p.startsWith('/autoposter/js/'))f=path.join(repo,'autoposter/ui',p.slice('/autoposter/'.length))
   if(f && fs.existsSync(f) && ['.js','.css','.svg'].includes(path.extname(f))){body=fs.readFileSync(f);contentType=({'.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(f)]}
   else {unexpected.push(p);return route.abort()}
  }
  return route.fulfill({status:200,contentType,body:Buffer.isBuffer(body)||typeof body==='string'?body:JSON.stringify(body)})
 })
 const inventory=async selector=>page.locator(selector).evaluate(node=>[...node.querySelectorAll('input,select,textarea,button,a')].map(el=>({tag:el.tagName,type:el.type||'',text:['BUTTON','A'].includes(el.tagName)?el.textContent.trim():'',value:['INPUT','SELECT','TEXTAREA'].includes(el.tagName)?el.value:undefined,checked:el.type==='checkbox'?el.checked:undefined,disabled:el.disabled||false})))
 await page.goto('http://settings.test/baseline');await page.locator('#baseline section').last().waitFor()
 const baseline=await inventory('#baseline')
 await page.goto('http://settings.test/settings/shared?section=autopost');await page.locator('#autopost-settings section').last().waitFor()
 assert.deepEqual(await inventory('#autopost-settings'),baseline,'Every old AutoPost control/action and prepopulation must survive')
 assert.equal(writes.length,0,'Loading settings must never save')
 assert.equal(await page.locator('#autopost-settings a[href="/autoposter/#/channels"]').count(),2)
 const openrouter=page.locator('#autopost-settings section').filter({has:page.getByRole('heading',{name:'OpenRouter verbinden',exact:true})})
 await openrouter.getByRole('button',{name:'Speichern',exact:true}).click()
 assert.equal(writes.at(-1).path,'/api/v1/settings/integrations')
 assert.equal(Object.hasOwn(writes.at(-1).payload,'openrouter_api_key'),false,'Blank secret must be omitted')
 assert.equal(writes.at(-1).payload.openrouter_model_text,'fixture/text')
 const planning=page.locator('#autopost-settings section').filter({has:page.getByRole('heading',{name:'Planung',exact:true})})
 await planning.getByRole('button',{name:'Speichern',exact:true}).click()
 assert.deepEqual(writes.at(-1).payload,{cross_channel_min_gap_minutes:7,inventory_warn_days:14,auto_plan_enabled:false,auto_plan_days:19,auto_plan_interval_hours:26})
 await openrouter.getByRole('button',{name:'Schlüssel entfernen'}).click()
 await page.locator('#modals .modal').waitFor()
 await page.getByRole('button',{name:'Abbrechen',exact:true}).click()
 assert.equal(writes.filter(w=>w.path.endsWith('/clear')).length,0,'Cancelling removal must not change any secret')
 const calendar=page.locator('#autopost-settings section').filter({has:page.getByRole('heading',{name:'Kalender leeren',exact:true})})
 await calendar.getByRole('button',{name:'Alle geplanten Posts löschen'}).click()
 await page.locator('#modals .modal').waitFor()
 await page.getByRole('button',{name:'Abbrechen',exact:true}).click()
 assert.equal(writes.at(-1).path,'/api/v1/maintenance/delete-planned')
 // It is a counting preview only; cancellation must not send dry_run=false.
 assert.equal(writes.filter(w=>w.path.includes('delete-planned')).length,1)
 await page.screenshot({path:path.join(render,'autopost-desktop.png'),fullPage:false})
 const mutations=writes.length
 await page.locator('.settings-tabs').getByRole('link',{name:'AutoChat',exact:true}).click()
 await page.locator('[name="system_prompt"]').waitFor({state:'attached'})
 assert.equal(writes.length,mutations,'Tab navigation cannot save')
 await page.locator('#sect-all').click()
 assert.equal(await page.locator('details.sect:not([open])').count(),0)
 assert.equal(await page.locator('form form').count(),0,'Forms must never nest')
 await page.screenshot({path:path.join(render,'autochat-desktop.png'),fullPage:false})
 await page.locator('.settings-tabs').getByRole('link',{name:'Gemeinsam',exact:true}).click()
 assert.equal(await page.locator('form form').count(),0)
 await page.screenshot({path:path.join(render,'shared-desktop.png'),fullPage:false})
 await page.setViewportSize({width:390,height:844})
 for(const section of ['shared','autochat','autopost']){
  await page.goto('http://settings.test/settings/shared?section='+section)
  if(section==='autopost') await page.locator('#autopost-settings section').last().waitFor()
  assert.equal(await page.locator('[data-settings-nav] a.active').getAttribute('href'),'/settings/shared?section='+section,'Sidebar must select actual settings category')
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'No mobile horizontal overflow: '+section)
  await page.screenshot({path:path.join(render,section+'-mobile.png'),fullPage:false})
 }
 assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[])
 console.log(JSON.stringify({autopost_controls_actions:baseline.length,mutations_on_load:0,synthetic_save_checks:writes.length,screenshots:6,errors,unexpected},null,2))
 await browser.close()
}
main().catch(error=>{console.error(error);process.exit(1)})
