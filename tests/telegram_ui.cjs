// Real modules, Chromium, fully offline synthetic channel/settings data.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const fs=require('fs'),path=require('path'),assert=require('assert/strict')
const repo=path.resolve(__dirname,'..'),out=process.env.TELEGRAM_RENDER_OUTPUT || '/tmp/telegram-ui';fs.mkdirSync(out,{recursive:true})
const policy={posts_per_day:2,min_gap_minutes:180,jitter_minutes:12,max_images_per_post:10,text_only_ratio:.3,allowed_time_windows:[{dow:[0,1,2,3,4,5,6],from:'09:00',to:'22:00'}],image_time_windows:[],text_time_windows:[],daily_api_quota:100,reuse_cooldown_days:0,phash_min_distance:6,phash_lookback_posts:20,stagger_minutes:0,auto_approve:false,auto_retry_on_fail:false}
const channel={id:'tg',platform:'telegram',display_name:'<img src=x onerror=alert(1)> '+ 'LongName'.repeat(15),handle:'@sample_channel',color:'#229ed9',health:'needs_reauth',health_note:'Noch nicht verbunden',is_active:false,is_connected:false,policy,oauth_client_id:'',has_bot_token:true}
const writes=[],unexpected=[],errors=[]
;(async()=>{
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});const page=await browser.newPage()
page.on('pageerror',e=>errors.push(e.message));page.on('dialog',()=>{throw Error('XSS dialog')})
await page.route('**/*',async route=>{
 const req=route.request(),u=new URL(req.url()),p=u.pathname
 if(u.hostname!=='telegram.test'){unexpected.push(req.url());return route.abort()}
 if(req.method()!=='GET'){
  const body=req.postDataJSON();writes.push({path:p,method:req.method(),body})
  if(p==='/api/v1/channels/tg/telegram')return route.fulfill({json:channel})
  if(p==='/api/v1/channels/tg/test')return route.fulfill({json:{ok:false,channel_name:'synthetic',platform:'telegram',message:'Nicht eingerichtet',step:'telegram'}})
  if(p==='/api/v1/channels/tg')return route.fulfill({json:{...channel,...body}})
  if(p==='/api/v1/channels')return route.fulfill({json:{...channel,...body}})
  if(p==='/api/v1/posts/p/publish')return route.fulfill({json:{ok:false,message:'synthetic disconnected'}})
  unexpected.push(req.method()+' '+p);return route.abort()
 }
 if(p==='/api/v1/calendar/month')return route.fulfill({json:{posts:[],by_day:{},blackouts:[]}})
 if(p==='/api/v1/runs')return route.fulfill({json:[]})
 if(p==='/api/v1/channels')return route.fulfill({json:[channel]})
 if(p==='/api/v1/personas')return route.fulfill({json:[]})
 if(p==='/api/v1/posts/p/preflight')return route.fulfill({json:{issues:[]}})
 if(p==='/')return route.fulfill({contentType:'text/html',body:`<!doctype html><meta charset=utf-8><link rel=stylesheet href=/autoposter/app.css><div id=toasts></div><div id=modals></div><div id=root></div><script type=module>import render from '/autoposter/js/pages/channels.js';import {openPostEditor} from '/autoposter/js/posteditor.js';window.CreatorStudioLive={busy:()=>false,begin:()=>{},end:()=>{}};document.querySelector('#root').appendChild(await render());window.edit=()=>openPostEditor({id:'p',channel_id:'tg',body_text:'Independent preview',hashtags:[],media_asset_ids:['photo'],generation_meta:{telegram_delivery:{state:'uncertain'}}},${JSON.stringify([channel])});</script>`})
 if(p.includes('/thumb'))return route.fulfill({status:404,body:''})
 if(p.startsWith('/autoposter/')){let f=path.join(repo,'autoposter/ui',p.slice('/autoposter/'.length));if(fs.existsSync(f))return route.fulfill({contentType:f.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(f)})}
 unexpected.push(p);return route.abort()
})
for(const width of [320,390,1280]){
 await page.setViewportSize({width,height:900});await page.goto('http://telegram.test/');await page.getByRole('button',{name:'Kanal anlegen',exact:true}).waitFor()
 assert.equal(await page.locator('#root img').count(),0,'Malicious channel name stays text')
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Channel overflow '+width)
 await page.getByRole('button',{name:'Kanal anlegen',exact:true}).click()
 await page.getByLabel('Plattform',{exact:true}).selectOption('telegram')
 assert.equal(await page.getByText('Client-ID',{exact:true}).isVisible(),false)
 assert.match(await page.locator('.modal').innerText(),/Telegram/)
 await page.screenshot({path:`${out}/create-${width}.png`})
 await page.keyboard.press('Escape')
 // Modal uses backdrop/close button; reload is intentionally read-only.
 await page.goto('http://telegram.test/');await page.getByRole('button',{name:'Bot-Zugang',exact:true}).click()
 const token=page.getByLabel('Bot-Token',{exact:true});assert.equal(await token.inputValue(),'');assert.equal(await token.getAttribute('type'),'password')
 await page.getByRole('button',{name:'Speichern',exact:true}).click();await page.waitForTimeout(100)
 const saved=writes.filter(w=>w.path.endsWith('/telegram')).at(-1);assert.equal(saved.body.bot_token,'');assert.equal(saved.body.destination,'@sample_channel')
 await page.getByRole('button',{name:'Bot-Zugang',exact:true}).waitFor()
 await page.getByRole('button',{name:'Testen',exact:true}).click()
 await page.locator('#modals').getByText('Nicht eingerichtet',{exact:true}).waitFor()
 assert.equal((await page.locator('.modal').innerText()).includes('globale App'),false)
 assert.match(await page.locator('.modal').innerText(),/Kanal-ID/)
 await page.keyboard.press('Escape')
 await page.getByRole('button',{name:'Einstellungen',exact:true}).click()
 assert.equal(await page.getByText('Fanvue verwendet die gemeinsame Verbindung.',{exact:false}).count(),0)
 assert.match(await page.locator('.modal').innerText(),/Telegram/)
 const guidelines=page.getByLabel('Telegram-Textvorgaben',{exact:true});await guidelines.fill('Nur für diesen Telegram-Kanal.')
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Policy overflow '+width)
 await page.screenshot({path:`${out}/policy-${width}.png`})
 await page.goto('http://telegram.test/');await page.waitForFunction(()=>window.edit);await page.evaluate(()=>window.edit())
 await page.getByText('Telegram · 1024 Zeichen',{exact:false}).waitFor()
 assert.equal(await page.getByText('Sichtbarkeit',{exact:true}).count(),0)
 assert.match(await page.locator('.modal').innerText(),/unklar/)
 await page.locator('.modal textarea').first().fill('x'.repeat(1020));await page.locator('.modal input').first().fill('tag')
 await page.getByText('1026 / 1024 Zeichen',{exact:true}).waitFor()
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Preview overflow '+width)
 await page.screenshot({path:`${out}/preview-${width}.png`})
 await page.goto('http://telegram.test/');await page.waitForFunction(()=>window.edit)
 await page.evaluate(async()=>{const {default:render}=await import('/autoposter/js/pages/calendar.js');document.querySelector('#root').replaceChildren(await render())})
 await page.getByRole('button',{name:'⚡ Telegram planen',exact:true}).click()
 assert.match(await page.locator('.modal-head').innerText(),/Telegram/)
 assert.equal(await page.getByText('Sub-Posts pro Tag',{exact:true}).count(),0)
 assert.equal(await page.locator('.modal').evaluate(el=>el.scrollWidth>el.clientWidth),false,'Planner modal overflow '+width)
 await page.screenshot({path:`${out}/planner-${width}.png`})
}
assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[])
assert.equal(writes.some(w=>w.path.includes('oauth')),false)
await page.evaluate(async()=>{
 const {mediaPreview}=await import('/autoposter/js/ui.js');
 const video=mediaPreview({mime:'video/mp4',url:'/synthetic.mp4',thumb_url:'/thumb.jpg'});
 if(video.tagName!=='VIDEO' || !video.controls || video.autoplay || video.getAttribute('preload')!=='metadata')throw Error('Safe MP4 preview missing');
})
await page.evaluate(async()=>{const {createDropZone}=await import('/autoposter/js/upload.js');document.querySelector('#root').replaceChildren(createDropZone().root);document.querySelector('#modals').replaceChildren()})
await page.locator('input[type=file]').setInputFiles({name:'synthetic.mp4',mimeType:'video/mp4',buffer:Buffer.from('synthetic selection only')})
await page.locator('.uploadrow-name').filter({hasText:'synthetic.mp4'}).waitFor()
const result={widths:[320,390,1280],screenshots:12,blank_secret_preserved:true,errors,unexpected,writes:writes.map(w=>({path:w.path,method:w.method}))};fs.writeFileSync(out+'/results.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));await browser.close()
})().catch(e=>{console.error(e);process.exit(1)})
