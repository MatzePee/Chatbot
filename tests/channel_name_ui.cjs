// Offline channel-settings regression: no network, no workers or real posts.
const fs=require('node:fs'); const vm=require('node:vm'); const assert=require('node:assert/strict');
const source=fs.readFileSync('autoposter/ui/js/pages/channels.js','utf8')
 .replace(/^import .*$/gm,'').replace('export default async function renderChannels()', 'async function renderChannels()');
function node(tag, props={}, ...children) {
 const n={tag, props, children:children.flat().filter(x=>x!=null), value:props.value??'', checked:props.checked??false,
  style:props.style||{}, textContent:'', disabled:false,
  appendChild(x){this.children.push(x)}, addEventListener(){}, dispatchEvent(){}, focus(){}, select(){}};
 return n;
}
function walk(n,fn){ if(n==null)return null; if(fn(n))return n; for(const c of n.children||[]){const found=walk(c,fn);if(found)return found} return null; }
async function check(platform){
 const channel={id:'synthetic-'+platform,platform,display_name:'Alter Anzeigename',handle:'@unverändert',color:'#6366f1',
  health:'ok',is_active:true,is_connected:true,quota_used_today:0,persona_id:null,fanvue_audience:platform==='fanvue'?'subscribers':'',
  policy:{posts_per_day:2,daily_api_quota:100,allowed_time_windows:[{dow:[0,1],from:'09:00',to:'22:00'}],image_time_windows:[],text_time_windows:[]}};
 let form, payload, id;
 const api={channels:async()=>[channel],personas:async()=>[],updateChannel:async(i,d)=>{id=i;payload=d}};
 const ctx={api,h:node,clear:n=>n.children=[],empty:t=>node('div',{},t),field:(label,input)=>node('field',{label},input),
  modal:(_title,render)=>{form=render(()=>{})},toast:{ok(){},error(){throw Error('unexpected validation error')},info(){}},
  guard:fn=>fn,fmtDateTime:x=>x,watermarkEditor:()=>({node:node('div'),values:()=>({})}),
  location:{origin:'https://example.invalid',href:''},window:{CreatorStudioLive:{busy:()=>false}},URL,JSON,console};
 vm.runInNewContext(source+'\nthis.renderChannels=renderChannels',ctx,{filename:'channels.js'});
 const page=await ctx.renderChannels();
 const settings=walk(page,n=>n.tag==='button' && n.children.includes('Einstellungen'));
 assert(settings,`${platform}: settings action missing`);
 settings.props.onClick();
 const label=walk(form,n=>n.tag==='field' && n.props.label==='Anzeigename');
 assert(label,`${platform}: Anzeigename input missing from settings`);
 const input=label.children[0];
 assert.equal(input.value,'Alter Anzeigename');
 input.value='  Neuer Anzeigename  ';
 const save=walk(form,n=>n.tag==='button' && n.children.includes('Speichern'));
 assert(save,`${platform}: save action missing`);
 await save.props.onClick();
 assert.equal(id,channel.id);
 assert.equal(payload.display_name,'Neuer Anzeigename');
 assert(!('handle' in payload),`${platform}: rename must not change handle`);
 assert(!('is_active' in payload),`${platform}: rename must not change active state`);
 console.log('channel name update',platform,'ok');
}
(async()=>{for(const p of ['fanvue','x','telegram'])await check(p)})().catch(e=>{console.error(e);process.exitCode=1});
