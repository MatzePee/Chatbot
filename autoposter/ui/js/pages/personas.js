import { api } from '../api.js?v=creatorstudio-mobile-system-20260909'
import { h, clear, empty, field, card, toast, guard, spinner } from '../ui.js?v=creatorstudio-mobile-system-20260909'
import { rhythmEditor } from '../rhythm.js?v=creatorstudio-mobile-system-20260909'
import { examplesEditor } from '../examples.js?v=creatorstudio-mobile-system-20260909'

export default async function renderPersonas() {
  let [personas, channels] = await Promise.all([api.personas(), api.channels()])
  const state = { active: personas[0] || null }

  const listBox = h('aside', { class: 'persona-list' })
  const detailBox = h('div', { class: 'persona-editor' })

  function drawList() {
    clear(listBox)
    listBox.appendChild(h('button', {
      class: 'primary', style: { width: '100%', justifyContent: 'center', marginBottom: '8px' },
      onClick: guard(async () => {
        const name = prompt('Name der Persona?')
        if (!name) return
        const p = await api.createPersona({ name, language: 'de' })
        personas.push(p); state.active = p; drawList(); drawDetail()
      }),
    }, '+ Neue Persona'))

    if (!personas.length) { listBox.appendChild(empty('Keine Personas.')); return }
    for (const p of personas) {
      listBox.appendChild(h('button', {
        style: {
          width: '100%', justifyContent: 'flex-start', marginBottom: '3px',
          background: state.active && state.active.id === p.id ? 'var(--bg-2)' : 'transparent',
          borderColor: state.active && state.active.id === p.id ? 'var(--line)' : 'transparent',
        },
        onClick: () => { state.active = p; drawList(); drawDetail() },
      }, h('span', {}, p.name, h('div', { class: 'hint' }, p.language))))
    }
  }

  function drawDetail() {
    clear(detailBox)
    const p = state.active
    if (!p) { detailBox.appendChild(empty('Keine Persona ausgewählt.')); return }

    const name = h('input', { value: p.name })
    const bio = h('textarea', {}); bio.value = p.bio || ''
    const tone = h('textarea', {}); tone.value = p.tone_guidelines || ''
    const sys = h('textarea', { style: { height: '190px', fontFamily: 'ui-monospace, monospace', fontSize: '12px' } })
    sys.value = p.system_prompt || ''
    const lang = h('input', { value: p.language })
    const emoji = h('select', {}, ...[['none', 'keine'], ['sparse', 'sparsam'], ['liberal', 'großzügig']]
      .map(([v, l]) => h('option', { value: v, selected: v === p.emoji_policy }, l)))
    const temp = h('input', { type: 'number', step: '0.1', value: p.temperature })
    const model = h('input', { value: p.model_override || '', placeholder: 'z. B. anthropic/claude-sonnet-4' })
    const forbidden = h('input', { value: (p.forbidden_topics || []).join(', ') })
    const hashtags = h('input', { value: (p.hashtag_pool || []).join(', ') })
    const ctas = h('input', { value: (p.cta_pool || []).join(' | ') })
    const rhythm = rhythmEditor({ value: p.daily_rhythm || '' })
    const examples = examplesEditor(p)

    const save = guard(async () => {
      const updated = await api.updatePersona(p.id, {
        name: name.value,
        bio: bio.value,
        tone_guidelines: tone.value,
        system_prompt: sys.value,
        language: lang.value,
        emoji_policy: emoji.value,
        temperature: Number(temp.value),
        model_override: model.value || null,
        forbidden_topics: forbidden.value.split(',').map((t) => t.trim()).filter(Boolean),
        hashtag_pool: hashtags.value.split(',').map((t) => t.trim()).filter(Boolean),
        cta_pool: ctas.value.split('|').map((t) => t.trim()).filter(Boolean),
        daily_rhythm: rhythm.value,
      })
      Object.assign(p, updated)
      window.CreatorStudioLive?.saved(detailBox)
      toast.ok('Persona gespeichert')
      drawList()
    })

    // ---- Playground ----
    const chanSel = h('select', { style: { width: '260px' } },
      ...channels.map((c) => h('option', { value: c.id }, `${c.display_name} (${c.platform})`)))
    const out = h('div', { class: 'col' })
    const testBtn = h('button', {}, 'Testen')
    testBtn.addEventListener('click', guard(async () => {
      if (!chanSel.value) { toast.error('Kein Kanal vorhanden'); return }
      clear(testBtn).appendChild(spinner()); testBtn.disabled = true
      try {
        const r = await api.generate({ channel_id: chanSel.value, variants: 3 })
        clear(out)
        for (const v of r.variants) {
          out.appendChild(h('div', { style: { border: '1px solid var(--line)', borderRadius: '8px', padding: '8px 10px' } },
            v.text,
            h('div', { style: { color: '#818cf8', fontSize: '12px', marginTop: '4px' } },
              (v.hashtags || []).map((t) => '#' + t).join(' ')),
            v.issues && v.issues.length
              ? h('div', { style: { color: 'var(--warn)', fontSize: '12px' } }, v.issues.join(', '))
              : null))
        }
      } finally {
        clear(testBtn).appendChild(document.createTextNode('Testen')); testBtn.disabled = false
      }
    }))

    detailBox.appendChild(h('div', { class: 'stack' },
      h('h1', { style: { margin: 0, fontSize: '20px' } }, p.name),
      h('div', { class: 'grid c2' },
        h('div', {},
          field('Name', name),
          field('Kurzbeschreibung', bio),
          field('Tonalität', tone),
          h('div', { class: 'grid c3' },
            field('Sprache', lang),
            field('Emojis', emoji),
            field('Temperatur', temp),
          ),
          field('Modell (leer = Standard)', model),
        ),
        h('div', {},
          field('System-Prompt', sys),
          field('Verbotene Themen (kommagetrennt)', forbidden),
          field('Hashtag-Pool', hashtags),
          field('CTA-Pool (mit | trennen)', ctas),
        ),
      ),
      h('button', { class: 'primary', onClick: save }, 'Speichern'),
      card('Tagesrhythmus',
        h('p', { class: 'hint', style: { marginTop: '-4px' } },
          'Jeder erzeugte Text bekommt die Aktivität mit, die zur geplanten Uhrzeit gehört – ',
          'in der Zeitzone des jeweiligen Kanals. So behauptet kein Post um 03:00, ',
          'die Persona sei gerade im Gym.'),
        rhythm.node,
      ),
      card('Beispielposts',
        h('p', { class: 'hint', style: { marginTop: '-4px' } },
          'Der zuverlässigste Hebel für den Stil. Bis zu 8 Beispiele gehen bei jeder ',
          'Generierung als Vorlage mit – das Modell übernimmt Aufbau, Länge und Tonfall, ',
          'nicht den Inhalt. Getrennt nach Posts mit und ohne Bild, weil eine Frage an die ',
          'Community anders klingt als eine Bildunterschrift.'),
        examples.node,
      ),
      card('Prompt-Playground',
        h('div', { class: 'row', style: { marginBottom: '10px' } }, chanSel, testBtn),
        out,
      ),
    ))
  }

  drawList(); drawDetail()

  const livePage = h('div', { class: 'page persona-layout' }, listBox, detailBox)
  livePage.refresh = async () => {
    const data = await Promise.all([api.personas(), api.channels()])
    if (window.CreatorStudioLive.busy()) return false
    ;[personas, channels] = data
    drawList()
    if (window.CreatorStudioLive.dirty(detailBox)) return false
    const active = personas.find(p => p.id === state.active?.id) || personas[0] || null
    if (JSON.stringify(active) !== JSON.stringify(state.active)) { state.active = active; drawDetail() }
  }
  return livePage
}
