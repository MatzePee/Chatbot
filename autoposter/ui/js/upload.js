// Bilder-Upload per Drag & Drop, mit Fortschritt je Datei und
// anschließender Zuordnung zu Kanälen.
import { api } from './api.js?v=creatorstudio-layout-20260909'
import { h, clear, append, field, modal, toast, guard, spinner, fmtBytes } from './ui.js?v=creatorstudio-layout-20260909'

const ACCEPT = ['image/jpeg', 'image/png', 'image/webp']
//: Wie viele Dateien pro Anfrage. Kleine Bündel halten die Rückmeldung flüssig
//: und die Schreibtransaktionen kurz.
const BATCH_SIZE = 4

function isUsable(file) {
  return ACCEPT.includes(file.type) || /\.(jpe?g|png|webp|zip)$/i.test(file.name)
}

/** Dateien aus einem Drop-Event einsammeln – auch aus Ordnern. */
async function filesFromDataTransfer(dt) {
  const out = []
  const items = dt.items ? Array.from(dt.items) : []

  const walk = async (entry, path = '') => {
    if (!entry) return
    if (entry.isFile) {
      const file = await new Promise((res) => entry.file(res, () => res(null)))
      if (file && isUsable(file)) out.push(file)
    } else if (entry.isDirectory) {
      const reader = entry.createReader()
      // readEntries liefert je Aufruf höchstens 100 Einträge.
      for (;;) {
        const batch = await new Promise((res) => reader.readEntries(res, () => res([])))
        if (!batch.length) break
        for (const child of batch) await walk(child, path + entry.name + '/')
      }
    }
  }

  const entries = items
    .map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null))
    .filter(Boolean)

  if (entries.length) {
    for (const entry of entries) await walk(entry)
  } else {
    for (const file of Array.from(dt.files || [])) if (isUsable(file)) out.push(file)
  }
  return out
}

/**
 * Ablagefläche zum Wiederverwenden.
 * onDone(result) wird nach jedem abgeschlossenen Upload aufgerufen.
 */
export function createDropZone({ channels = [], onDone = () => {}, compact = false } = {}) {
  const state = { files: [], busy: false }

  const fileInput = h('input', {
    type: 'file', multiple: true, style: { display: 'none' },
    accept: 'image/jpeg,image/png,image/webp,.zip',
  })
  const listBox = h('div', { class: 'uploadlist' })
  const summary = h('div', { class: 'hint' })
  const bar = h('div', { class: 'bar', style: { display: 'none' } }, h('i', { style: { width: '0%' } }))

  const tagsInput = h('input', { placeholder: 'z. B. beach, sommer' })
  const nsfwSelect = h('select', {}, ...['sfw', 'suggestive', 'explicit'].map((v) =>
    h('option', { value: v }, v)))
  const channelSelect = h('select', { multiple: true, size: Math.min(5, Math.max(2, channels.length)) },
    ...channels.map((c) => h('option', { value: c.id }, `${c.display_name} (${c.platform})`)))

  const startBtn = h('button', { class: 'primary' }, 'Hochladen')
  const clearBtn = h('button', {}, 'Liste leeren')

  // ------------------------------------------------------------ Liste
  function drawList() {
    clear(listBox)
    if (!state.files.length) {
      listBox.appendChild(h('div', { class: 'hint', style: { padding: '10px 0' } },
        'Noch keine Dateien ausgewählt.'))
    } else {
      for (const entry of state.files) {
        listBox.appendChild(h('div', { class: 'uploadrow', dataset: { state: entry.state } },
          entry.preview
            ? h('img', { src: entry.preview, alt: '' })
            : h('div', { class: 'uploadrow-ph' }, entry.file.name.slice(-4)),
          h('div', { style: { flex: '1', minWidth: '0' } },
            h('div', { class: 'uploadrow-name' }, entry.file.name),
            h('div', { class: 'hint', style: { fontSize: '11px' } },
              fmtBytes(entry.file.size) + (entry.message ? ' · ' + entry.message : '')),
          ),
          h('span', { class: 'uploadrow-state' },
            entry.state === 'wartet' ? '…'
              : entry.state === 'laeuft' ? '⟳'
              : entry.state === 'ok' ? '✓'
              : entry.state === 'duplikat' ? '≡' : '✕'),
          entry.state === 'wartet'
            ? h('button', {
                class: 'small ghost',
                onClick: () => { state.files = state.files.filter((f) => f !== entry); drawList() },
              }, '✕')
            : null,
        ))
      }
    }
    const total = state.files.length
    const done = state.files.filter((f) => f.state !== 'wartet' && f.state !== 'laeuft').length
    summary.textContent = total
      ? `${total} Datei(en)${done ? ` · ${done} verarbeitet` : ''}`
      : ''
    startBtn.disabled = state.busy || !total || !state.files.some((f) => f.state === 'wartet')
  }

  function addFiles(files) {
    const known = new Set(state.files.map((f) => f.file.name + ':' + f.file.size))
    let added = 0
    for (const file of files) {
      const key = file.name + ':' + file.size
      if (known.has(key)) continue
      known.add(key)
      const entry = { file, state: 'wartet', message: '', preview: null }
      state.files.push(entry)
      added++
      // Vorschau nur für echte Bilder und nur für die ersten 60 Einträge,
      // sonst frisst das bei Massen-Uploads den Speicher.
      if (file.type.startsWith('image/') && state.files.length <= 60) {
        const reader = new FileReader()
        reader.onload = () => { entry.preview = reader.result; drawList() }
        reader.readAsDataURL(file)
      }
    }
    if (added) toast.info(`${added} Datei(en) hinzugefügt`)
    drawList()
  }

  // ------------------------------------------------------------ Ablagefläche
  const zone = h('div', { class: 'dropzone' + (compact ? ' compact' : '') },
    h('div', { class: 'dropzone-icon' }, '⇪'),
    h('div', {}, h('b', {}, 'Bilder hierher ziehen'), ' oder ',
      h('button', { class: 'ghost', onClick: () => fileInput.click() }, 'Dateien auswählen')),
    h('div', { class: 'hint' }, 'JPEG, PNG, WebP oder ZIP · ganze Ordner sind möglich'),
    fileInput,
  )

  fileInput.addEventListener('change', () => {
    addFiles(Array.from(fileInput.files || []).filter(isUsable))
    fileInput.value = ''
  })

  let dragDepth = 0
  const over = (e) => { e.preventDefault(); e.stopPropagation() }
  zone.addEventListener('dragenter', (e) => { over(e); dragDepth++; zone.classList.add('hot') })
  zone.addEventListener('dragover', (e) => { over(e); e.dataTransfer.dropEffect = 'copy' })
  zone.addEventListener('dragleave', (e) => {
    over(e); dragDepth = Math.max(0, dragDepth - 1)
    if (!dragDepth) zone.classList.remove('hot')
  })
  zone.addEventListener('drop', guard(async (e) => {
    over(e); dragDepth = 0; zone.classList.remove('hot')
    addFiles(await filesFromDataTransfer(e.dataTransfer))
  }))

  // ------------------------------------------------------------ Hochladen
  const run = guard(async () => {
    const pending = state.files.filter((f) => f.state === 'wartet')
    if (!pending.length) return
    state.busy = true
    bar.style.display = ''
    clear(startBtn).appendChild(spinner())
    append(startBtn, [' Lädt …'])

    const tags = tagsInput.value
    const nsfw = nsfwSelect.value
    const targets = Array.from(channelSelect.selectedOptions).map((o) => o.value)
    let created = 0, duplicates = 0, failed = 0, assigned = 0
    let processed = 0

    for (let i = 0; i < pending.length; i += BATCH_SIZE) {
      const chunk = pending.slice(i, i + BATCH_SIZE)
      chunk.forEach((entry) => { entry.state = 'laeuft' })
      drawList()

      const form = new FormData()
      chunk.forEach((entry) => form.append('files', entry.file))
      form.append('tags', tags)
      form.append('nsfw_level', nsfw)
      form.append('assign_channel_ids', targets.join(','))

      try {
        const res = await api.upload(form)
        const dupNames = new Set((res.duplicates || []).map((d) => d.filename))
        const errMap = new Map((res.errors || []).map((e) => [e.filename, e.message]))

        for (const entry of chunk) {
          const short = entry.file.name.split('/').pop()
          if (errMap.has(short)) {
            entry.state = 'fehler'; entry.message = errMap.get(short); failed++
          } else if (dupNames.has(short)) {
            entry.state = 'duplikat'; entry.message = 'bereits im Bestand'; duplicates++
          } else {
            entry.state = 'ok'; entry.message = ''; created++
          }
        }
        // ZIP-Archive liefern mehr Ergebnisse als Eingabedateien.
        if (res.summary) {
          created = Math.max(created, created)
          assigned += res.summary.assigned || 0
        }
        if ((res.rejected || []).length) toast.error(res.rejected[0].reason)
      } catch (err) {
        for (const entry of chunk) { entry.state = 'fehler'; entry.message = err.message; failed++ }
        toast.error(err.message)
      }

      processed += chunk.length
      bar.firstChild.style.width = Math.round((processed / pending.length) * 100) + '%'
      drawList()
    }

    state.busy = false
    clear(startBtn).appendChild(document.createTextNode('Hochladen'))
    drawList()

    const parts = [`${created} hochgeladen`]
    if (assigned) parts.push(`${assigned} zugeordnet`)
    if (duplicates) parts.push(`${duplicates} Duplikate übersprungen`)
    if (failed) parts.push(`${failed} Fehler`)
    failed ? toast.error(parts.join(' · ')) : toast.ok(parts.join(' · '))

    onDone({ created, duplicates, failed, assigned })
  })

  startBtn.addEventListener('click', run)
  clearBtn.addEventListener('click', () => {
    state.files = state.files.filter((f) => f.state === 'laeuft')
    drawList()
  })

  drawList()

  const options = h('div', { class: 'grid c3' },
    field('Tags (kommagetrennt, optional)', tagsInput),
    field('NSFW-Level', nsfwSelect),
    field('Direkt Kanälen zuordnen (optional)', channelSelect,
      'Mehrfachauswahl mit Strg/Cmd. Lässt sich später jederzeit unter „Zuordnung" ändern.'),
  )

  const root = h('div', { class: 'col' },
    zone,
    options,
    h('div', { class: 'row' }, startBtn, clearBtn, summary),
    bar,
    listBox,
  )

  return { root, addFiles, zone }
}

/** Upload als Dialog. */
export function openUploadDialog({ channels = [], files = [], onDone = () => {} } = {}) {
  const { close } = modal('Bilder hochladen', () => {
    const dz = createDropZone({ channels, onDone })
    // Bereits gezogene Dateien direkt übernehmen.
    if (files && files.length) dz.addFiles(files.filter(isUsable))
    return dz.root
  }, { wide: true })
  return close
}
