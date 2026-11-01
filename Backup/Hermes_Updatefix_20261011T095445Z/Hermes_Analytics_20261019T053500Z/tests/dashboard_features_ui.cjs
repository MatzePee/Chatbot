// Offline render/interaction test. No HTTP, posts, jobs or production DOM.
const fs = require('fs'); const assert = require('assert');
class Node {
  constructor(tag, attrs={}, children=[]) { this.tag=tag; this.attrs=attrs; this.children=children.flat().filter(x=>x!=null); this.classList={add(){}}; this.listeners={}; }
  appendChild(n) { this.children.push(n); return n; }
  addEventListener(k,f) { this.listeners[k]=f; }
  querySelector(tag) { return this.children.find(n=>n instanceof Node && n.tag===tag) || this.children.filter(n=>n instanceof Node).map(n=>n.querySelector(tag)).find(Boolean); }
}
const h=(tag,attrs,...children)=>new Node(tag,attrs,children);
const clear=n=>{n.children=[];return n};
const card=(title,...children)=>h('section',{},h('h2',{},title),...children);
const empty=t=>h('p',{},t); const guard=f=>f;
const data={inventory:{channels:[],unassigned_assets:0}, lifecycle_counts:{},upcoming_posts:[],channel_health:[],notifications:[{id:'n',title:'Warnung',body:'Fehler',created_at:'2026-10-11T10:00:00',level:'warning'}],recent_failures:[{id:'p',error_message:'Fehlversuch',last_attempt_at:'2026-10-10T10:00:00Z'}]};
const acknowledged=[];
const api={dashboard:async()=>data, xFollowers:async()=>[{name:'Account',current:100,yesterday:{count:90,growth:10},days7:{count:null,growth:null},days30:{count:120,growth:-20},sampled_at:'2026-10-11T10:00:00Z',stale:false,status:'OK'}],jobs:async()=>({jobs:[]}),markRead:async ids=>{acknowledged.push(ids);data.notifications=[]},ackFailure:async id=>{acknowledged.push(id);data.recent_failures=[]}};
let source=fs.readFileSync('autoposter/ui/js/pages/dashboard.js','utf8').replace(/^import [^\n]*\n/gm,'').replace('export function','function').replace('export default async function','async function');
const build=new Function('api','h','card','empty','guard','clear','renderUpcoming','LIFECYCLE_ORDER','toast','fmtDate','fmtDateTime','spinner','append','confirmDialog',source+';return {renderDashboard,problemTime}');
const mod=build(api,h,card,empty,guard,clear,()=>{},[],{},()=>'',()=>'',()=>h('i',{}),()=>{},()=>{});
const flat=n=>n instanceof Node ? n.children.flatMap(flat):[String(n)];
const nodes=n=>n instanceof Node ? [n,...n.children.flatMap(nodes)]:[];
(async()=>{
 assert.equal(mod.problemTime(null),'Zeitpunkt unbekannt'); assert.equal(mod.problemTime('bad'),'Zeitpunkt unbekannt');
 assert(mod.problemTime('2026-10-11T10:00:00').includes('12:00:00'));
 const page=await mod.renderDashboard(); const output=flat(page).join(' ');
 for(const expected of ['Europe/Berlin','Keine Historie','90 (+10)','120 (-20)','Warnung']) assert(output.includes(expected),expected);
 const buttons=nodes(page).filter(n=>n.tag==='button' && flat(n).join('')==='Zur Kenntnis genommen'); assert.equal(buttons.length,2);
 await buttons[0].attrs.onClick(); await buttons[1].attrs.onClick();
 assert.deepEqual(acknowledged,[['n'],'p']);
 console.log('UI: render, Berlin timestamps, missing date/history, signed growth, both acknowledgments PASS');
})().catch(e=>{console.error(e);process.exit(1)});
