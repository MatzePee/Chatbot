import { api } from '../api.js?v=creatorstudio-layout-20260909'
import { openUploadDialog } from '../upload.js?v=creatorstudio-layout-20260909'
import { openCreateSetDialog, openSetDialog } from '../sets.js?v=creatorstudio-layout-20260909'
import {
  h, append, clear, card, empty, field, modal, toast, guard, spinner,
  mediaTile, LIFECYCLE, LIFECYCLE_ORDER, fmtDate, fmtDateTime, fmtBytes, debounce, attachPreview,
} from '../ui.js?v=creatorstudio-layout-20260909'

export default async function renderLibrary({ query }) {
  const [channels, views, tagList] = await Promise.all([api.channels(), api.views(), api.tags()])

  const state = {
    q: { limit: 120, sort: 'created_desc', lifecycle: query.get('lifecycle') ? [query.get('lifecycle')] : [] },
    hideConsumed: true,
    items: [],
    cursor: null,
    selected: new Set(),
    counts: null,
    sets: [],
    expanded: new Set(),
  }

  const grid = h('div', { class: 'tiles' })
  const tableBox = h('div', { style: { display: 'none' } })
  const moreBtn = h('button', { style: { width: '100%', justifyContent: 'center', marginTop: '14px', display: 'none' } }, 'Mehr laden')
  const bulkBar = h('div', { class: 'row', style: { display: 'none', padding: '8px 20px', background: 'rgba(99,102,241,.12)', borderBottom: '1px solid var(--line)' } })
  const sideCounts = h('div')
  let viewMode = 'grid'

  // ------------------------------------------------------------ Laden
  function effectiveQuery(cursor) {
    const q = { ...state.q, limit: Math.max(state.q.limit, state.items.length), cursor: cursor || null }
    if (state.hideConsumed && (!q.lifecycle || !q.lifecycle.length)) {
      q.lifecycle = ['new', 'assigned', 'scheduled', 'partially_used']
    }
    return q
  }

  async function load(append_ = false) {
    const res = await api.searchMedia(effectiveQuery(append_ ? state.cursor : null))
    state.items = append_ ? state.items.concat(res.items) : res.items
    state.cursor = res.next_cursor
    draw()
  }

  async function refreshCounts() {
    state.counts = await api.counts(true)
    drawCounts()
  }

  async function refreshSets() {
    state.sets = await api.sets()
    drawSets()
  }

  const sideSets = h('div')

  function drawSets() {
    clear(sideSets)
    if (!state.sets.length) {
      sideSets.appendChild(h('div', { class: 'hint' },
        'Noch keine Sets. Mehrere Bilder auswählen und „Set bilden" klicken.'))
      return
    }
    sideSets.appendChild(h('button', {
      class: 'ghost',
      style: { width: '100%', justifyContent: 'space-between', border: 'none' },
      onClick: () => { state.q = { ...state.q, set_id: null }; load() },
    }, 'Alle Bilder', h('span', { class: 'hint num' }, '')))

    for (const mediaSet of state.sets) {
      const active = state.q.set_id === mediaSet.id
      sideSets.appendChild(h('div', { class: 'row', style: { gap: '2px' } },
        h('button', {
          class: 'ghost',
          style: {
            flex: '1', justifyContent: 'flex-start', border: 'none', minWidth: '0',
            background: active ? 'var(--bg-2)' : 'transparent',
          },
          title: 'Nur die Bilder dieses Sets zeigen',
          onClick: () => {
            state.q = { ...state.q, set_id: active ? null : mediaSet.id }
            state.hideConsumed = false
            load()
          },
        },
          h('span', { style: { flex: '1', textAlign: 'left', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } },
            '▣ ' + mediaSet.name),
          h('span', { class: 'hint num' }, String(mediaSet.asset_ids.length)),
        ),
        h('button', {
          class: 'ghost small', style: { border: 'none' }, title: 'Set bearbeiten',
          onClick: () => openSetDialog(mediaSet, {
            channels,
            onDone: async () => { await refreshSets(); await load(); await refreshCounts() },
          }),
        }, '⚙'),
      ))
    }
  }

  // ------------------------------------------------------------ Zeichnen
  const tileFor = (asset) => mediaTile(asset, channels, {
    selected: state.selected.has(asset.id),
    onClick: (e) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey) {
        state.selected.has(asset.id) ? state.selected.delete(asset.id) : state.selected.add(asset.id)
        draw(); drawBulk()
      } else {
        openDetail(asset.id)
      }
    },
  })

  /**
   * Ein Set belegt im Raster nur eine Kachel – als Stapel mit dem Titelbild
   * obenauf. Bei vielen Bildern wäre die Bibliothek sonst voll mit Serien, die
   * ohnehin zusammengehören. Ein Klick klappt den Stapel auf.
   */
  function stackTile(mediaSet, assets) {
    const cover = assets[0]
    const allSelected = assets.every((a) => state.selected.has(a.id))

    const el = h('div', {
      class: 'tile stack' + (allSelected ? ' selected' : ''),
      title: `${mediaSet ? mediaSet.name : 'Set'} — ${assets.length} Bilder, werden gemeinsam behandelt`,
      onClick: (e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey) {
          // Auswahl gilt für den ganzen Stapel – so, wie er auch gepostet wird.
          for (const a of assets) {
            allSelected ? state.selected.delete(a.id) : state.selected.add(a.id)
          }
          draw(); drawBulk()
        } else {
          state.expanded.add(mediaSet.id)
          draw()
        }
      },
    },
      h('img', { src: cover.thumb_url, alt: '', loading: 'lazy' }),
      h('span', { class: 'stackcount' }, '▣ ' + assets.length),
      h('span', { class: 'cap' },
        h('i', { class: 'dot', style: { background: 'var(--accent)' } }),
        mediaSet ? mediaSet.name : 'Set'),
    )
    attachPreview(el, cover.url, { delay: 400, size: 320 })
    return el
  }

  function draw() {
    clear(grid); clear(tableBox)
    if (!state.items.length) {
      grid.appendChild(empty('Keine Bilder für diese Filter.'))
    } else if (viewMode === 'grid') {
      // Nach Set gruppieren, dabei die Reihenfolge des ersten Mitglieds behalten.
      const bySet = new Map()
      for (const asset of state.items) {
        const setId = (asset.set_ids || [])[0]
        if (!setId) continue
        if (!bySet.has(setId)) bySet.set(setId, [])
        bySet.get(setId).push(asset)
      }
      // Ist bereits auf ein Set gefiltert, wäre ein Stapel sinnlos.
      const stacking = !state.q.set_id
      const done = new Set()

      for (const asset of state.items) {
        const setId = (asset.set_ids || [])[0]
        const group = setId ? bySet.get(setId) : null

        if (!stacking || !group || group.length < 2 || state.expanded.has(setId)) {
          if (setId && state.expanded.has(setId) && stacking && !done.has(setId)) {
            done.add(setId)
            const mediaSet = state.sets.find((x) => x.id === setId)
            grid.appendChild(h('div', {
              class: 'tile stackclose',
              title: 'Stapel wieder zuklappen',
              onClick: () => { state.expanded.delete(setId); draw() },
            }, h('span', {}, '▣'), h('b', {}, mediaSet ? mediaSet.name : 'Set'),
              h('span', { class: 'hint' }, 'zuklappen')))
          }
          grid.appendChild(tileFor(asset))
          continue
        }
        if (done.has(setId)) continue
        done.add(setId)
        grid.appendChild(stackTile(state.sets.find((x) => x.id === setId) || { id: setId }, group))
      }
    } else {
      tableBox.appendChild(buildTable())
    }
    grid.style.display = viewMode === 'grid' ? '' : 'none'
    tableBox.style.display = viewMode === 'table' ? '' : 'none'
    moreBtn.style.display = state.cursor ? '' : 'none'
    drawBulk()
  }

  function buildTable() {
    const rows = state.items.map((a) => h('tr', {
      style: { cursor: 'pointer' },
      onClick: () => openDetail(a.id),
    },
      h('td', {}, h('span', { class: 'row', style: { gap: '8px' } },
        h('img', { src: a.thumb_url, alt: '', style: { width: '32px', height: '32px', objectFit: 'cover', borderRadius: '5px' } }),
        a.filename)),
      h('td', { style: { color: LIFECYCLE[a.lifecycle].color } }, LIFECYCLE[a.lifecycle].label),
      h('td', { class: 'hint' }, a.assigned_channel_ids.map((id) => {
        const c = channels.find((x) => x.id === id); return c ? c.display_name : '?'
      }).join(', ') || '—'),
      h('td', { class: 'num' }, a.usage_count),
      h('td', { class: 'hint' }, fmtDate(a.last_used_at)),
      h('td', { class: 'hint' }, (a.tags || []).join(', ')),
    ))
    return h('table', {},
      h('thead', {}, h('tr', {}, ...['Datei', 'Lebenszyklus', 'Kanäle', 'Verwendungen', 'Zuletzt', 'Tags'].map((t) => h('th', {}, t)))),
      h('tbody', {}, ...rows),
    )
  }

  function drawCounts() {
    clear(sideCounts)
    const c = state.counts
    sideCounts.appendChild(h('button', {
      class: 'ghost',
      style: { width: '100%', justifyContent: 'space-between', border: 'none' },
      onClick: () => { state.q.lifecycle = []; load() },
    }, 'Alle', h('span', { class: 'hint num' }, c ? c.total : '—')))

    for (const key of LIFECYCLE_ORDER) {
      sideCounts.appendChild(h('button', {
        class: 'ghost',
        style: {
          width: '100%', justifyContent: 'flex-start', border: 'none',
          background: state.q.lifecycle[0] === key ? 'var(--bg-2)' : 'transparent',
        },
        onClick: () => { state.q.lifecycle = [key]; state.hideConsumed = false; load() },
      },
        h('i', { class: 'dot', style: { background: LIFECYCLE[key].color } }),
        h('span', { style: { flex: '1', textAlign: 'left' } }, LIFECYCLE[key].label),
        h('span', { class: 'hint num' }, c ? (c[key] ?? 0) : '—'),
      ))
    }
  }

  function drawBulk() {
    clear(bulkBar)
    if (!state.selected.size) { bulkBar.style.display = 'none'; return }
    bulkBar.style.display = 'flex'
    const ids = () => Array.from(state.selected)

    const channelSelect = h('select', { style: { width: '190px' } },
      h('option', { value: '' }, 'Zuordnen zu …'),
      ...channels.map((c) => h('option', { value: c.id }, c.display_name)))
    channelSelect.addEventListener('change', guard(async () => {
      if (!channelSelect.value) return
      const r = await api.assign({ asset_ids: ids(), channel_ids: [channelSelect.value] })
      toast.ok(`${r.created} zugeordnet, ${r.skipped} bereits vorhanden`)
      if (r.rejected.length) toast.error(r.rejected[0].reason)
      state.selected.clear(); await load(); await refreshCounts()
    }))

    append(bulkBar, [
      h('b', {}, state.selected.size + ' ausgewählt'),
      h('button', { class: 'small', onClick: guard(async () => {
        await api.bulkMedia({ asset_ids: ids(), add_tags: [], remove_tags: [], archive: true })
        toast.ok('Archiviert'); state.selected.clear(); await load(); await refreshCounts()
      }) }, 'Archivieren'),
      h('button', { class: 'small', onClick: guard(async () => {
        await api.bulkMedia({ asset_ids: ids(), add_tags: [], remove_tags: [], archive: false })
        toast.ok('Reaktiviert'); state.selected.clear(); await load(); await refreshCounts()
      }) }, 'Reaktivieren'),
      h('button', { class: 'small', onClick: guard(async () => {
        const tag = prompt('Tag hinzufügen:')
        if (!tag) return
        await api.bulkMedia({ asset_ids: ids(), add_tags: [tag], remove_tags: [] })
        toast.ok('Tag gesetzt'); state.selected.clear(); await load()
      }) }, 'Tag setzen'),
      channelSelect,
      h('button', {
        class: 'small primary',
        disabled: state.selected.size < 2,
        title: state.selected.size < 2 ? 'Mindestens zwei Bilder auswählen' : '',
        onClick: () => openCreateSetDialog({
          // Reihenfolge der Kacheln übernehmen, nicht die Klickreihenfolge –
          // sie entspricht dem, was man auf dem Schirm sieht.
          assets: state.items.filter((a) => state.selected.has(a.id)),
          onDone: async () => {
            state.selected.clear()
            await refreshSets(); await load(); await refreshCounts()
          },
        }),
      }, 'Set bilden'),
      h('div', { class: 'spacer' }),
      h('button', { class: 'small', onClick: () => { state.selected.clear(); draw() } }, 'Auswahl aufheben'),
    ])
  }

  // ------------------------------------------------------------ Detail
  async function openDetail(id) {
    const d = await api.mediaDetail(id)
    modal(d.filename, (close) => {
      const tags = h('input', { value: (d.tags || []).join(', ') })
      const nsfw = h('select', {},
        ...['sfw', 'suggestive', 'explicit'].map((v) => h('option', { value: v, selected: v === d.nsfw_level }, v)))

      const history = d.history.length
        ? h('div', { class: 'col' }, ...d.history.map((e) => {
            const c = channels.find((x) => x.id === e.channel_id)
            return h('div', { style: { border: '1px solid var(--line)', borderRadius: '8px', padding: '6px 8px', fontSize: '12px' } },
              h('div', { class: 'row' },
                h('i', { class: 'dot', style: { background: c ? c.color : '#888' } }),
                h('b', {}, e.channel_name),
                h('span', { class: 'hint' }, fmtDateTime(e.used_at)),
                e.external_url ? h('a', { href: e.external_url, target: '_blank', rel: 'noreferrer', style: { marginLeft: 'auto' } }, 'Post ansehen') : null,
              ),
              e.post_text ? h('div', { class: 'hint' }, e.post_text) : null,
            )
          }))
        : h('p', { class: 'hint' }, 'Noch nie veröffentlicht.')

      const openChannels = d.open_channels.length
        ? h('div', { class: 'row' }, ...d.open_channels.map((id2) => {
            const c = channels.find((x) => x.id === id2)
            return h('span', { class: 'chip', style: { color: c ? c.color : null } }, c ? c.display_name : id2)
          }))
        : h('p', { class: 'hint' }, 'Auf allen zugeordneten Kanälen gelaufen.')

      const addChips = h('div', { class: 'row' }, ...channels
        .filter((c) => !d.assigned_channel_ids.includes(c.id))
        .map((c) => h('button', {
          class: 'chip',
          onClick: guard(async () => {
            const r = await api.assign({ asset_ids: [d.id], channel_ids: [c.id] })
            if (r.rejected.length) { toast.error(r.rejected[0].reason); return }
            toast.ok('Zu ' + c.display_name + ' zugeordnet')
            close(); await load(); await refreshCounts()
          }),
        }, '+ ' + c.display_name)))

      return h('div', { class: 'grid c2' },
        h('div', {},
          h('img', { src: d.url, alt: '', style: { width: '100%', borderRadius: '10px', border: '1px solid var(--line)' } }),
          h('div', { class: 'hint', style: { margin: '8px 0' } },
            `${d.width}×${d.height} · ${fmtBytes(d.bytes)} · ${d.mime}`),
          field('Tags (kommagetrennt)', tags),
          field('NSFW-Level', nsfw),
          d.ai_description ? field('KI-Beschreibung', h('p', { class: 'hint', style: { margin: 0 } }, d.ai_description)) : null,
          h('div', { class: 'row' },
            h('button', { class: 'primary', onClick: guard(async () => {
              await api.updateMedia(d.id, {
                tags: tags.value.split(',').map((t) => t.trim()).filter(Boolean),
                nsfw_level: nsfw.value,
              })
              toast.ok('Gespeichert'); close(); await load()
            }) }, 'Speichern'),
            h('button', { onClick: guard(async () => {
              await api.archiveMedia(d.id, d.lifecycle !== 'archived')
              toast.ok('Status geändert'); close(); await load(); await refreshCounts()
            }) }, d.lifecycle === 'archived' ? 'Reaktivieren' : 'Archivieren'),
          ),
        ),
        h('div', { class: 'stack' },
          h('div', {},
            h('label', { class: 'lbl' }, 'Status'),
            h('span', { style: { color: LIFECYCLE[d.lifecycle].color } }, LIFECYCLE[d.lifecycle].label),
          ),
          h('div', {}, h('label', { class: 'lbl' }, 'Verwendungs-Historie'), history),
          h('div', {}, h('label', { class: 'lbl' }, 'Zugeordnet, aber noch offen'), openChannels),
          h('div', { class: 'warnbox' },
            'Dieses Bild darf auf beliebig vielen Kanälen laufen. Die Sperre gilt nur pro Kanal — eine Veröffentlichung auf X blockiert Fanvue nicht.'),
          h('div', {}, h('label', { class: 'lbl' }, 'Weitere Kanäle zuordnen'), addChips),
        ),
      )
    }, { wide: true })
  }

  // ------------------------------------------------------------ Upload
  function openUpload() {
    openUploadDialog({
      channels,
      onDone: async () => { await load(false); await refreshCounts() },
    })
  }

  // ------------------------------------------------------------ Seitenleiste
  const searchInput = h('input', { placeholder: 'Suchen …', value: state.q.q || '' })
  searchInput.addEventListener('input', debounce(() => { state.q.q = searchInput.value; load() }, 400))

  const sortSelect = h('select', { style: { width: '170px' } },
    ...[
      ['created_desc', 'Neueste zuerst'], ['created_asc', 'Älteste zuerst'],
      ['used_asc', 'Am längsten ungenutzt'], ['usage_asc', 'Am wenigsten genutzt'],
      ['usage_desc', 'Am meisten genutzt'], ['filename', 'Dateiname'],
    ].map(([v, l]) => h('option', { value: v }, l)))
  sortSelect.addEventListener('change', () => { state.q.sort = sortSelect.value; load() })

  const hideBtn = h('button', { class: 'primary' }, 'Verbrauchte ausblenden')
  hideBtn.addEventListener('click', () => {
    state.hideConsumed = !state.hideConsumed
    hideBtn.className = state.hideConsumed ? 'primary' : ''
    load()
  })

  const viewBtn = h('button', {}, 'Tabelle')
  viewBtn.addEventListener('click', () => {
    viewMode = viewMode === 'grid' ? 'table' : 'grid'
    clear(viewBtn).appendChild(document.createTextNode(viewMode === 'grid' ? 'Tabelle' : 'Kacheln'))
    draw()
  })

  const tagSelect = h('select', {},
    h('option', { value: '' }, 'Alle Tags'),
    ...tagList.map((t) => h('option', { value: t.tag }, `${t.tag} (${t.count})`)))
  tagSelect.addEventListener('change', () => {
    state.q.tags_all = tagSelect.value ? [tagSelect.value] : []
    load()
  })

  const viewButtons = h('div', {}, ...views.map((v) => h('button', {
    class: 'ghost', style: { width: '100%', justifyContent: 'flex-start', border: 'none' },
    onClick: () => { state.q = { limit: 120, sort: 'created_desc', ...v.query }; state.hideConsumed = false; load() },
  }, '★ ' + v.name)))

  const channelFilters = h('div', {}, ...channels.map((c) => h('div', { style: { marginBottom: '6px' } },
    h('div', { class: 'row', style: { gap: '6px', fontSize: '12px' } },
      h('i', { class: 'dot', style: { background: c.color } }), c.display_name),
    h('div', { class: 'row', style: { gap: '4px', paddingLeft: '14px', marginTop: '2px' } },
      h('button', { class: 'chip', onClick: () => { state.q = { ...state.q, assigned_to: [c.id], used_on: [], not_used_on: [] }; load() } }, 'zugeordnet'),
      h('button', { class: 'chip', onClick: () => { state.q = { ...state.q, used_on: [c.id], assigned_to: [], not_used_on: [] }; load() } }, 'gelaufen'),
      h('button', { class: 'chip', onClick: () => { state.q = { ...state.q, not_used_on: [c.id], used_on: [], assigned_to: [c.id] }; load() } }, 'offen'),
    ),
  )))

  moreBtn.addEventListener('click', guard(() => load(true)))

  const sidebar = h('aside', {
    style: { width: '215px', flex: '0 0 215px', borderRight: '1px solid var(--line)', overflowY: 'auto', padding: '12px' },
  },
    h('label', { class: 'lbl' }, 'Lebenszyklus'), sideCounts,
    h('label', { class: 'lbl', style: { marginTop: '14px' } }, 'Sets'), sideSets,
    h('label', { class: 'lbl', style: { marginTop: '14px' } }, 'Gespeicherte Ansichten'), viewButtons,
    h('label', { class: 'lbl', style: { marginTop: '14px' } }, 'Kanal-Filter'), channelFilters,
    h('label', { class: 'lbl', style: { marginTop: '14px' } }, 'Tags'), tagSelect,
  )

  const content = h('div', { style: { flex: '1', display: 'flex', flexDirection: 'column', overflow: 'hidden' } },
    h('div', { class: 'page-head', style: { padding: '14px 20px', margin: 0, borderBottom: '1px solid var(--line)' } },
      h('h1', {}, 'Bibliothek'),
      h('div', { style: { width: '230px' } }, searchInput),
      sortSelect, hideBtn,
      h('div', { class: 'spacer' }),
      viewBtn,
      h('button', { class: 'primary', onClick: openUpload }, 'Hochladen'),
    ),
    bulkBar,
    h('div', { style: { flex: '1', overflowY: 'auto', padding: '16px' } }, grid, tableBox, moreBtn),
  )

  await Promise.all([load(), refreshCounts(), refreshSets()])

  const livePage = h('div', { style: { display: 'flex', height: 'calc(100dvh - var(--workspace-header-height))', overflow: 'hidden' } }, sidebar, content)
  livePage.refresh = async () => {
    if (state.selected.size) return false
    const wanted = state.items.length, q = { ...effectiveQuery(), limit: 120 }
    let res = await api.searchMedia(q), items = [...res.items]
    while (res.next_cursor && items.length < wanted) { res = await api.searchMedia({ ...q, cursor: res.next_cursor }); items.push(...res.items) }
    if (window.CreatorStudioLive.busy() || state.selected.size) return false
    state.items = items; state.cursor = res.next_cursor; draw()
    await Promise.all([refreshCounts(), refreshSets()])
  }
  return livePage

}
