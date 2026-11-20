const {chromium}=require('playwright');
const fs=require('fs'),assert=require('assert/strict'),path=require('path');
const root=process.env.FORM_SCOPE_SOURCE||path.resolve(__dirname,'..');
const script=fs.readFileSync(path.join(root,'app/static/character-workspace.js'),'utf8');
const queue=fs.readFileSync(path.join(root,'app/templates/queue.html'),'utf8');
const overrides=[...queue.matchAll(/<button[^>]*formaction="\/queue\/\{\{ d\['id'\] \}\}\/(pause|save|regenerate|reject)"[^>]*>[\s\S]*?<\/button>/g)].map(m=>({action:m[1],html:m[0].replaceAll("{{ d['id'] }}",'42')}));
assert.deepEqual(new Set(overrides.map(x=>x.action)),new Set(['pause','save','regenerate','reject']));
const owners=Array.from({length:6},(_,i)=>'00000000-0000-0000-0000-'+String(i+1).padStart(12,'0'));
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});let cases=0;
try{for(const owner of [...owners,null])for(const action of ['reject','save','regenerate','pause','approve']){
 const page=await browser.newPage();let submitted=null;
 const form='<form method="post" action="/queue/42/approve"><textarea name="edited_text">Synthetic draft</textarea>'+overrides.map(x=>x.html).join('')+'<button id="approve">Approve</button></form>';
 await page.route('**/*',async route=>{const r=route.request(),u=new URL(r.url());assert.equal(u.origin,'http://creator.test');
  if(u.pathname==='/api/v1/characters/mode')return route.fulfill({json:{enabled:false}});
  if(u.pathname==='/workspace.js')return route.fulfill({contentType:'application/javascript',body:script});
  if(r.method()==='POST'){
   submitted={url:u.href,body:r.postData(),action:u.pathname};
   if(owner&&u.searchParams.get('character_id')!==owner)return route.fulfill({status:409,json:{detail:'Character URL context required; open /characters'}});
   return route.fulfill({status:200,contentType:'text/html',body:'<p>Native synthetic form request accepted; no provider call.</p>'});
  }
  return route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><script src="/workspace.js"></script>'+form});
 });
 await page.goto('http://creator.test/queue'+(owner?'?character_id='+owner:''));
 await Promise.all([page.waitForNavigation(),page.locator(action==='approve'?'#approve':`button[formaction*="/${action}"]`).click()]);
 assert(submitted);assert.equal(new URL(submitted.url).pathname,'/queue/42/'+action);
 assert.equal(new URL(submitted.url).searchParams.get('character_id'),owner,`Native ${action} POST lost owner ${owner}`);
 assert.equal(new URL(page.url()).pathname,'/queue/42/'+action);assert.equal(new URL(page.url()).searchParams.get('character_id'),owner);
 cases++;await page.close();
}
console.log(JSON.stringify({passed:true,native_form_scenarios:cases,owners:owners.length,legacy:true,actual_queue_override_buttons:true,providers_called:0}));
}finally{await browser.close()}})().catch(e=>{console.error(e.stack);process.exitCode=1});
