// Actual update-card markup and module; every request fulfilled offline.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const ROOT=path.resolve(__dirname,'..');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const requests=[],unexpected=[];let state={phase:'legacy',helper:{ready:true}},rejectPost=false,expectedSource='';
 const page=await browser.newPage();
 await page.route('**/*',async route=>{
  const r=route.request(),u=new URL(r.url());
  if(u.hostname!=='upgrade.test'){unexpected.push(r.url());return route.abort();}
  requests.push([r.method(),u.pathname]);
  if(u.pathname==='/api/v1/system/character-upgrade'){
   if(r.method()==='POST'&&rejectPost)return route.fulfill({status:409,json:{detail:'Administrator erforderlich: Helfer wurde inzwischen geändert.'}});
   if(r.method()==='POST'){assert.deepEqual(r.postDataJSON(),{name:'Cherry',confirmed:true,...(expectedSource?{technical_source:expectedSource}:{})});state={phase:'pending_restart',name:'Cherry',helper:{ready:true}};}
   if(r.method()==='DELETE')state={phase:'legacy',helper:{ready:true}};
   return route.fulfill({json:state});
  }
  if(u.pathname==='/static/character-upgrade.js')return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(ROOT,'app/static/character-upgrade.js'),'utf8')});
  if(u.pathname==='/'){
   const template=fs.readFileSync(path.join(ROOT,'app/templates/_character_upgrade.html'),'utf8');
   return route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+template.replace(/\?v={{ css_v }}/g,'')});
  }
  unexpected.push(u.pathname);return route.abort();
 });
 for(const reason of ['old','missing','environment']){
  state={phase:'legacy',helper:{ready:false,message:'Administrator erforderlich: '+reason}};
  await page.goto('http://upgrade.test/');
  await page.locator('[role=status]').filter({hasText:'Administrator erforderlich:'}).waitFor({timeout:2000});
  await page.getByLabel('Name des bestehenden Charakters').fill('Cherry');
  await page.getByRole('checkbox').check();
  assert.equal(await page.getByRole('button',{name:'Umstellung vormerken'}).isDisabled(),true);
 }
 state={phase:'legacy',helper:{ready:true}};
 for(const width of [320,390,1280]){
  await page.setViewportSize({width,height:844});await page.goto('http://upgrade.test/');
  await page.getByText('Einzelcharakter bleibt unverändert').waitFor();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 }
 await page.getByLabel('Name des bestehenden Charakters').fill('Cherry');
 assert.equal(await page.getByRole('button',{name:'Umstellung vormerken'}).isDisabled(),true);
 await page.getByRole('checkbox').check();await page.getByRole('button',{name:'Umstellung vormerken'}).click();
 await page.getByText('Vorgemerkt: Cherry').waitFor();
 assert.equal(requests.some(([m,p])=>p.includes('restart')||p.includes('update-check')),false);
 await page.getByRole('button',{name:'Vormerkung abbrechen'}).click();
 await page.getByText('Einzelcharakter bleibt unverändert').waitFor();
 for(const source of ['autochat','autopost']){
  expectedSource=source;
  await page.locator('select[name=technical_source]').selectOption(source,{timeout:2000});
  await page.getByRole('button',{name:'Umstellung vormerken'}).click();
  await page.getByText('Vorgemerkt: Cherry').waitFor();
  await page.getByRole('button',{name:'Vormerkung abbrechen'}).click();
  await page.getByText('Einzelcharakter bleibt unverändert').waitFor();
 }
 rejectPost=true;
 await page.getByRole('button',{name:'Umstellung vormerken'}).click();
 await page.getByText('Administrator erforderlich: Helfer wurde inzwischen geändert.').waitFor({timeout:2000});
 assert.equal(await page.getByRole('button',{name:'Umstellung vormerken'}).isDisabled(),true);
 assert.deepEqual(unexpected,[]);
 console.log(JSON.stringify({ok:true,widths:[320,390,1280],real_template:true,requests}));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
