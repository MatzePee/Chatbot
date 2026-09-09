// Beispielposts einer Persona: Stilvorlagen, die in jeden Prompt einfließen.
import { api } from './api.js?v=creatorstudio-mobile-system-20260909'
import { h, append, clear, empty, toast, guard, spinner, confirmDialog, fmtDate } from './ui.js?v=creatorstudio-mobile-system-20260909'

const PLATFORMS = [['x', 'X'], ['fanvue', 'Fanvue']]

/**
 * Liste plus Eingabefeld für mehrere Beispiele auf einmal.
 * Getrennt nach Plattform und danach, ob ein Bild dabei ist – eine Frage an
 * die Community klingt anders als eine Bildunterschrift.
 */
export function examplesEditor(persona) {
  const state = { platform: 'x', kind: 'text', items: [] }

  const listBox = h('div', { class: 'col', style: { gap: '4px' } })
  const countLine = h('div', { class: 'hint' })
  const area = h('textarea', {
    style: { height: '150px', fontSize: '12px' },
    placeholder:
      'Ein Beispiel pro Zeile. Nummerierungen wie "1." werden automatisch entfernt.\n'
      + 'Coffee or tea? And no, \'both\' is not an answer. ☕🍵',
  })

  const kindTabs = h('div', { class: 'row' })
  const platTabs = h('div', { class: 'row' })

  const visible = () => state.items.filter((item) => {
    const hasImage = !!(item.image_description || '').trim()
    return item.platform === state.platform && hasImage === (state.kind === 'image')
  })

  function drawTabs() {
    clear(platTabs)
    platTabs.appendChild(h('span', { class: 'hint' }, 'Plattform:'))
    for (const [value, label] of PLATFORMS) {
      platTabs.appendChild(h('button', {
        class: 'chip' + (state.platform === value ? ' on' : ''),
        onClick: () => { state.platform = value; drawTabs(); drawList() },
      }, label))
    }

    clear(kindTabs)
    kindTabs.appendChild(h('span', { class: 'hint' }, 'Gilt für:'))
    for (const [value, label] of [['text', '¶ Posts ohne Bild'], ['image', '▣ Posts mit Bild']]) {
      kindTabs.appendChild(h('button', {
        class: 'chip' + (state.kind === value ? ' on' : ''),
        onClick: () => { state.kind = value; drawTabs(); drawList() },
      }, label))
    }
  }

  function drawList() {
    clear(listBox)
    const items = visible()
    countLine.textContent = items.length
      ? `${items.length} Beispiele — pro Post werden bis zu 8 davon zufällig als Vorlage mitgegeben.`
      : 'Noch keine Beispiele. Ohne Vorlage schreibt das Modell, was es für typisch hält.'
    if (!items.length) return

    for (const item of items) {
      listBox.appendChild(h('div', {
        class: 'row',
        style: {
          gap: '8px', alignItems: 'flex-start', padding: '5px 8px',
          border: '1px solid var(--line)', borderRadius: '7px', fontSize: '12px',
        },
      },
        h('span', { style: { flex: '1', minWidth: '0' } }, item.text),
        h('span', { class: 'hint', style: { whiteSpace: 'nowrap' } }, fmtDate(item.created_at)),
        h('button', {
          class: 'small ghost',
          title: 'Beispiel entfernen',
          onClick: guard(async () => {
            await api.deleteExample(item.id)
            state.items = state.items.filter((x) => x.id !== item.id)
            drawList()
          }),
        }, '✕'),
      ))
    }
  }

  const addBtn = h('button', { class: 'primary' }, 'Beispiele hinzufügen')
  addBtn.addEventListener('click', guard(async () => {
    if (!area.value.trim()) { toast.error('Nichts einzufügen'); return }
    clear(addBtn).appendChild(spinner()); addBtn.disabled = true
    try {
      const created = await api.addExamples(persona.id, {
        text: area.value,
        platform: state.platform,
        // Nicht leer, damit das Beispiel als "für Bildposts" erkannt wird.
        image_description: state.kind === 'image' ? 'Bildpost' : '',
        rating: 1,
      })
      state.items = created.concat(state.items)
      area.value = ''
      drawList()
      toast.ok(`${created.length} Beispiele übernommen`)
    } finally {
      clear(addBtn).appendChild(document.createTextNode('Beispiele hinzufügen'))
      addBtn.disabled = false
    }
  }))

  const clearBtn = h('button', { class: 'small danger' }, 'Alle dieser Art löschen')
  clearBtn.addEventListener('click', guard(async () => {
    const items = visible()
    if (!items.length) return
    if (!(await confirmDialog('Beispiele löschen', `${items.length} Beispiele wirklich löschen?`,
      { okLabel: 'Löschen', danger: true }))) return
    for (const item of items) await api.deleteExample(item.id)
    state.items = state.items.filter((x) => !items.includes(x))
    drawList()
    toast.ok('Gelöscht')
  }))

  const node = h('div', { class: 'stack' },
    h('div', { class: 'row', style: { gap: '18px', flexWrap: 'wrap' } }, platTabs, kindTabs),
    countLine,
    listBox,
    area,
    h('div', { class: 'row' }, addBtn, h('div', { class: 'spacer' }), clearBtn),
  )

  drawTabs()
  api.examples(persona.id)
    .then((items) => { state.items = items; drawList() })
    .catch((err) => { countLine.textContent = err.message })

  return { node }
}
