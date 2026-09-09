// Matrix: Bilder × Kanäle. Beantwortet auf einen Blick "was fehlt wo noch".
import { api } from '../api.js?v=creatorstudio-layout-20260909'
import { h, clear, empty, toast, guard, attachPreview, fmtDate, spinner } from '../ui.js?v=creatorstudio-layout-20260909'

const CELL = {
  none: {
    label: 'nicht zugeordnet', short: '+', cls: 'none',
    title: 'Klicken, um dieses Bild dem Kanal zuzuordnen',
  },
  assigned: {
    label: 'zugeordnet', short: '✓', cls: 'assigned',
    title: 'Zugeordnet – klicken, um die Zuordnung zu entfernen',
  },
  scheduled: {
    label: 'eingeplant', short: '⏱', cls: 'scheduled',
    title: 'Für einen geplanten Post vorgesehen – nicht entfernbar',
  },
  published: {
    label: 'veröffentlicht', short: '✔', cls: 'published',
    title: 'Auf diesem Kanal bereits veröffentlicht',
  },
}

export default async function renderMatrix() {
  const state = { rows: [], channels: [], cursor: null, onlyOpen: false, busy: new Set() }

  const box = h('div', { class: 'matrix-wrap card' })
  const moreBtn = h('button', { style: { marginTop: '12px', display: 'none' } }, 'Mehr laden')
  const counts = h('div', { class: 'row', style: { gap: '14px' } })

  // ------------------------------------------------------------ Laden
  async function load(cursor) {
    const data = await api.matrix(150, cursor)
    state.channels = data.channels
    state.rows = cursor ? state.rows.concat(data.rows) : data.rows
    state.cursor = data.next_cursor
    draw()
  }

  /** Eine einzelne Zelle neu zeichnen, ohne die ganze Tabelle aufzubauen. */
  async function toggle(row, channel, cell) {
    const key = row.asset.id + ':' + channel.id
    if (state.busy.has(key)) return
    const current = row.cells[channel.id] || 'none'
    if (current === 'published' || current === 'scheduled') return

    state.busy.add(key)
    cell.classList.add('busy')
    try {
      if (current === 'assigned') {
        await api.unassign({ asset_ids: [row.asset.id], channel_ids: [channel.id] })
        row.cells[channel.id] = 'none'
      } else {
        const result = await api.assign({ asset_ids: [row.asset.id], channel_ids: [channel.id] })
        if (result.rejected && result.rejected.length) {
          toast.error(result.rejected[0].reason)
          return
        }
        row.cells[channel.id] = 'assigned'
      }
      paintCell(row, channel, cell)
      drawCounts()
    } catch (err) {
      toast.error(err.message)
    } finally {
      state.busy.delete(key)
      cell.classList.remove('busy')
    }
  }

  function paintCell(row, channel, cell) {
    const stateKey = row.cells[channel.id] || 'none'
    const meta = CELL[stateKey]
    clear(cell)
    cell.className = 'mcell ' + meta.cls
    cell.title = meta.title
    cell.disabled = stateKey === 'published' || stateKey === 'scheduled'
    cell.appendChild(h('span', { class: 'mcell-icon' }, meta.short))
    cell.appendChild(h('span', { class: 'mcell-label' }, meta.label))
  }

  // ------------------------------------------------------------ Zeichnen
  function drawCounts() {
    clear(counts)
    for (const channel of state.channels) {
      const assigned = state.rows.filter((r) => (r.cells[channel.id] || 'none') !== 'none').length
      counts.appendChild(h('span', { class: 'row', style: { gap: '5px', fontSize: '12px' } },
        h('i', { class: 'dot', style: { background: channel.color } }),
        h('b', {}, channel.name),
        h('span', { class: 'hint' }, `${assigned} von ${state.rows.length}`),
      ))
    }
  }

  function draw() {
    clear(box)
    const rows = state.onlyOpen
      ? state.rows.filter((r) => state.channels.some((c) => (r.cells[c.id] || 'none') === 'none'))
      : state.rows

    if (!rows.length) {
      box.appendChild(empty(state.onlyOpen ? 'Alle Bilder sind überall zugeordnet.' : 'Keine Bilder.'))
      drawCounts()
      return
    }

    // -------- Kopfzeile: Kanalname, darunter passt die Schaltfläche --------
    const head = h('tr', {},
      h('th', { class: 'mhead-img' }, 'Bild'),
      ...state.channels.map((channel) => h('th', { class: 'mhead-ch' },
        h('div', { class: 'row', style: { gap: '6px', justifyContent: 'center' } },
          h('i', { class: 'dot', style: { background: channel.color } }),
          h('b', {}, channel.name),
        ),
        h('div', { class: 'hint', style: { textAlign: 'center', fontSize: '10px' } },
          channel.platform.toUpperCase()),
      )),
    )

    const body = rows.map((row) => {
      const thumb = h('img', {
        src: row.asset.thumb_url, alt: '', class: 'mthumb', loading: 'lazy',
      })
      attachPreview(thumb, row.asset.url, { size: 380 })

      return h('tr', {},
        h('td', { class: 'mcol-img' },
          h('div', { class: 'row', style: { gap: '10px', flexWrap: 'nowrap' } },
            thumb,
            h('div', { style: { minWidth: '0' } },
              h('div', { class: 'mname' }, row.asset.filename),
              h('div', { class: 'hint', style: { fontSize: '11px' } },
                `${row.asset.width}×${row.asset.height}` +
                (row.asset.usage_count ? ` · ${row.asset.usage_count}× genutzt` : '') +
                (row.asset.last_used_at ? ` · zuletzt ${fmtDate(row.asset.last_used_at)}` : '')),
            ),
          ),
        ),
        ...state.channels.map((channel) => {
          const cell = h('button', {})
          paintCell(row, channel, cell)
          cell.addEventListener('click', () => toggle(row, channel, cell))
          return h('td', { class: 'mcol-cell' }, cell)
        }),
      )
    })

    box.appendChild(h('table', { class: 'matrix' },
      h('thead', {}, head),
      h('tbody', {}, ...body),
    ))
    moreBtn.style.display = state.cursor ? '' : 'none'
    drawCounts()
  }

  // ------------------------------------------------------------ Kopfleiste
  const onlyOpenBtn = h('button', {}, 'Nur mit offenen Kanälen')
  onlyOpenBtn.addEventListener('click', () => {
    state.onlyOpen = !state.onlyOpen
    onlyOpenBtn.className = state.onlyOpen ? 'primary' : ''
    draw()
  })

  const assignAllBtn = h('select', { style: { width: '230px' } },
    h('option', { value: '' }, 'Alle sichtbaren zuordnen zu …'))
  const fillAssignSelect = () => {
    clear(assignAllBtn)
    assignAllBtn.appendChild(h('option', { value: '' }, 'Alle sichtbaren zuordnen zu …'))
    for (const c of state.channels) assignAllBtn.appendChild(h('option', { value: c.id }, c.name))
  }
  assignAllBtn.addEventListener('change', guard(async () => {
    const channelId = assignAllBtn.value
    if (!channelId) return
    const ids = state.rows
      .filter((r) => (r.cells[channelId] || 'none') === 'none')
      .map((r) => r.asset.id)
    assignAllBtn.value = ''
    if (!ids.length) { toast.info('Nichts offen'); return }
    const result = await api.assign({ asset_ids: ids, channel_ids: [channelId] })
    toast.ok(`${result.created} zugeordnet`)
    if (result.rejected && result.rejected.length) toast.error(result.rejected[0].reason)
    await load()
  }))

  moreBtn.addEventListener('click', guard(() => load(state.cursor)))

  await load()
  fillAssignSelect()

  const livePage = h('div', { class: 'page stack' },
    h('div', {},
      h('h1', {}, 'Matrix'),
      h('p', { class: 'hint' },
        'Bilder × Kanäle. Ein Klick auf eine Schaltfläche ordnet zu oder entfernt die Zuordnung. ' +
        'Zum Vergrößern mit der Maus über ein Bild fahren.'),
    ),
    h('div', { class: 'row' }, onlyOpenBtn, assignAllBtn, h('div', { class: 'spacer' }), counts),
    box,
    moreBtn,
    h('div', { class: 'row', style: { fontSize: '12px', color: 'var(--dim)', gap: '16px' } },
      h('span', {}, '+ nicht zugeordnet'),
      h('span', { style: { color: '#38bdf8' } }, '✓ zugeordnet'),
      h('span', { style: { color: 'var(--warn)' } }, '⏱ eingeplant'),
      h('span', { style: { color: 'var(--ok)' } }, '✔ veröffentlicht'),
    ),
  )
  livePage.refresh = async () => {
    if (state.busy.size) return false
    const wanted = state.rows.length
    let data = await api.matrix(150), rows = [...data.rows]
    while (data.next_cursor && rows.length < wanted) { data = await api.matrix(150, data.next_cursor); rows.push(...data.rows) }
    if (window.CreatorStudioLive.busy() || state.busy.size) return false
    state.channels = data.channels; state.rows = rows; state.cursor = data.next_cursor
    draw(); fillAssignSelect()
  }
  return livePage

}
