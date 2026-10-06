// Matrix: Bilder × Kanäle. Beantwortet auf einen Blick "was fehlt wo noch".
import { api } from '../api.js?v=creatorstudio-matrix-reuse-20261006'
import { h, clear, empty, toast, guard, attachPreview, fmtDate, spinner } from '../ui.js?v=creatorstudio-preview-bounds-20261006'

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
  const state = { rows: [], channels: [], cursor: null, onlyOpen: false, hiddenStates: new Set(), busy: new Set() }

  const box = h('div', { class: 'matrix-wrap card' })
  const moreBtn = h('button', { style: { marginTop: '12px', display: 'none' } }, 'Mehr laden')
  const counts = h('div', { class: 'row', style: { gap: '14px' } })
  const visibleCount = h('span', { class: 'hint', 'aria-live': 'polite' })

  function matchesFilters(row) {
    const statuses = state.channels.map(channel => row.cells[channel.id] || 'none')
    return !statuses.some(status => state.hiddenStates.has(status)) &&
      (!state.onlyOpen || statuses.includes('none'))
  }

  const visibleRows = () => state.rows.filter(matchesFilters)

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
      if (matchesFilters(row)) {
        paintCell(row, channel, cell)
        drawCounts()
      } else {
        draw()
      }
    } catch (err) {
      toast.error(err.message)
    } finally {
      state.busy.delete(key)
      cell.classList.remove('busy')
    }
  }

  async function releaseForReuse(row, channel, button) {
    const key = row.asset.id + ':' + channel.id
    if (state.busy.has(key)) return
    state.busy.add(key)
    button.disabled = true
    try {
      row.asset = await api.releaseForReuse(channel.id, row.asset.id)
      row.cells[channel.id] = 'assigned'
      draw()
      toast.ok(`Bild für ${channel.name} erneut bereitgestellt`)
    } finally {
      state.busy.delete(key)
      if (button.isConnected) button.disabled = false
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
    const label = stateKey === 'assigned' && (row.asset.reusable_channel_ids || []).includes(channel.id) ? 'erneut bereitgestellt' : meta.label
    cell.appendChild(h('span', { class: 'mcell-label' }, label))
  }

  // ------------------------------------------------------------ Zeichnen
  function drawCounts() {
    visibleCount.textContent = `${visibleRows().length} von ${state.rows.length} geladenen Bildern sichtbar`
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
    const scrollTop = box.scrollTop, scrollLeft = box.scrollLeft
    const workspace = box.closest('.workspace-scroll')
    const workspaceTop = workspace?.scrollTop
    clear(box)
    const rows = visibleRows()
    // More results must stay reachable even when the loaded page is filtered out.
    moreBtn.style.display = state.cursor ? '' : 'none'

    if (!rows.length) {
      box.appendChild(empty(state.rows.length ? 'Keine Bilder für die gewählten Filter.' : 'Keine Bilder.'))
      drawCounts()
      if (workspace) workspace.scrollTop = workspaceTop
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
          const published = (row.cells[channel.id] || 'none') === 'published'
          const alreadyPlanned = (row.asset.scheduled_channel_ids || []).includes(channel.id)
          return h('td', { class: 'mcol-cell' },
            h('div', { class: 'col', style: { alignItems: 'center', gap: '5px' } }, cell,
              published ? h('button', {
                class: 'small', disabled: alreadyPlanned,
                title: alreadyPlanned ? 'Auf diesem Kanal bereits erneut eingeplant' : 'Für eine weitere Verplanung auf diesem Kanal freigeben',
                onClick: guard((event) => releaseForReuse(row, channel, event.currentTarget)),
              }, alreadyPlanned ? 'Bereits erneut eingeplant' : 'Erneut bereitstellen') : null,
            ),
          )
        }),
      )
    })

    box.appendChild(h('table', { class: 'matrix' },
      h('thead', {}, head),
      h('tbody', {}, ...body),
    ))
    drawCounts()
    box.scrollTop = scrollTop
    box.scrollLeft = scrollLeft
    if (workspace) workspace.scrollTop = workspaceTop
  }

  // ------------------------------------------------------------ Kopfleiste
  const onlyOpenBtn = h('button', {}, 'Nur mit offenen Kanälen')
  onlyOpenBtn.addEventListener('click', () => {
    state.onlyOpen = !state.onlyOpen
    onlyOpenBtn.className = state.onlyOpen ? 'primary' : ''
    draw()
  })

  const statusFilters = h('div', { class: 'row', style: { gap: '16px' }, 'aria-label': 'Bilder nach Status ausblenden' },
    ...[
      ['assigned', 'Zugeordnete ausblenden'],
      ['scheduled', 'Eingeplante ausblenden'],
      ['published', 'Veröffentlichte ausblenden'],
    ].map(([key, label]) => {
      const input = h('input', { type: 'checkbox' })
      input.addEventListener('change', () => {
        input.checked ? state.hiddenStates.add(key) : state.hiddenStates.delete(key)
        draw()
      })
      return h('label', { class: 'inline' }, input, label)
    }),
  )

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
    const ids = visibleRows()
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
    statusFilters,
    h('div', { class: 'hint' }, 'Ein Bild wird ausgeblendet, sobald es auf mindestens einem Kanal einen ausgewählten Status hat.'),
    visibleCount,
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
    const changed = JSON.stringify([state.channels, state.rows, state.cursor]) !== JSON.stringify([data.channels, rows, data.next_cursor])
    const channelsChanged = JSON.stringify(state.channels) !== JSON.stringify(data.channels)
    state.channels = data.channels; state.rows = rows; state.cursor = data.next_cursor
    if (changed) draw()
    if (channelsChanged) fillAssignSelect()
  }
  return livePage

}
