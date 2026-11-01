import { h } from './ui.js?v=creatorstudio-mobile-system-20260909'

// One real last-success sample per Berlin calendar day; null explicitly breaks paths.
export function historyChart(account) {
  const points = account.history || []
  const root = h('figure', { class: 'account-history', style: { margin: '12px 0', minWidth: '0', maxWidth: '100%', overflowWrap: 'anywhere' } },
    h('figcaption', {}, `30-Tage-Verlauf · ${account.name}`),
    h('div', { class: 'hint' }, 'Europe/Berlin · letzter erfolgreicher Tageswert · heute unvollständig. Lücken = keine Messung.'))
  const ns = 'http://www.w3.org/2000/svg'
  const node = (tag, attrs, text) => { const n = document.createElementNS(ns, tag); for (const [k,v] of Object.entries(attrs)) n.setAttribute(k,v); if (text !== undefined) n.textContent = text; return n }
  const valid = p => typeof p.count === 'number' && Number.isFinite(p.count) && p.count >= 0
  const values = points.filter(valid).map(p => p.count)
  if (values.length) {
    const min = Math.min(...values), max = Math.max(...values), span = max - min || 1
    const svg = node('svg', { viewBox: '0 0 600 160', role: 'img', 'aria-label': `30-Tage-Verlauf ${account.name}; Messwerte in der Datentabelle`, width: '100%', style: 'display:block;max-width:100%;height:auto;color:var(--accent,#63b3ed)' })
    svg.append(node('title', {}, `30 Tage · ${account.name}`))
    let segment = []
    const flush = () => { if (segment.length > 1) svg.append(node('polyline', { points: segment.join(' '), fill: 'none', stroke: 'currentColor', 'stroke-width': '2' })); segment = [] }
    points.forEach((p,i) => {
      if (!valid(p)) { flush(); return }
      const x = 10 + i * 580 / Math.max(1, points.length-1), y = max === min ? 80 : 135 - (p.count-min)*110/span
      segment.push(`${x},${y}`)
      const dot = node('circle', { cx:x, cy:y, r:3, fill:'currentColor' })
      dot.append(node('title', {}, `${p.date}: ${p.count}`)); svg.append(dot)
    }); flush()
    root.append(svg, h('div', { class:'hint' }, `${points[0]?.date || ''} – ${points.at(-1)?.date || ''} · Min ${min} / Max ${max}`))
  } else root.append(h('p', {class:'hint'}, 'Noch keine Historie – keine Messung verfügbar.'))
  const table = h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Datum (Europe/Berlin)'), h('th', {}, 'Anzahl'))),
    h('tbody', {}, ...points.map(p => h('tr', {}, h('td', {}, p.date), h('td', {}, valid(p) ? p.count : 'Keine Messung')))))
  root.append(h('details', {}, h('summary', {}, 'Tageswerte / barrierefreie Datentabelle'), table))
  return root
}
