import { h, card, guard, toast } from './ui.js?v=persona-hashtags-20261121-1'
import { platformLabel } from './platform-icon.js?v=persona-hashtags-20261121-1'
import { historyChart } from './account-history.js?v=persona-hashtags-20261121-1'
import { api } from './api.js?v=social-summary-20261122-1'

const number = value => value == null ? 'Keine Messung' : new Intl.NumberFormat('de-DE').format(value)
const growth = value => value?.growth == null ? 'Noch keine Vergleichsdaten' : `${value.growth >= 0 ? '+' : ''}${number(value.growth)}${value.percent == null ? '' : ' (' + (value.percent >= 0 ? '+' : '') + new Intl.NumberFormat('de-DE',{maximumFractionDigits:1}).format(value.percent) + ' %)'} `

export function socialSummaryCard(data) {
  const total=data.total
  const metrics=h('div',{style:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,140px),1fr))',gap:'12px'}},
    ...[['Follower gesamt',number(total.current)],['Wachstum seit gestern',growth(total.yesterday)],['Wachstum in 7 Tagen',growth(total.days7)],['Wachstum in 30 Tagen',growth(total.days30)]].map(([label,value])=>
      h('div',{class:'card pad',style:{minWidth:0,overflowWrap:'anywhere'}},h('div',{class:'hint'},label),h('div',{style:{fontSize:value==='Noch keine Vergleichsdaten'?'14px':'24px',fontWeight:700,marginTop:'8px'}},value))))
  const platforms=h('div',{style:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,220px),1fr))',gap:'12px',marginTop:'12px'}},
    ...data.platforms.map(p=>h('div',{class:'card pad',style:{minWidth:0}},
      h('b',{},platformLabel(p.platform,{fanvue:'Fanvue',x:'X',instagram:'Instagram'}[p.platform])),
      h('div',{style:{fontSize:'26px',fontWeight:700,margin:'8px 0'}},number(p.current)),
      h('div',{class:'hint'},`Gestern: ${growth(p.yesterday)}`),
      h('div',{class:'hint'},`7 Tage: ${growth(p.days7)}`),
      h('div',{class:'hint'},`30 Tage: ${growth(p.days30)}`),
      !p.accounts ? h('div',{class:'hint'},'Kein Konto eingerichtet') : !p.complete ? h('div',{style:{color:'#b42332'}},`${p.measured}/${p.accounts} Konten gemessen – unvollständig`) : p.stale ? h('div',{style:{color:'#80520b'}},'Letzte Messung ist veraltet') : null)))
  const refresh=h('button',{type:'button',onClick:guard(async()=>{
    refresh.disabled=true;refresh.textContent='Wird aktualisiert …'
    try{const result=await api.refreshSocialSummary();toast.info(`${result.updated} Kontostatistiken aktualisiert`);location.reload()}
    finally{refresh.disabled=false;refresh.textContent='Social-Media-Statistik aktualisieren'}
  })},'Social-Media-Statistik aktualisieren')
  return h('section',{class:'social-summary',style:{minWidth:0}},card('Social Media gesamt · Fanvue, X und Instagram',
    h('p',{class:'hint'},'Summe der Follower deiner Konten dieses Charakters. Personen können auf mehreren Plattformen enthalten sein. Fanvue-Postkanäle desselben Kontos werden nur einmal gezählt; Abonnenten werden nicht zusätzlich auf die Followerzahl aufgeschlagen.'),
    !total.complete ? h('p',{style:{color:'#b42332'},role:'status'},`Teilstand: ${total.measured}/${total.accounts} Konten mit Messung. Fehlende Werte werden nicht als 0 gezählt.`) : total.stale ? h('p',{style:{color:'#80520b'},role:'status'},'Mindestens eine Messung ist veraltet. Wachstum wird erst mit vollständigen aktuellen Messungen angezeigt.') : null,
    metrics,platforms,historyChart(total),
    h('p',{class:'hint'},'Vergleich derselben Konten mit dem exakten Kalendertag (Europe/Berlin). Fehlende historische Messungen werden nicht geschätzt.'),refresh))
}
