// DOM-Helfer, Overlays und die gemeinsame visuelle Sprache.
// Bewusst klein gehalten – das ersetzt React, nicht mehr.

export function h(tag, props, ...children) {
  const el = document.createElement(tag)
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue
    if (k === 'class') el.className = v
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v)
    else if (k === 'html') el.innerHTML = v
    else if (k === 'dataset') Object.assign(el.dataset, v)
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v)
    else if (k in el && k !== 'list' && k !== 'form') el[k] = v
    else el.setAttribute(k, v)
  }
  append(el, children)
  return el
}

export function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === true) continue
    parent.appendChild(child instanceof Node ? child : document.createTextNode(String(child)))
  }
  return parent
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild)
  return el
}

export const $ = (sel, root = document) => root.querySelector(sel)

// ---------------------------------------------------------------- Formate
export const fmtDate = (v) => (v ? new Date(v).toLocaleDateString('de-DE') : '—')
export const fmtDateTime = (v) =>
  v ? new Date(v).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'
export const fmtTime = (v) =>
  v ? new Date(v).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }) : '—'
export const fmtBytes = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.round(n / 1024) + ' KB')

/** Wert für ein datetime-local-Feld (lokale Zeit, nicht UTC). */
export function toLocalInput(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// ---------------------------------------------------------------- Lebenszyklus
export const LIFECYCLE = {
  new:            { label: 'Nicht zugeordnet',      color: '#64748b' },
  assigned:       { label: 'Zugeordnet',            color: '#0ea5e9' },
  scheduled:      { label: 'Eingeplant',            color: '#f59e0b' },
  partially_used: { label: 'Teilweise verbraucht',  color: '#84cc16' },
  fully_used:     { label: 'Vollständig verbraucht', color: '#059669' },
  archived:       { label: 'Archiviert',            color: '#3f3f46' },
}
export const LIFECYCLE_ORDER = ['new', 'assigned', 'scheduled', 'partially_used', 'fully_used', 'archived']

export const NSFW_RANK = { sfw: 0, suggestive: 1, explicit: 2 }

// ---------------------------------------------------------------- Bausteine
export const spinner = () => h('span', { class: 'spin' })
export const empty = (text) => h('div', { class: 'empty' }, text)

export function card(title, ...children) {
  return h('section', { class: 'card pad' }, title ? h('h2', {}, title) : null, ...children)
}

export function field(label, control, hint) {
  return h('div', { class: 'field' },
    h('label', { class: 'lbl' }, label),
    control,
    hint ? h('div', { class: 'hint' }, hint) : null,
  )
}

export function input(props = {}) { return h('input', props) }

export function select(props = {}, options = [], value) {
  const el = h('select', props)
  for (const opt of options) {
    el.appendChild(h('option', { value: opt.value, selected: String(opt.value) === String(value) }, opt.label))
  }
  return el
}

export function button(label, props = {}) {
  return h('button', props, props.icon ? h('span', {}, props.icon) : null, label)
}

// ---------------------------------------------------------------- Toasts
export const toast = {
  ok: (m) => show('ok', m),
  error: (m) => show('error', m),
  info: (m) => show('info', m),
}

function show(kind, message) {
  const host = document.getElementById('toasts')
  if (!host) return
  const el = h('div', { class: 'toast ' + kind }, message)
  host.appendChild(el)
  setTimeout(() => el.remove(), kind === 'error' ? 9000 : 5000)
}

// ---------------------------------------------------------------- Modal
export function modal(title, bodyBuilder, { wide = false } = {}) {
  const host = document.getElementById('modals')
  const close = () => {
    bg.remove()
    document.removeEventListener('keydown', onKey)
  }
  // Escape schließt nur den obersten Dialog. Liegt eine Rückfrage über einer
  // Maske, soll sie nicht beide auf einmal wegräumen.
  const onKey = (e) => {
    if (e.key !== 'Escape') return
    if (host.lastElementChild !== bg) return
    close()
  }
  const body = h('div', { class: 'modal-body' })
  const box = h('div', { class: 'modal' + (wide ? ' wide' : '') },
    h('div', { class: 'modal-head' },
      h('h3', {}, title),
      h('button', {
        class: 'ghost small', title: 'Schließen (Escape)', onClick: close,
      }, '✕'),
    ),
    body,
  )
  // Ein Klick daneben schließt NICHT. In den langen Masken (Planer, Kanal-Regeln)
  // wäre sonst mit einem Fehlklick alles Eingetippte weg. Stattdessen ein kurzer
  // Wink, damit klar ist: Der Klick kam an, zum Schließen dient ✕ oder Escape.
  const bg = h('div', {
    class: 'modal-bg',
    onClick: (e) => {
      if (e.target !== bg) return
      box.classList.remove('nudge')
      void box.offsetWidth  // Neustart der Animation erzwingen
      box.classList.add('nudge')
    },
  }, box)
  host.appendChild(bg)
  document.addEventListener('keydown', onKey)
  append(body, [bodyBuilder(close)])
  return { close, body }
}

export function confirmDialog(title, message, { okLabel = 'OK', danger = false } = {}) {
  return new Promise((resolve) => {
    modal(title, (close) =>
      h('div', { class: 'col' },
        h('p', {}, message),
        h('div', { class: 'row' },
          h('button', { class: danger ? 'danger' : 'primary', onClick: () => { close(); resolve(true) } }, okLabel),
          h('button', { onClick: () => { close(); resolve(false) } }, 'Abbrechen'),
        ),
      ),
    )
  })
}

// ---------------------------------------------------------------- Vorschau
let previewEl = null

function previewNode() {
  if (!previewEl) {
    previewEl = h('div', { class: 'hoverpreview' }, h('img', { alt: '' }))
    document.body.appendChild(previewEl)
  }
  return previewEl
}

function hidePreview() {
  if (previewEl) previewEl.classList.remove('on')
}

/**
 * Zeigt beim Überfahren eine große Vorschau neben dem Zeiger.
 * Die Verzögerung verhindert, dass beim bloßen Überstreichen einer Liste
 * ständig Bilder aufpoppen.
 */
export function attachPreview(el, src, { delay = 260, size = 340 } = {}) {
  if (!src) return el
  let timer = null

  const place = (event) => {
    const node = previewNode()
    const margin = 14
    // Standard: rechts unterhalb des Zeigers. Passt es dort nicht, wird
    // gespiegelt bzw. an den Rand geklemmt, damit nichts abgeschnitten wird.
    let x = event.clientX + margin
    let y = event.clientY + margin
    if (x + size > window.innerWidth - 8) x = event.clientX - size - margin
    if (y + size > window.innerHeight - 8) y = Math.max(8, window.innerHeight - size - 8)
    node.style.left = Math.max(8, x) + 'px'
    node.style.top = Math.max(8, y) + 'px'
  }

  el.addEventListener('mouseenter', (event) => {
    timer = setTimeout(() => {
      const node = previewNode()
      node.firstChild.src = src
      node.style.width = size + 'px'
      place(event)
      node.classList.add('on')
    }, delay)
  })
  el.addEventListener('mousemove', (event) => {
    if (previewEl && previewEl.classList.contains('on')) place(event)
  })
  el.addEventListener('mouseleave', () => {
    clearTimeout(timer)
    hidePreview()
  })
  el.addEventListener('click', () => {
    clearTimeout(timer)
    hidePreview()
  })
  // Beim Ziehen würde die Vorschau sonst stehen bleiben und die Ablagefläche verdecken.
  el.addEventListener('dragstart', () => {
    clearTimeout(timer)
    hidePreview()
  })
  window.addEventListener('scroll', hidePreview, { passive: true })
  return el
}


// ---------------------------------------------------------------- Bildkachel
/**
 * Kanal-Chips: gefüllt = veröffentlicht, umrandet = zugeordnet oder eingeplant.
 * Damit ist "läuft schon auf X, für Fanvue noch offen" ohne Klick lesbar.
 */
export function channelChips(asset, channels, max = 4) {
  const relevant = channels.filter((c) =>
    asset.assigned_channel_ids.includes(c.id) ||
    asset.used_channel_ids.includes(c.id) ||
    asset.scheduled_channel_ids.includes(c.id),
  )
  const box = h('div', { class: 'chips' })
  relevant.slice(0, max).forEach((c) => {
    const published = asset.used_channel_ids.includes(c.id)
    const scheduled = asset.scheduled_channel_ids.includes(c.id)
    box.appendChild(h('span', {
      class: 'cchip',
      title: `${c.display_name || c.name} — ${published ? 'veröffentlicht' : scheduled ? 'eingeplant' : 'zugeordnet'}`,
      style: {
        borderColor: c.color,
        background: published ? c.color : 'transparent',
        opacity: scheduled && !published ? '0.7' : '1',
      },
    }))
  })
  if (relevant.length > max) {
    box.appendChild(h('span', { class: 'hint' }, '+' + (relevant.length - max)))
  }
  return box
}

export function mediaTile(asset, channels, opts = {}) {
  const meta = LIFECYCLE[asset.lifecycle] || LIFECYCLE.new
  const consumed = asset.lifecycle === 'fully_used' || asset.lifecycle === 'archived'
  const el = h('div', {
    class: `tile lc-${asset.lifecycle}${consumed ? ' consumed' : ''}${opts.selected ? ' selected' : ''}`,
    title: `${asset.filename} — ${meta.label}`,
    draggable: !!opts.draggable,
    onClick: opts.onClick,
    ondragstart: opts.onDragStart,
  },
    h('img', { src: asset.thumb_url, alt: asset.caption_hint || asset.filename, loading: 'lazy' }),
    channelChips(asset, channels),
    // Gehört das Bild zu einem Set, muss man das auf einen Blick sehen –
    // sonst wundert man sich, warum es nie allein gepostet wird.
    (asset.set_names || []).length
      ? h('span', {
          class: 'setbadge',
          title: 'Set: ' + asset.set_names.join(', ') + ' – wird gemeinsam gepostet',
        }, '▣ ' + asset.set_names[0])
      : null,
    asset.usage_count > 0 ? h('span', { class: 'count' }, asset.usage_count + '×') : null,
    h('span', { class: 'cap' },
      h('i', { class: 'dot', style: { background: meta.color } }),
      asset.filename,
    ),
  )
  if (opts.onDragStart) el.addEventListener('dragstart', opts.onDragStart)
  // Beim Verweilen das Bild groß zeigen – in dichten Rastern der schnellste Weg,
  // ein Motiv zu erkennen, ohne es anzuklicken.
  if (opts.preview !== false) attachPreview(el, asset.url, { delay: 400, size: 320 })
  return el
}

// ---------------------------------------------------------------- Sonstiges
export const TRAFFIC_LABEL = { green: 'reichlich', amber: 'wird knapp', red: 'kritisch', grey: 'unbekannt' }

export function debounce(fn, ms = 300) {
  let timer
  return (...args) => {
    clearTimeout(timer)
    timer = setTimeout(() => fn(...args), ms)
  }
}

/** Fehler einheitlich anzeigen, ohne die Seite zu zerstören. */
export function guard(fn) {
  return async (...args) => {
    window.CreatorStudioLive?.begin()
    try {
      const result = await fn(...args)
      return result
    } catch (err) { toast.error(err.message || String(err)) }
    finally { window.CreatorStudioLive?.end() }
  }
}
