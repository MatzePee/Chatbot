// Zuordnungsseite: Bilder per Drag & Drop auf Kanäle ziehen.
// Ein Bild darf beliebig vielen Kanälen zugeordnet sein – X und Fanvue gleichzeitig
// ist der Normalfall, kein Konflikt.
import { api } from '../api.js?v=creatorstudio-layout-20260909'
import { openUploadDialog } from '../upload.js?v=creatorstudio-layout-20260909'
import {
  h, append, clear, empty, modal, toast, guard, spinner,
  mediaTile, LIFECYCLE, LIFECYCLE_ORDER, fmtDate, NSFW_RANK, debounce,
} from '../ui.js?v=creatorstudio-layout-20260909'

const POOL_FILTERS = [
  { key: 'all', label: 'Alle' },
  { key: 'unassigned', label: 'Nicht zugeordnet' },
  { key: 'unused', label: 'Noch nie gepostet' },
  { key: 'partially_used', label: 'Teilweise verbraucht' },
]

export default async function renderAssign() {
  const state = {
    columns: [], channels: [], pool: [], cursor: null,
    selected: new Set(), lastClicked: null,
    filter: 'all', search: '', tag: '',
    undo: [], hidden: new Set(), altDown: false,
    // Auf einem Kanal bereits veröffentlichte Bilder sind für die Zuordnung
    // erledigt – sie stehen standardmäßig nicht mehr im Weg.
    showUsed: false,
  }

  const tagList = await api.tags()

  const poolBody = h('div', { class: 'pool-body' })
  const poolFoot = h('div', { class: 'pool-foot', style: { display: 'none' } })
  const poolInfo = h('div', { class: 'row', style: { justifyContent: 'space-between', fontSize: '11px', color: 'var(--dim)' } })
  const columnsBox = h('div', { class: 'columns' })
  const colToggle = h('div', { class: 'board-head' })
  const undoBtn = h('button', {}, 'Rückgängig (0)')

  // ------------------------------------------------------------ Laden
  function poolQuery(cursor) {
    const q = { limit: Math.max(120, Math.min(500, state.pool.length)), sort: 'created_desc', cursor: cursor || null }
    if (state.search) q.q = state.search
    if (state.tag) q.tags_all = [state.tag]
    if (state.filter === 'unassigned') q.unassigned = true
    if (state.filter === 'unused') q.max_usage = 0
    if (state.filter === 'partially_used') q.lifecycle = ['partially_used']
    return q
  }

  async function loadPool(append_ = false) {
    const res = await api.searchMedia(poolQuery(append_ ? state.cursor : null))
    state.pool = append_ ? state.pool.concat(res.items) : res.items
    state.cursor = res.next_cursor
  }

  async function loadBoard() {
    const [board, channels] = await Promise.all([api.board(), api.channels()])
    state.columns = board.columns
    state.channels = channels
  }

  async function reloadAll() {
    await Promise.all([loadBoard(), loadPool(false)])
    drawPool(); drawColumns(); drawToggles()
  }

  const visibleColumns = () => state.columns.filter((c) => !state.hidden.has(c.channel.id))

  // ------------------------------------------------------------ Auswahl
  function toggleSelect(asset, e) {
    if (e.shiftKey && state.lastClicked) {
      const from = state.pool.findIndex((a) => a.id === state.lastClicked)
      const to = state.pool.findIndex((a) => a.id === asset.id)
      if (from >= 0 && to >= 0) {
        const [s, t] = from < to ? [from, to] : [to, from]
        state.pool.slice(s, t + 1).forEach((a) => state.selected.add(a.id))
      }
    } else if (e.metaKey || e.ctrlKey) {
      state.selected.has(asset.id) ? state.selected.delete(asset.id) : state.selected.add(asset.id)
    } else {
      state.selected.clear()
      state.selected.add(asset.id)
    }
    state.lastClicked = asset.id
    drawPool()
  }

  // ------------------------------------------------------------ Aktionen
  const assignTo = guard(async (channelId, assetIds, move = false) => {
    if (!assetIds.length) return
    const r = await api.assign({ asset_ids: assetIds, channel_ids: [channelId], mode: move ? 'move' : 'copy' })
    state.undo.push({ action: 'assign', asset_ids: assetIds, channel_ids: [channelId] })
    if (state.undo.length > 20) state.undo.shift()
    let msg = `${r.created} zugeordnet`
    if (r.skipped) msg += `, ${r.skipped} bereits vorhanden`
    if (r.rejected.length) msg += `, ${r.rejected.length} abgelehnt`
    toast.ok(msg)
    if (r.rejected.length) toast.error(r.rejected[0].reason)
    await reloadAll()
  })

  const requestRemove = guard(async (channelId, assetIds) => {
    const check = await api.checkRemove({ asset_ids: assetIds, channel_ids: [channelId] })
    if (check.count > 0) {
      modal('Geplante Posts betroffen', (close) =>
        h('div', { class: 'col' },
          h('div', { class: 'warnbox' },
            `${check.count} geplante Posts nutzen dieses Bild auf dem Kanal. Wenn du die Zuordnung entfernst, verlieren sie ihre Grundlage.`),
          h('div', { class: 'row' },
            h('button', { onClick: () => { close(); doRemove(channelId, assetIds, false) } }, 'Posts behalten'),
            h('button', { class: 'danger', onClick: () => { close(); doRemove(channelId, assetIds, true) } }, 'Posts mitlöschen'),
            h('div', { class: 'spacer' }),
            h('button', { onClick: close }, 'Abbrechen'),
          ),
        ))
      return
    }
    await doRemove(channelId, assetIds, false)
  })

  const doRemove = guard(async (channelId, assetIds, cascade) => {
    await api.unassign({ asset_ids: assetIds, channel_ids: [channelId], cascade_posts: cascade })
    state.undo.push({ action: 'unassign', asset_ids: assetIds, channel_ids: [channelId] })
    toast.ok(`${assetIds.length} Zuordnung(en) entfernt`)
    await reloadAll()
  })

  const undo = guard(async () => {
    const last = state.undo.pop()
    if (!last) return
    if (last.action === 'assign') await api.unassign({ asset_ids: last.asset_ids, channel_ids: last.channel_ids })
    else await api.assign({ asset_ids: last.asset_ids, channel_ids: last.channel_ids })
    toast.info('Rückgängig gemacht')
    await reloadAll()
  })

  // ------------------------------------------------------------ Drag & Drop
  function dragData(asset, from) {
    const ids = state.selected.has(asset.id) ? Array.from(state.selected) : [asset.id]
    return JSON.stringify({ asset_ids: ids, from: from || null })
  }

  function onDragStart(asset, from) {
    return (e) => {
      e.dataTransfer.setData('application/json', dragData(asset, from))
      e.dataTransfer.effectAllowed = 'copyMove'
    }
  }

  function wireColumnDrop(el, column) {
    el.addEventListener('dragover', (e) => {
      e.preventDefault()
      const ids = Array.from(state.selected)
      const assets = state.pool.filter((a) => ids.includes(a.id))
      const reject = assets.some((a) => (NSFW_RANK[a.nsfw_level] || 0) > (NSFW_RANK[column.channel.nsfw_level] || 0))
      el.classList.toggle('drop-no', reject)
      el.classList.toggle('drop-ok', !reject)
      e.dataTransfer.dropEffect = state.altDown ? 'move' : 'copy'
    })
    el.addEventListener('dragleave', () => el.classList.remove('drop-ok', 'drop-no'))
    el.addEventListener('drop', (e) => {
      e.preventDefault()
      el.classList.remove('drop-ok', 'drop-no')
      try {
        const payload = JSON.parse(e.dataTransfer.getData('application/json'))
        assignTo(column.channel.id, payload.asset_ids, state.altDown)
      } catch { /* ignorieren */ }
    })
  }

  poolBody.addEventListener('dragover', (e) => {
    e.preventDefault()
    // Dateien vom Schreibtisch: Ablagefläche hervorheben.
    if (e.dataTransfer.types.includes('Files')) poolBody.classList.add('filedrop')
  })
  poolBody.addEventListener('dragleave', () => poolBody.classList.remove('filedrop'))
  poolBody.addEventListener('drop', (e) => {
    e.preventDefault()
    poolBody.classList.remove('filedrop')

    // Fall 1: Bilddateien vom Rechner -> Upload-Dialog mit den Dateien öffnen.
    if (e.dataTransfer.files && e.dataTransfer.files.length) {
      const dropped = Array.from(e.dataTransfer.files)
      openUploadDialog({
        channels: state.channels,
        files: dropped,
        onDone: () => reloadAll(),
      })
      return
    }

    // Fall 2: Kachel aus einer Kanal-Spalte -> Zuordnung entfernen.
    try {
      const payload = JSON.parse(e.dataTransfer.getData('application/json'))
      if (payload.from) requestRemove(payload.from, payload.asset_ids)
    } catch { /* ignorieren */ }
  })

  // ------------------------------------------------------------ Zeichnen
  function drawPool() {
    clear(poolBody)
    if (!state.pool.length) {
      poolBody.appendChild(empty('Keine Bilder für diesen Filter.'))
    } else {
      const tiles = h('div', { class: 'tiles small' })
      for (const asset of state.pool) {
        tiles.appendChild(mediaTile(asset, state.channels, {
          selected: state.selected.has(asset.id),
          draggable: true,
          onClick: (e) => toggleSelect(asset, e),
          onDragStart: onDragStart(asset),
        }))
      }
      poolBody.appendChild(tiles)
      if (state.cursor) {
        poolBody.appendChild(h('button', {
          style: { width: '100%', justifyContent: 'center', marginTop: '10px' },
          onClick: guard(async () => { await loadPool(true); drawPool() }),
        }, 'Mehr laden'))
      }
    }

    clear(poolInfo)
    append(poolInfo, [
      h('span', {}, `${state.pool.length} Bilder · ${state.selected.size} ausgewählt`),
      state.selected.size
        ? h('button', { class: 'ghost small', style: { border: 'none' }, onClick: () => { state.selected.clear(); drawPool() } }, 'Auswahl aufheben')
        : null,
    ])

    clear(poolFoot)
    if (state.selected.size) {
      poolFoot.style.display = ''
      append(poolFoot, [
        h('div', { class: 'hint', style: { marginBottom: '6px' } }, 'Schnellzuordnung — Taste 1–9 oder Klick:'),
        h('div', { class: 'row' }, ...visibleColumns().map((col, i) =>
          h('button', {
            class: 'chip',
            style: { color: col.channel.color },
            onClick: () => assignTo(col.channel.id, Array.from(state.selected)),
          }, h('span', { class: 'hint' }, String(i + 1)), ' ' + col.channel.name))),
      ])
    } else {
      poolFoot.style.display = 'none'
    }
    undoBtn.textContent = `Rückgängig (${state.undo.length})`
    undoBtn.disabled = !state.undo.length
  }

  function drawColumns() {
    clear(columnsBox)
    const cols = visibleColumns()
    if (!cols.length) {
      columnsBox.appendChild(empty('Keine sichtbaren Kanäle. Lege zuerst einen Kanal an.'))
      return
    }
    cols.forEach((col, index) => {
      const body = h('div', { class: 'column-body' })
      // Auf DIESEM Kanal gelaufen? Die Sperre gilt pro Kanal – ein Bild, das
      // auf X lief, bleibt für Fanvue offen und darf dort nicht verschwinden.
      const usedHere = col.assets.filter((a) => (a.used_channel_ids || []).includes(col.channel.id))
      const shown = state.showUsed
        ? col.assets
        : col.assets.filter((a) => !(a.used_channel_ids || []).includes(col.channel.id))

      if (!shown.length) {
        body.appendChild(h('div', { class: 'dropzone-hint' },
          usedHere.length && !state.showUsed
            ? `Alle ${usedHere.length} zugeordneten Bilder sind hier gelaufen`
            : 'Bilder hierher ziehen'))
      } else {
        const tiles = h('div', { class: 'tiles small' })
        for (const asset of shown) {
          const tile = mediaTile(asset, state.channels, {
            draggable: true,
            onDragStart: onDragStart(asset, col.channel.id),
          })
          const wrap = h('div', { class: 'tile-wrap' }, tile,
            h('button', {
              class: 'rm', title: 'Zuordnung entfernen',
              onClick: (e) => { e.stopPropagation(); requestRemove(col.channel.id, [asset.id]) },
            }, '✕'))
          tiles.appendChild(wrap)
        }
        body.appendChild(tiles)
      }

      const el = h('div', { class: 'column' },
        h('div', { class: 'column-head' },
          h('div', { class: 'row', style: { gap: '6px' } },
            h('span', { class: 'hint' }, String(index + 1)),
            h('i', { class: 'dot', style: { background: col.channel.color } }),
            h('b', { style: { flex: '1' } }, col.channel.name),
            h('span', { class: 'hint', style: { textTransform: 'uppercase' } }, col.channel.platform),
          ),
          h('div', { class: 'row', style: { gap: '6px', marginTop: '5px', fontSize: '11px' } },
            h('i', { class: 'led ' + col.traffic_light }),
            h('span', { class: 'hint' },
              `${col.count} zugeordnet · ${col.available} offen` + (col.days_left !== null ? ` · ${col.days_left} Tage` : '')),
          ),
          usedHere.length
            ? h('div', { class: 'hint', style: { fontSize: '10px' } },
                state.showUsed
                  ? `${usedHere.length} davon hier bereits gelaufen`
                  : `${usedHere.length} bereits gelaufene ausgeblendet`)
            : null,
          col.empty_on ? h('div', { class: 'hint', style: { fontSize: '10px' } }, 'leer am ' + fmtDate(col.empty_on)) : null,
          h('div', { class: 'hint', style: { fontSize: '10px' } }, 'max. NSFW: ' + col.channel.nsfw_level),
        ),
        body,
      )
      wireColumnDrop(el, col)
      columnsBox.appendChild(el)
    })
  }

  function drawToggles() {
    clear(colToggle)
    append(colToggle, [
      h('span', { class: 'hint' }, 'Spalten:'),
      ...state.columns.map((col) => h('button', {
        class: 'chip' + (state.hidden.has(col.channel.id) ? ' off' : ''),
        onClick: () => {
          state.hidden.has(col.channel.id) ? state.hidden.delete(col.channel.id) : state.hidden.add(col.channel.id)
          drawColumns(); drawToggles(); drawPool()
        },
      }, h('i', { class: 'dot', style: { background: col.channel.color } }), col.channel.name)),
      h('span', { class: 'spacer' }),
      h('button', {
        class: 'chip' + (state.showUsed ? ' on' : ''),
        title: 'Bilder, die auf dem jeweiligen Kanal schon veröffentlicht wurden',
        onClick: () => { state.showUsed = !state.showUsed; drawColumns(); drawToggles() },
      }, state.showUsed ? '✓ Gelaufene sichtbar' : 'Gelaufene einblenden'),
      h('span', { class: 'hint' }, 'Ziehen = kopieren · Alt+Ziehen = verschieben'),
    ])
  }

  // ------------------------------------------------------------ Tastatur
  const onKeyDown = (e) => {
    if (e.key === 'Alt') state.altDown = true
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'a' && !/input|textarea|select/i.test(e.target.tagName)) {
      e.preventDefault()
      state.pool.forEach((a) => state.selected.add(a.id))
      drawPool()
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); undo() }
    if (/^[1-9]$/.test(e.key) && state.selected.size && !/input|textarea|select/i.test(e.target.tagName)) {
      const col = visibleColumns()[Number(e.key) - 1]
      if (col) assignTo(col.channel.id, Array.from(state.selected))
    }
    if (e.key === 'Escape') { state.selected.clear(); drawPool() }
  }
  const onKeyUp = (e) => { if (e.key === 'Alt') state.altDown = false }
  window.addEventListener('keydown', onKeyDown)
  window.addEventListener('keyup', onKeyUp)
  // Beim Seitenwechsel wieder abmelden
  window.addEventListener('hashchange', function off() {
    window.removeEventListener('keydown', onKeyDown)
    window.removeEventListener('keyup', onKeyUp)
    window.removeEventListener('hashchange', off)
  })

  // ------------------------------------------------------------ Kopfzeile Pool
  const searchInput = h('input', { placeholder: 'Suchen (Dateiname, Tags, Beschreibung)' })
  searchInput.addEventListener('input', debounce(guard(async () => {
    state.search = searchInput.value
    await loadPool(); drawPool()
  }), 400))

  const filterRow = h('div', { class: 'row' }, ...POOL_FILTERS.map((f) => {
    const btn = h('button', {
      class: 'chip' + (state.filter === f.key ? ' on' : ''),
      onClick: guard(async () => {
        state.filter = f.key
        filterRow.querySelectorAll('.chip').forEach((c) => c.classList.remove('on'))
        btn.classList.add('on')
        await loadPool(); drawPool()
      }),
    }, f.label)
    return btn
  }))

  const tagSelect = h('select', {},
    h('option', { value: '' }, 'Alle Tags'),
    ...tagList.map((t) => h('option', { value: t.tag }, `${t.tag} (${t.count})`)))
  tagSelect.addEventListener('change', guard(async () => {
    state.tag = tagSelect.value
    await loadPool(); drawPool()
  }))

  undoBtn.addEventListener('click', undo)

  await reloadAll()

  const legend = h('div', { class: 'row', style: { padding: '6px 20px', borderTop: '1px solid var(--line)', fontSize: '11px', color: 'var(--dim)' } },
    h('span', {}, 'Klick = auswählen · Shift/Strg = Mehrfachauswahl · Strg+A = alle · Strg+Z = rückgängig'),
    h('span', { class: 'spacer' }),
    ...LIFECYCLE_ORDER.slice(0, 5).map((k) =>
      h('span', { class: 'row', style: { gap: '4px' } },
        h('i', { class: 'dot', style: { background: LIFECYCLE[k].color } }), LIFECYCLE[k].label)),
  )

  const livePage = h('div', {},
    h('div', { class: 'page-head', style: { padding: '14px 20px', margin: 0, borderBottom: '1px solid var(--line)' } },
      h('h1', {}, 'Zuordnung'),
      h('span', { class: 'hint' }, 'Ein Bild darf gleichzeitig auf X und Fanvue laufen.'),
      h('div', { class: 'spacer' }),
      h('button', {
        class: 'primary',
        onClick: () => openUploadDialog({
          channels: state.channels,
          onDone: () => reloadAll(),
        }),
      }, '⇪ Bilder hochladen'),
      undoBtn,
      h('button', { onClick: guard(reloadAll) }, 'Neu laden'),
    ),
    h('div', { class: 'assign' },
      h('div', { class: 'pool' },
        h('div', { class: 'pool-head' }, searchInput, filterRow, tagSelect, poolInfo),
        poolBody,
        poolFoot,
      ),
      h('div', { class: 'board' }, colToggle, columnsBox),
    ),
    legend,
  )
  livePage.refresh = async () => {
    if (state.selected.size) return false
    const wanted = state.pool.length, q = { ...poolQuery(), limit: 120 }
    const [board, channels] = await Promise.all([api.board(), api.channels()])
    let res = await api.searchMedia(q), pool = [...res.items]
    while (res.next_cursor && pool.length < wanted) { res = await api.searchMedia({ ...q, cursor: res.next_cursor }); pool.push(...res.items) }
    if (window.CreatorStudioLive.busy() || state.selected.size) return false
    state.columns = board.columns; state.channels = channels; state.pool = pool; state.cursor = res.next_cursor
    drawPool(); drawColumns(); drawToggles()
  }
  return livePage

}
