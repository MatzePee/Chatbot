// Editor für den Tagesrhythmus einer Persona: Regeltext, Prüfung, Wochenraster.
import { api } from './api.js?v=creatorstudio-mobile-system-20260909'
import { h, append, clear, empty, toast, guard, spinner, debounce } from './ui.js?v=creatorstudio-mobile-system-20260909'

const DAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']

// Feste, gut unterscheidbare Farbtöne. Jede Aktivität bekommt beim ersten
// Auftreten einen – so bleibt die Zuordnung im Raster über Neuprüfungen stabil.
const HUES = [205, 145, 35, 275, 5, 175, 95, 320, 55, 240, 15, 120]
const colorFor = (activity, order) => {
  const index = order.indexOf(activity)
  return `hsl(${HUES[index % HUES.length]} 55% ${index >= HUES.length ? 32 : 42}%)`
}

/**
 * Baut den kompletten Block. `getText`/`setText` binden ihn an das Textfeld
 * der Persona-Seite, damit Speichern dort unverändert funktioniert.
 */
export function rhythmEditor({ value = '', onChange = () => {} } = {}) {
  const area = h('textarea', {
    style: {
      height: '340px', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
      fontSize: '12px', lineHeight: '1.55', whiteSpace: 'pre', overflowWrap: 'normal',
    },
    spellcheck: false,
    placeholder: 'taeglich 00:30-08:30  schlaefst tief und fest',
  })
  area.value = value || ''

  const status = h('div', { class: 'col', style: { gap: '6px' } })
  const gridBox = h('div', {})
  const legendBox = h('div', { class: 'row', style: { gap: '10px', flexWrap: 'wrap', fontSize: '11px' } })

  const drawGrid = (result) => {
    clear(gridBox); clear(legendBox)
    const order = []
    for (const row of result.grid) {
      for (const cell of row) if (cell && !order.includes(cell)) order.push(cell)
    }
    if (!order.length) {
      gridBox.appendChild(empty('Noch keine gültige Regel.'))
      return
    }

    const table = h('div', { class: 'rhythmgrid' })
    table.appendChild(h('div', { class: 'rhythmcorner' }))
    for (let hour = 0; hour < 24; hour++) {
      table.appendChild(h('div', { class: 'rhythmhour' }, hour % 3 === 0 ? String(hour) : ''))
    }
    result.grid.forEach((row, day) => {
      table.appendChild(h('div', { class: 'rhythmday' }, DAYS[day]))
      row.forEach((cell, hour) => {
        table.appendChild(h('div', {
          class: 'rhythmcell' + (cell ? '' : ' empty'),
          style: cell ? { background: colorFor(cell, order) } : {},
          title: `${DAYS[day]} ${String(hour).padStart(2, '0')}:00 — ${cell || 'nicht abgedeckt'}`,
        }))
      })
    })
    gridBox.appendChild(table)

    for (const activity of order) {
      legendBox.appendChild(h('span', { class: 'row', style: { gap: '5px' } },
        h('i', {
          style: {
            width: '11px', height: '11px', borderRadius: '3px',
            background: colorFor(activity, order), display: 'inline-block',
          },
        }),
        h('span', { class: 'hint' }, activity)))
    }
  }

  const drawStatus = (result) => {
    clear(status)
    for (const err of result.errors) {
      status.appendChild(h('div', { class: 'errbox' },
        h('b', {}, `Zeile ${err.line}: `), err.message,
        h('div', { class: 'hint', style: { fontFamily: 'ui-monospace, monospace' } }, err.text)))
    }
    if (result.gaps.length) {
      const byDay = {}
      for (const gap of result.gaps) {
        (byDay[gap.day] = byDay[gap.day] || []).push(`${gap.from}–${gap.to}`)
      }
      status.appendChild(h('div', { class: 'warnbox' },
        h('b', {}, 'Nicht abgedeckt: '),
        Object.entries(byDay).map(([day, times]) => `${day} ${times.join(', ')}`).join(' · '),
        h('div', { class: 'hint' },
          'Für diese Zeiten bekommt das Modell die Anweisung, vage zu bleiben.')))
    }
    if (!result.errors.length && !result.gaps.length && result.rules.length) {
      status.appendChild(h('div', { class: 'okbox' },
        `${result.rules.length} Regeln verstanden, die Woche ist lückenlos abgedeckt.`))
    }
  }

  let lastText = null
  const check = async ({ silent = true } = {}) => {
    const text = area.value
    if (silent && text === lastText) return
    lastText = text
    try {
      const result = await api.checkRhythm(text)
      drawStatus(result)
      drawGrid(result)
      onChange(text)
      return result
    } catch (err) {
      clear(status).appendChild(h('div', { class: 'errbox' }, err.message))
    }
  }

  area.addEventListener('input', debounce(() => check(), 500))

  const exampleBtn = h('button', { class: 'small' }, 'Beispielplan einsetzen')
  exampleBtn.addEventListener('click', guard(async () => {
    if (area.value.trim() && !confirm('Der vorhandene Plan wird ersetzt. Fortfahren?')) return
    clear(exampleBtn).appendChild(spinner())
    try {
      const { text } = await api.rhythmTemplate()
      area.value = text
      await check({ silent: false })
      toast.ok('Beispielplan eingesetzt – bitte anpassen und speichern')
    } finally {
      clear(exampleBtn).appendChild(document.createTextNode('Beispielplan einsetzen'))
    }
  }))

  const node = h('div', { class: 'grid c2' },
    h('div', {},
      area,
      h('div', { class: 'row', style: { marginTop: '8px' } },
        exampleBtn,
        h('button', { class: 'small', onClick: guard(() => check({ silent: false })) }, 'Prüfen'),
      ),
      h('div', { class: 'hint', style: { marginTop: '8px' } },
        'Eine Regel pro Zeile: Tage, Zeitfenster, Aktivität. Zeilen mit # sind Kommentare. ',
        'Fenster dürfen über Mitternacht laufen und gehören dann zum Starttag. ',
        'Treffen mehrere Regeln zu, gewinnt die mit den wenigsten Tagen, dann die mit dem ',
        'kürzesten Fenster.'),
    ),
    h('div', { class: 'stack' }, status, gridBox, legendBox),
  )

  check({ silent: false })
  return { node, get value() { return area.value }, refresh: () => check({ silent: false }) }
}
