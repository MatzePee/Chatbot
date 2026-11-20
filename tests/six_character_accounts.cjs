const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const root=path.resolve(__dirname,'..'),out=path.resolve(root,'../evidence/six-character-acceptance'),render=path.join(out,'render');
const owners=JSON.parse(fs.readFileSync(path.join(render,'owners.json'))),writes=[],scenarios=[],errors=[];
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});try{
const context=await browser.newContext();
await context.route('**/*',async route=>{
 const req=route.request(),u=new URL(req.url()),p=u.pathname,owner=u.searchParams.get('character_id');
 assert.equal(u.hostname,'offline.test');const json=x=>route.fulfill({json:x});
 if(p==='/api/v1/characters/mode')return json({enabled:true});
 if(p==='/api/v1/characters')return json(owners);
 if(p==='/api/v1/channels')return json(JSON.parse(fs.readFileSync(path.join(render,owner+'-channels.json'))));
 if(p.startsWith('/api/v1/channels/')){writes.push({path:p,owner,method:req.method(),payload:req.postDataJSON()});return json({ok:true});}
 const fixtures={'/api/v1/settings/integrations':{x_api_tier:'basic'},'/api/v1/auth/me':{role:'admin'},'/api/v1/auth/mode':{auth_enabled:false},'/api/v1/personas':[], '/api/v1/settings':{dry_run:true,global_pause:true},'/api/deployment-status':{},'/api/creatorpilot/health':{ok:true},'/api/v1/system/character-upgrade':{phase:'legacy'}};
 if(Object.hasOwn(fixtures,p))return json(fixtures[p]);
 if(p==='/settings/shared')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(render,owner+'-channels.html'))});
 let f;if(p.startsWith('/static/'))f=path.join(root,'app',p);else if(p.startsWith('/autoposter/js/'))f=path.join(root,'autoposter/ui',p.slice('/autoposter/'.length));
 if(f&&fs.existsSync(f))return route.fulfill({contentType:f.endsWith('.js')?'application/javascript':'text/css',body:fs.readFileSync(f)});
 throw Error('Unexpected '+p);
});
const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
for(const width of [1280,320,390])for(const owner of owners){
 await page.setViewportSize({width,height:900});await page.goto('http://offline.test/settings/shared?section=channels&character_id='+owner.id);
 await page.getByRole('button',{name:'API-Zugang',exact:true}).click();
 let dialog=page.locator('#modals .modal');await dialog.waitFor();
 assert.equal(await dialog.locator('input[type=password]').inputValue(),'');
 const clear=dialog.getByRole('checkbox',{name:'Gespeichertes Client-Secret entfernen',exact:true});
 await clear.check();await dialog.getByRole('button',{name:'Abbrechen',exact:true}).click();
 assert.equal(writes.length,scenarios.length*2,'Cancel does not send clear');
 await page.getByRole('button',{name:'API-Zugang',exact:true}).click();dialog=page.locator('#modals .modal');
 await dialog.getByRole('checkbox',{name:'Gespeichertes Client-Secret entfernen',exact:true}).check();
 await dialog.getByRole('button',{name:'Speichern',exact:true}).click();await dialog.waitFor({state:'detached'});
 assert.equal(writes.at(-1).owner,owner.id);assert.equal(writes.at(-1).payload.oauth_clear_client_secret,true);
 await page.getByRole('button',{name:'Bot-Zugang',exact:true}).click();dialog=page.locator('#modals .modal');
 assert.equal(await dialog.getByRole('textbox',{name:'Zielkanal',exact:true}).inputValue(),'@synthetic_channel');
 assert.equal(await dialog.locator('input[type=password]').inputValue(),'');
 await dialog.getByRole('button',{name:'Speichern',exact:true}).click();await dialog.waitFor({state:'detached'});
 assert.equal(writes.at(-1).owner,owner.id);assert.equal(writes.at(-1).payload.bot_token,'');
 await page.getByRole('button',{name:'API-Zugang',exact:true}).click();dialog=page.locator('#modals .modal');
 const geometry=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,dialog:document.querySelector('#modals .modal').getBoundingClientRect().toJSON()}));
 assert(geometry.scroll<=width,JSON.stringify(geometry));assert(geometry.dialog.width<=width);
 await page.screenshot({path:path.join(out,`accounts-${width}-${owner.name}.png`),fullPage:true});
 scenarios.push({owner:owner.name,width,geometry,xClear:true,telegramBlankSave:true,cancel:true});
}
assert.equal(errors.length,0,errors.join('\n'));fs.writeFileSync(path.join(out,'accounts-browser.json'),JSON.stringify({scenarios,writes,errors,visual_review:false},null,2));console.log(JSON.stringify({passed:true,scenarios:scenarios.length}));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
