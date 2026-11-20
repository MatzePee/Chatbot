// Bild-Sets: Medien, die zusammen in einem Post erscheinen sollen.
import { api } from './api.js?v=image-warning-20261121-1'
import {
  h, append, clear, empty, field, modal, toast, guard, spinner, confirmDialog, attachPreview,
} from './ui.js?v=image-warning-20261121-1'

/** Set aus einer Auswahl anlegen. Reihenfolge ist die Reihenfolge im Post. */
export function openCreateSetDialog({ assets, onDone = () => {} }) {
  modal(`Set aus ${assets.length} Medien`, (close) => {
    const name = h('input', {
      value: `Set ${new Date().toLocaleDateString('de-DE')}`,
      placeholder: 'Name des Sets',
    })
    const description = h('input', { placeholder: 'Notiz (optional)' })
    const stars = h('input', {type:'number',min:1,max:25000,step:1,placeholder:'Kein Pay-Angebot'})

    // Reihenfolge per Ziehen. Das erste Bild ist gleichzeitig das Titelbild.
    let order = assets.slice()
    const strip = h('div', { class: 'setstrip' })
    const drawStrip = () => {
      clear(strip)
      order.forEach((asset, index) => {
        const tile = h('div', {
          class: 'setitem', draggable: true,
          title: `${index + 1}. ${asset.filename}`,
        },
          h('img', { src: asset.thumb_url, alt: '' }),
          h('span', { class: 'setpos' }, String(index + 1)),
          h('button', {
            class: 'setdrop', title: 'Aus dem Set nehmen',
            onClick: (e) => {
              e.stopPropagation()
              order = order.filter((a) => a.id !== asset.id)
              drawStrip()
            },
          }, '✕'),
        )
        attachPreview(tile, asset.url, { size: 300 })
        tile.addEventListener('dragstart', (e) => e.dataTransfer.setData('text/plain', asset.id))
        tile.addEventListener('dragover', (e) => e.preventDefault())
        tile.addEventListener('drop', (e) => {
          e.preventDefault()
          const draggedId = e.dataTransfer.getData('text/plain')
          if (draggedId === asset.id) return
          const from = order.findIndex((a) => a.id === draggedId)
          const to = order.findIndex((a) => a.id === asset.id)
          if (from < 0 || to < 0) return
          order.splice(to, 0, order.splice(from, 1)[0])
          drawStrip()
        })
        strip.appendChild(tile)
      })
      counter.textContent = order.length < 2
        ? 'Ein Set braucht mindestens zwei Medien.'
        : `${order.length} Medien · das erste ist das Titelbild`
    }
    const counter = h('div', { class: 'hint' })
    drawStrip()

    const save = guard(async () => {
      if (order.length < 2) { toast.error('Mindestens zwei Medien auswählen'); return }
      if (!stars.checkValidity() || (stars.value && order.length > 10)) {toast.error('Pay-Set: 1–25000 Sterne und höchstens 10 Medien.'); return}
      const created = await api.createSet({
        telegram_price_stars: stars.value ? Number(stars.value) : null,
        name: name.value.trim() || 'Unbenanntes Set',
        description: description.value,
        asset_ids: order.map((a) => a.id),
        is_ordered: true,
      })
      toast.ok(`Set „${created.name}" mit ${created.asset_ids.length} Medien angelegt`)
      close()
      await onDone()
    })

    return h('div', { class: 'col' },
      h('div', { class: 'grid c2' }, field('Name', name), field('Notiz', description)),
      field('Telegram-Setpreis in Sternen', stars, 'Gesamtpreis für das ganze Set. Einzelbildpreise werden nicht addiert. Leer = nicht für Pay planbar.'),
      h('label', { class: 'lbl' }, 'Reihenfolge im Post'),
      strip,
      counter,
      h('div', { class: 'hint', style: { marginTop: '6px' } },
        'Die Medien eines Sets werden gemeinsam in einem Post veröffentlicht. '
        + 'Beim Planen zieht der Planer automatisch das ganze Set, sobald eines '
        + 'seiner Medien an der Reihe ist — bis zur Bild-Obergrenze des Kanals.'),
      h('div', { class: 'row', style: { marginTop: '12px' } },
        h('button', { class: 'primary', onClick: save }, 'Set anlegen'),
        h('div', { class: 'spacer' }),
        h('button', { onClick: close }, 'Abbrechen'),
      ),
    )
  }, { wide: true })
}

/** Bestehendes Set ansehen und bearbeiten. */
export function openSetDialog(mediaSet, { channels = [], onDone = () => {} } = {}) {
  modal(mediaSet.name, (close) => {
    const name = h('input', { value: mediaSet.name })
    const stars = h('input', {type:'number',min:1,max:25000,step:1,value:mediaSet.telegram_price_stars || '',placeholder:'Kein Pay-Angebot'})
    const strip = h('div', { class: 'setstrip' })
    const info = h('div', { class: 'hint' })
    let order = []

    const persist = guard(async () => {
      if (order.length < 2) {toast.error('Ein Set benötigt mindestens zwei Medien.'); return}
      if (!stars.checkValidity() || (stars.value && order.length > 10)) {toast.error('Pay-Set: 1–25000 Sterne und höchstens 10 Medien.'); return}
      const updated = await api.updateSet(mediaSet.id, {
        telegram_price_stars: stars.value ? Number(stars.value) : null,
        name: name.value.trim() || mediaSet.name,
        asset_ids: order.map((a) => a.id),
      })
      Object.assign(mediaSet, updated)
      toast.ok('Set gespeichert')
      await onDone()
    })

    const drawStrip = () => {
      clear(strip)
      order.forEach((asset, index) => {
        const tile = h('div', { class: 'setitem', draggable: true, title: asset.filename },
          h('img', { src: asset.thumb_url, alt: '' }),
          h('span', { class: 'setpos' }, String(index + 1)),
          h('button', {
            class: 'setdrop', title: 'Aus dem Set nehmen',
            onClick: (e) => { e.stopPropagation(); order = order.filter((a) => a.id !== asset.id); drawStrip() },
          }, '✕'),
        )
        attachPreview(tile, asset.url, { size: 300 })
        tile.addEventListener('dragstart', (e) => e.dataTransfer.setData('text/plain', asset.id))
        tile.addEventListener('dragover', (e) => e.preventDefault())
        tile.addEventListener('drop', (e) => {
          e.preventDefault()
          const from = order.findIndex((a) => a.id === e.dataTransfer.getData('text/plain'))
          const to = order.findIndex((a) => a.id === asset.id)
          if (from < 0 || to < 0 || from === to) return
          order.splice(to, 0, order.splice(from, 1)[0])
          drawStrip()
        })
        strip.appendChild(tile)
      })
      info.textContent = order.length < 2
        ? 'Weniger als zwei Medien – das Set wirkt wie ein einzelnes Bild.'
        : `${order.length} Medien · ${order.length > 10 ? 'Für Telegram Pay zu groß (maximal 10).' : 'als vollständiges Set verkäuflich'}`
    }

    // Medien des Sets laden. Die Set-Antwort enthält nur IDs.
    api.searchMedia({ set_id: mediaSet.id, limit: 100, include_archived: true })
      .then((res) => {
        const byId = new Map(res.items.map((a) => [a.id, a]))
        order = mediaSet.asset_ids.map((id) => byId.get(id)).filter(Boolean)
        drawStrip()
      })
      .catch((err) => { info.textContent = err.message })

    const channelSelect = h('select', { style: { width: '200px' } },
      h('option', { value: '' }, 'Ganzes Set zuordnen zu …'),
      ...channels.map((c) => h('option', { value: c.id }, c.display_name)))
    channelSelect.addEventListener('change', guard(async () => {
      if (!channelSelect.value) return
      const r = await api.assign({ set_ids: [mediaSet.id], channel_ids: [channelSelect.value] })
      toast.ok(`${r.created} Medien zugeordnet, ${r.skipped} bereits vorhanden`)
      if (r.rejected.length) toast.error(r.rejected[0].reason)
      channelSelect.value = ''
      await onDone()
    }))

    return h('div', { class: 'col' },
      field('Name', name),
      field('Telegram-Setpreis in Sternen', stars, 'Gesamtpreis, unabhängig von Einzelpreisen. Pay-Sets enthalten 2–10 Medien und werden vollständig verkauft.'),
      h('label', { class: 'lbl' }, 'Reihenfolge im Post'),
      strip,
      info,
      h('div', { class: 'row', style: { marginTop: '12px' } },
        h('button', { class: 'primary', onClick: persist }, 'Speichern'),
        channelSelect,
        h('div', { class: 'spacer' }),
        h('button', { class: 'small danger', onClick: guard(async () => {
          if (!(await confirmDialog('Set auflösen',
            'Das Set wird gelöscht. Die Medien selbst bleiben erhalten und '
            + 'werden danach wieder einzeln gepostet.',
            { okLabel: 'Auflösen', danger: true }))) return
          await api.deleteSet(mediaSet.id)
          toast.ok('Set aufgelöst')
          close()
          await onDone()
        }) }, 'Set auflösen'),
      ),
    )
  }, { wide: true })
}
