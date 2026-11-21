import { platformIcon } from '../platform-icon.js?v=instagram-ai-20261121-1'
import { libraryCleanupCard } from './settings.js?v=instagram-ai-20261121-1'
import { api } from '../api.js?v=instagram-ai-20261121-1'
import { openUploadDialog } from '../upload.js?v=instagram-ai-20261121-1'
import { openCreateSetDialog, openSetDialog } from '../sets.js?v=instagram-ai-20261121-1'
import {
  h, append, clear, card, empty, field, modal, toast, guard, spinner, confirmDialog,
  mediaPreview, mediaTile, openMediaEditor, instagramCropWarning, LIFECYCLE, LIFECYCLE_ORDER, fmtDate, fmtDateTime, fmtBytes, debounce, attachPreview,
} from '../ui.js?v=instagram-ai-20261121-1'

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
  const tableBox = h('div', { class: 'table-scroll', tabindex: 0, role: 'region', 'aria-label': 'Medientabelle, seitlich scrollbar', style: { display: 'none' } })
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
          h('span', { class: 'hint num' }, String(mediaSet.asset_ids.length) + (mediaSet.telegram_price_stars ? ' · ⭐ ' + mediaSet.telegram_price_stars : '')),
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
    attachPreview(el, cover.mime === 'video/mp4' ? cover.thumb_url : cover.url, { delay: 400, size: 320 })
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
        h('img', { src: a.thumb_url, alt: '',title:instagramCropWarning(a,channels)?'⚠ Instagram-Bildformat passt nicht – Bild bearbeiten':'Bild bearbeiten',onClick:guard(async e=>{e.stopPropagation();await openMediaEditor(a,{platform:instagramCropWarning(a,channels)?'instagram':null,onSaved:()=>load(true)})}), style: { width: '32px', height: '32px', objectFit: 'cover', borderRadius: '5px',outline:instagramCropWarning(a,channels)?'3px solid #dc2626':'' } }),instagramCropWarning(a,channels)?h('span',{role:'img','aria-label':'Achtung: Instagram-Bildformat passt nicht',style:{color:'#b91c1c'}},'⚠'):null,
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
    // Keep the current search order even when edits change filter membership.
    const ids = state.items.map((asset) => asset.id)
    let d = await api.mediaDetail(id)
    let dialog, tags, nsfw, stars, saveStatus, saved, timer
    let queue = Promise.resolve()
    let busy = false
    let removed = false
    let assignmentMessage = ''
    const values = () => ({
      tags: tags.value.split(',').map((tag) => tag.trim()).filter(Boolean),
      nsfw_level: nsfw.value,
      telegram_price_stars: stars.value ? Number(stars.value) : null,
    })
    const saveNow = () => {
      clearTimeout(timer)
      if (!stars.checkValidity()) return Promise.reject(new Error("Telegram-Preis: ganze Zahl von 1 bis 25000 Sternen."))
      const payload = values()
      const assetId = d.id
      const serialized = JSON.stringify(payload)
      queue = queue.catch(() => {}).then(async () => {
        if (serialized === saved) return
        saveStatus.textContent = 'Speichert …'
        try {
          await api.updateMedia(assetId, payload)
          saved = serialized
          saveStatus.textContent = JSON.stringify(values()) === saved ? 'Gespeichert' : 'Änderungen werden gespeichert …'
        } catch (error) {
          saveStatus.textContent = 'Nicht gespeichert – bitte erneut versuchen'
          throw error
        }
      })
      return queue
    }
    const saveError = (error) => toast.error(error.message || 'Speichern fehlgeschlagen')
    const renderCurrent = () => {
      clear(dialog.body).appendChild(buildDetail(dialog.close))
      dialog.body.closest('.modal').querySelector('h3').textContent = d.filename
    }
    const changeAsset = async (action) => {
      if (busy) return
      busy = true
      dialog.body.inert = true
      try {
        await saveNow()
        await action()
        d = await api.mediaDetail(d.id)
        renderCurrent()
        await load()
        await refreshCounts()
      } finally {
        busy = false
        dialog.body.inert = false
      }
    }
    const removeChannel = async (channel) => {
      await changeAsset(async () => {
        // Set membership belongs to the whole sale unit. Never silently detach
        // its other images or change the global set composition.
        const sets = (d.sets || []).length ? await api.sets() : []
        const inherited = sets.filter(set => (d.sets || []).includes(set.id)
          && (set.assigned_channel_ids || []).includes(channel.id))
        if (inherited.length && !(await confirmDialog('Set-Zuordnung aufheben',
          `Dieses Bild ist über ${inherited.map(set => '„' + set.name + '“').join(', ')} zu ${channel.display_name} zugeordnet. `
          + 'Die Zuordnung dieser Sets zu diesem Kanal wird für alle enthaltenen Bilder aufgehoben. Die Sets selbst bleiben erhalten.',
          { okLabel: 'Set-Zuordnung aufheben' }))) return
        const affectedIds = [...new Set([d.id, ...inherited.flatMap(set => set.asset_ids)])]
        const check = await api.checkRemove({ asset_ids: affectedIds, channel_ids: [channel.id] })
        let cascade = false
        if (check.count > 0) {
          cascade = await new Promise(resolve => {
            modal('Geplante Posts betroffen', close => h('div', { class: 'col' },
              h('p', {}, `${check.count} offene Posts verwenden diese Bilder auf ${channel.display_name}. Wie möchtest du vorgehen?`),
              h('div', { class: 'row' },
                h('button', { type: 'button', onClick: () => { resolve(false); close() } }, 'Nur Zuordnung aufheben'),
                h('button', { type: 'button', class: 'danger', onClick: () => { resolve(true); close() } }, 'Auch geplante Posts abbrechen'),
                h('button', { type: 'button', onClick: close }, 'Abbrechen'),
              ),
            ), { beforeClose: () => { resolve(null) } })
          })
          if (cascade === null) return
        }
        await api.unassign({ asset_ids: [d.id], set_ids: inherited.map(set => set.id),
          channel_ids: [channel.id], cascade_posts: cascade })
        assignmentMessage = '✓ Zuordnung zu ' + channel.display_name + ' aufgehoben'
      })
    }
    const navigate = async (direction) => {
      if (busy) return
      busy = true
      dialog.body.inert = true
      try {
        await saveNow()
        const index = ids.indexOf(d.id)
        if (direction > 0 && index === ids.length - 1 && state.cursor) {
          await load(true)
          for (const asset of state.items) if (!ids.includes(asset.id)) ids.push(asset.id)
        }
        const nextId = ids[index + direction]
        if (nextId) {
          d = await api.mediaDetail(nextId)
          assignmentMessage = ''
          renderCurrent()
          dialog.body.scrollTop = 0
          dialog.body.closest('.modal-bg').scrollTop = 0
        } else {
          renderCurrent()
        }
      } finally {
        busy = false
        dialog.body.inert = false
      }
    }
    function buildDetail(close) {
      tags = h('input', { value: (d.tags || []).join(', ') })
      stars = h('input', {type:'number',min:1,max:25000,step:1,value:d.telegram_price_stars || '',placeholder:'Kein Pay-Angebot'})
      nsfw = h('select', {},
        ...['sfw', 'explicit'].map((v) => h('option', { value: v, selected: v === (d.nsfw_level === 'suggestive' ? 'explicit' : d.nsfw_level) }, v === 'sfw' ? 'SFW' : 'Explizit')))

      saved = JSON.stringify(values())
      saveStatus = h('span', { class: 'hint', role: 'status', 'aria-live': 'polite' }, 'Änderungen werden automatisch gespeichert')
      tags.addEventListener('input', () => {
        clearTimeout(timer)
        saveStatus.textContent = 'Änderungen werden gespeichert …'
        timer = setTimeout(() => { saveNow().catch(saveError) }, 350)
      })
      tags.addEventListener('change', () => { saveNow().catch(saveError) })
      nsfw.addEventListener('change', () => { saveNow().catch(saveError) })
      stars.addEventListener('change', () => { saveNow().catch(saveError) })

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
            return h('button', { type: 'button', class: 'chip', style: { color: c ? c.color : null },
              title: 'Zuordnung aufheben', 'aria-label': 'Zuordnung zu ' + (c ? c.display_name : id2) + ' aufheben',
              onClick: guard(() => removeChannel(c || { id: id2, display_name: id2 })),
            }, platformIcon(c?.platform), c ? c.display_name : id2,
              c && instagramCropWarning(d,[c]) ? h('span',{role:'img','aria-label':'Achtung: Instagram-Bildformat passt nicht',title:'Instagram-Bildformat passt nicht. Bitte das Bild zuschneiden.',style:{color:'#b91c1c',fontWeight:'700'}},'⚠') : null,
              h('span', { 'aria-hidden': 'true' }, ' ×'))
          }))
        : h('p', { class: 'hint' }, d.assigned_channel_ids.length ? 'Auf allen zugeordneten Kanälen gelaufen.' : 'Keinem Kanal zugeordnet.')

      const addChips = h('div', { class: 'row' }, ...channels
        .filter((c) => !d.assigned_channel_ids.includes(c.id))
        .map((c) => h('button', {
          type: 'button', class: 'chip', 'aria-label': '+ ' + c.display_name,
          onClick: guard(async () => {
            await changeAsset(async () => {
              const r = await api.assign({ asset_ids: [d.id], channel_ids: [c.id] })
              if (r.rejected.length) throw new Error(r.rejected[0].reason)
              assignmentMessage = '✓ Zu ' + c.display_name + ' zugeordnet'
            })
          }),
        }, platformIcon(c.platform), '+ ' + c.display_name)))

      const index = ids.indexOf(d.id)
      return h('div', { class: 'library-detail' },
        h('div', { class: 'row library-detail-nav', 'aria-label': 'Bildnavigation' },
          h('button', { type: 'button', disabled: index <= 0, 'aria-label': 'Vorheriges Bild', onClick: guard(() => navigate(-1)) }, '← Vorheriges Bild'),
          h('span', { class: 'hint num' }, (index + 1) + ' / ' + ids.length),
          h('button', { type: 'button', disabled: index === ids.length - 1 && !state.cursor, 'aria-label': 'Nächstes Bild', onClick: guard(() => navigate(1)) }, 'Nächstes Bild →'),
        ),
        h('div', { class: 'grid c2' },
          h('div', {},
            h('div', { class:'library-detail-image', dataset:{cropAsset:d.id,instagramAssigned:String(channels.some(c=>c.platform==='instagram'&&[...(d.assigned_channel_ids||[]),...(d.scheduled_channel_ids||[])].includes(c.id)))},
              style:{position:'relative',borderRadius:'10px',outline:instagramCropWarning(d,channels)?'3px solid #dc2626':''} },
              mediaPreview(d, { style: { display:'block',width: '100%', borderRadius: '10px', border: '1px solid var(--line)' },editPlatform:instagramCropWarning(d,channels)?'instagram':null,onEdited:async()=>{await saveNow();d=await api.mediaDetail(d.id);renderCurrent();await load(true)} }),
              h('span',{class:'image-crop-warning',hidden:!instagramCropWarning(d,channels),role:'img','aria-label':'Achtung: Instagram-Bildformat passt nicht',title:'Instagram-Bildformat passt nicht. Bild anklicken und zuschneiden.',style:{position:'absolute',top:'8px',right:'8px',background:'#b91c1c',color:'white',borderRadius:'50%',width:'30px',height:'30px',textAlign:'center',fontSize:'22px',lineHeight:'30px',pointerEvents:'none'}},'⚠')),
            h('div', { class: 'hint', style: { margin: '8px 0' } },
              `${d.image_crops?.variants?.all?.width || d.width}×${d.image_crops?.variants?.all?.height || d.height} · ${fmtBytes(d.bytes)} · ${d.mime}` + (d.image_crops?.variants?.instagram ? ` · Instagram: ${d.image_crops.variants.instagram.width}×${d.image_crops.variants.instagram.height}` : '')),
            field('Tags (kommagetrennt)', tags),
            field('Bildkategorie', nsfw),
            field('Telegram-Einzelpreis in Sternen', stars, 'Leer = nicht einzeln im Pay-Kanal planbar. Setpreise werden separat am Set festgelegt.'),
            d.ai_description ? field('KI-Beschreibung', h('p', { class: 'hint', style: { margin: 0 } }, d.ai_description)) : null,
            h('div', { class: 'row' },
              h('button', { class: 'primary', onClick: close }, 'Schließen'),
              h('button', { onClick: guard(async () => {
                await changeAsset(() => api.archiveMedia(d.id, d.lifecycle !== 'archived'))
                toast.ok('Status geändert')
              }) }, d.lifecycle === 'archived' ? 'Reaktivieren' : 'Archivieren'),
              h('button', { class: 'danger', onClick: () => {
                modal('Bild löschen', (closeConfirm) => {
                  const deleteBtn = h('button', { class: 'danger' }, 'Bild löschen')
                  deleteBtn.addEventListener('click', guard(async () => {
                    if (deleteBtn.disabled) return
                    deleteBtn.disabled = true
                    deleteBtn.textContent = 'Lösche …'
                    try {
                      await saveNow()
                      await api.deleteMedia(d.id)
                      removed = true
                      state.selected.delete(d.id)
                      closeConfirm(); close()
                      toast.ok('Bild gelöscht')
                      await load()
                      await Promise.all([refreshCounts(), refreshSets()])
                    } finally {
                      deleteBtn.disabled = false
                      deleteBtn.textContent = 'Bild löschen'
                    }
                  }))
                  return h('div', { class: 'col' },
                    h('p', {}, `„${d.filename}“ endgültig aus der Bibliothek löschen? Zugehörige Zuordnungen und Set-Mitgliedschaften werden entfernt. Bereits veröffentlichte Beiträge auf X oder Fanvue bleiben bestehen.`),
                    h('div', { class: 'row' }, deleteBtn, h('button', { onClick: closeConfirm }, 'Abbrechen')),
                  )
                })
              } }, 'Löschen'),
            ),
            saveStatus,
          ),
          h('div', { class: 'stack' },
            h('div', {},
              h('label', { class: 'lbl' }, 'Status'),
              h('span', { style: { color: LIFECYCLE[d.lifecycle].color } }, LIFECYCLE[d.lifecycle].label),
            ),
            h('div', {}, h('label', { class: 'lbl' }, 'Verwendungs-Historie'), history),
            h('div', {}, h('label', { class: 'lbl' }, 'Zugeordnet, aber noch offen'), openChannels),
            h('div', {}, h('label', { class: 'lbl' }, 'Weitere Kanäle zuordnen'), addChips,
              h('div', { class: 'hint library-assignment-feedback', role: 'status', 'aria-live': 'polite',
                style: { marginTop: '8px', minHeight: '1.4em', color: 'var(--ok)', overflowWrap: 'anywhere' },
              }, assignmentMessage)),
          ),
        ),
      )
    }
    dialog = modal(d.filename, buildDetail, {
      wide: true,
      beforeClose: async () => {
        if (busy) return false
        if (!removed) {
          busy = true
          dialog.body.inert = true
          try {
            await saveNow()
            await load()
            await refreshCounts()
          } finally {
            busy = false
            dialog.body.inert = false
          }
        }
      },
    })
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

  const priceFilter = h('select', {'aria-label':'Telegram-Preisfilter'},
    h('option',{value:''},'Alle Einzelbilder und Videos'), h('option',{value:'yes'},'Mit Telegram-Einzelpreis'), h('option',{value:'no'},'Ohne Telegram-Einzelpreis'))
  priceFilter.addEventListener('change', () => {state.q.has_telegram_price = priceFilter.value === '' ? null : priceFilter.value === 'yes'; load()})
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
    class: 'library-sidebar', id: 'library-filters',
  },
    h('label', { class: 'lbl' }, 'Lebenszyklus'), sideCounts,
    h('label', { class: 'lbl', style: { marginTop: '14px' } }, 'Sets'), sideSets,
    h('label', { class: 'lbl', style: { marginTop: '14px' } }, 'Gespeicherte Ansichten'), viewButtons,
    h('label', { class: 'lbl', style: { marginTop: '14px' } }, 'Kanal-Filter'), channelFilters,
    h('label', { class: 'lbl', style: { marginTop: '14px' } }, 'Tags'), tagSelect,
    h('label',{class:'lbl',style:{marginTop:'14px'}},'Telegram-Sternepreis'), priceFilter,
  )

  const filtersToggle = h('button', { class: 'library-filter-toggle', type: 'button', 'aria-expanded': 'false', 'aria-controls': 'library-filters',
    onClick: () => {
      const expanded = filtersToggle.getAttribute('aria-expanded') !== 'true'
      filtersToggle.setAttribute('aria-expanded', String(expanded))
      sidebar.classList.toggle('is-open', expanded)
    },
  }, 'Filter')
  const content = h('div', { class: 'library-content' },
    h('div', { class: 'page-head', style: { padding: '14px 20px', margin: 0, borderBottom: '1px solid var(--line)' } },
      h('h1', {}, 'Bibliothek'), filtersToggle,
      h('div', { style: { width: '230px' } }, searchInput),
      sortSelect, hideBtn,
      h('div', { class: 'spacer' }),
      viewBtn,
      h('button', { class: 'primary', onClick: openUpload }, 'Hochladen'),
    ),
    sidebar, bulkBar,
    h('div', { class: 'library-media' }, grid, tableBox, moreBtn),
  )

  const cleanup = h('details', {class:'card library-cleanup'}, h('summary', {}, 'Bibliothek aufräumen'))
  cleanup.addEventListener('toggle', async () => {
    if (!cleanup.open || cleanup.dataset.loaded) return
    cleanup.dataset.loaded = 'true'
    try { cleanup.appendChild(await libraryCleanupCard()) }
    catch (error) { cleanup.dataset.loaded = ''; toast.error(error.message) }
  })
  content.querySelector('.library-media').prepend(cleanup)

  await Promise.all([load(), refreshCounts(), refreshSets()])

  const livePage = h('div', { class: 'library-layout' }, content)
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
