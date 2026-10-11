// Wasserzeichen eines Kanals: hochladen, platzieren, in echt ansehen.
import { api } from './api.js?v=creatorstudio-mobile-system-20260909'
import { h, append, clear, field, toast, guard, spinner, confirmDialog, debounce } from './ui.js?v=creatorstudio-mobile-system-20260909'

// Reihenfolge des 3×3-Rasters, so wie es auf dem Bildschirm liegt.
const ANCHORS = [
  ['top-left', 'oben links'], ['top-center', 'oben mittig'], ['top-right', 'oben rechts'],
  ['middle-left', 'mittig links'], ['center', 'Bildmitte'], ['middle-right', 'mittig rechts'],
  ['bottom-left', 'unten links'], ['bottom-center', 'unten mittig'], ['bottom-right', 'unten rechts'],
]

/**
 * Alle Maße sind relativ zum Bild: Größe in Prozent der Breite, Abstände in
 * Prozent der kürzeren Seite. Nur so sieht das Zeichen auf Hoch- und
 * Querformat gleich aus — Pixelwerte täten das nicht.
 */
export function watermarkEditor(channel, { onChange = () => {} } = {}) {
  const state = {
    anchor: channel.watermark_anchor || 'bottom-left',
    scale_pct: channel.watermark_scale_pct ?? 18,
    margin_x_pct: channel.watermark_margin_x_pct ?? 3,
    margin_y_pct: channel.watermark_margin_y_pct ?? 3,
    opacity: channel.watermark_opacity ?? 1,
    enabled: !!channel.watermark_enabled,
    has: !!channel.has_watermark,
  }

  const previewBox = h('div', { class: 'wmpreview' })
  const grid = h('div', { class: 'anchorgrid' })
  const controls = h('div', {})
  const fileInput = h('input', { type: 'file', accept: 'image/png', style: { display: 'none' } })
  const status = h('div', { class: 'hint' })

  const refreshPreview = debounce(() => {
    clear(previewBox)
    if (!state.has) {
      previewBox.appendChild(h('div', { class: 'empty' },
        'Noch kein Wasserzeichen. PNG mit freigestelltem Hintergrund hochladen.'))
      return
    }
    const img = h('img', { alt: 'Vorschau mit Wasserzeichen' })
    img.addEventListener('error', () => {
      clear(previewBox).appendChild(h('div', { class: 'hint' },
        'Für die Vorschau fehlt noch ein Bild in der Bibliothek.'))
    })
    img.src = api.watermarkPreviewUrl(channel.id, state)
    previewBox.appendChild(img)
  }, 250)

  const drawGrid = () => {
    clear(grid)
    for (const [value, label] of ANCHORS) {
      grid.appendChild(h('button', {
        class: 'anchorcell' + (state.anchor === value ? ' on' : ''),
        title: label,
        onClick: () => { state.anchor = value; drawGrid(); refreshPreview(); onChange(values()) },
      }, h('i')))
    }
  }

  const slider = (key, label, min, max, step, suffix) => {
    const input = h('input', {
      type: 'range', min: String(min), max: String(max), step: String(step),
      value: String(state[key]),
    })
    const out = h('span', { class: 'num hint', style: { minWidth: '48px', textAlign: 'right' } },
      state[key] + suffix)
    input.addEventListener('input', () => {
      state[key] = Number(input.value)
      out.textContent = state[key] + suffix
      refreshPreview()
      onChange(values())
    })
    return h('div', { class: 'row', style: { gap: '10px' } },
      h('span', { class: 'hint', style: { width: '150px' } }, label),
      h('div', { style: { flex: '1' } }, input),
      out)
  }

  const values = () => ({
    watermark_enabled: state.enabled,
    watermark_anchor: state.anchor,
    watermark_scale_pct: state.scale_pct,
    watermark_margin_x_pct: state.margin_x_pct,
    watermark_margin_y_pct: state.margin_y_pct,
    watermark_opacity: state.opacity,
  })

  const enabled = h('input', { type: 'checkbox', checked: state.enabled })
  enabled.addEventListener('change', () => { state.enabled = enabled.checked; onChange(values()) })

  const uploadBtn = h('button', {}, state.has ? 'PNG austauschen' : 'PNG hochladen')
  uploadBtn.addEventListener('click', () => fileInput.click())
  fileInput.addEventListener('change', guard(async () => {
    const file = fileInput.files && fileInput.files[0]
    if (!file) return
    clear(uploadBtn).appendChild(spinner())
    try {
      const updated = await api.uploadWatermark(channel.id, file)
      Object.assign(channel, updated)
      state.has = true
      state.enabled = true
      enabled.checked = true
      toast.ok('Wasserzeichen hinterlegt')
      drawControls()
      refreshPreview()
      onChange(values())
    } finally {
      clear(uploadBtn).appendChild(document.createTextNode(state.has ? 'PNG austauschen' : 'PNG hochladen'))
      fileInput.value = ''
    }
  }))

  const removeBtn = h('button', { class: 'small danger' }, 'Entfernen')
  removeBtn.addEventListener('click', guard(async () => {
    if (!(await confirmDialog('Wasserzeichen entfernen',
      'Künftige Posts dieses Kanals gehen dann ohne Wasserzeichen raus.',
      { okLabel: 'Entfernen', danger: true }))) return
    const updated = await api.deleteWatermark(channel.id)
    Object.assign(channel, updated)
    state.has = false
    state.enabled = false
    enabled.checked = false
    toast.ok('Entfernt')
    drawControls()
    refreshPreview()
  }))

  function drawControls() {
    clear(controls)
    append(controls, [
      h('label', { class: 'inline', style: { marginBottom: '10px' } }, enabled,
        'Wasserzeichen auf Bilder dieses Kanals legen'),
      h('label', { class: 'lbl' }, 'Position'),
      grid,
      h('div', { class: 'col', style: { gap: '6px', marginTop: '12px' } },
        slider('scale_pct', 'Größe (% der Bildbreite)', 3, 60, 1, ' %'),
        slider('margin_x_pct', 'Abstand seitlich', 0, 25, 0.5, ' %'),
        slider('margin_y_pct', 'Abstand oben/unten', 0, 25, 0.5, ' %'),
        slider('opacity', 'Deckkraft', 0.1, 1, 0.05, ''),
      ),
      h('div', { class: 'row', style: { marginTop: '12px' } },
        uploadBtn, fileInput,
        state.has ? removeBtn : null,
        h('div', { class: 'spacer' }),
        state.has ? h('img', {
          src: `/api/v1/channels/${channel.id}/watermark?t=${Date.now()}`,
          class: 'wmchip', alt: 'Das hinterlegte Wasserzeichen',
          title: 'Das hinterlegte PNG',
        }) : null,
      ),
      status,
    ])
    status.textContent = state.has
      ? 'Das Zeichen wird erst beim Veröffentlichen aufs Bild gerechnet – die Datei in der Bibliothek bleibt unverändert.'
      : 'Erwartet wird ein PNG mit Transparenz. Ohne Transparenz entstünde ein weißer Kasten.'
  }

  drawGrid()
  drawControls()
  refreshPreview()

  const node = h('div', { class: 'grid c2' }, controls, previewBox)
  return { node, values }
}
