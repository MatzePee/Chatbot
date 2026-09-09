// Fortschritt eines Hintergrundlaufs: Balken, Schritttext, Restzeit, Abbruch.
import { api } from './api.js?v=creatorstudio-layout-20260909'
import { h, append, clear, toast } from './ui.js?v=creatorstudio-layout-20260909'

const seconds = (value) => {
  if (value === null || value === undefined) return ''
  if (value < 60) return `noch ca. ${value} s`
  const min = Math.round(value / 60)
  return `noch ca. ${min} ${min === 1 ? 'Minute' : 'Minuten'}`
}

/**
 * Zeigt den Lauf an und fragt ihn ab, bis er endet.
 *
 * Gibt ein Promise auf den fertigen Lauf zurück – abgebrochen und
 * fehlgeschlagen eingeschlossen, damit der Aufrufer aufräumen kann.
 */
export function runProgress(runId, { label = '', onDone = () => {} } = {}) {
  const bar = h('i')
  const track = h('div', { class: 'progtrack' }, bar)
  const percent = h('span', { class: 'num', style: { fontWeight: '600' } }, '0 %')
  const counter = h('span', { class: 'hint num' })
  const left = h('span', { class: 'hint' })
  const message = h('div', { class: 'hint', style: { minHeight: '17px' } }, 'Startet …')
  const notes = h('div', {
    class: 'hint',
    style: { maxHeight: '90px', overflowY: 'auto', color: 'var(--warn)' },
  })

  const cancelBtn = h('button', { class: 'small' }, 'Abbrechen')
  cancelBtn.addEventListener('click', async () => {
    cancelBtn.disabled = true
    try {
      await api.cancelRun(runId)
      message.textContent = 'Abbruch angefordert – der laufende Schritt wird noch beendet.'
    } catch (err) {
      toast.error(err.message)
      cancelBtn.disabled = false
    }
  })

  const node = h('div', { class: 'progbox' },
    h('div', { class: 'row' },
      h('b', { style: { flex: '1' } }, label || 'Läuft …'),
      percent, cancelBtn,
    ),
    track,
    h('div', { class: 'row' }, counter, h('div', { class: 'spacer' }), left),
    message,
    notes,
  )

  const finished = new Promise((resolve) => {
    let misses = 0
    const tick = async () => {
      let state
      try {
        state = await api.run(runId)
        misses = 0
      } catch (err) {
        // Ein einzelner Aussetzer soll die Anzeige nicht abwürgen.
        if (++misses < 5) { setTimeout(tick, 1500); return }
        message.textContent = 'Der Lauf ist nicht mehr abfragbar: ' + err.message
        resolve({ status: 'error', error: err.message })
        return
      }

      bar.style.width = state.percent + '%'
      percent.textContent = state.percent + ' %'
      counter.textContent = `${state.done} / ${state.total}`
      left.textContent = state.status === 'running' ? seconds(state.seconds_left) : ''
      if (state.message) message.textContent = state.message
      if (state.notes && state.notes.length) {
        clear(notes)
        append(notes, state.notes.slice(-5).map((n) => h('div', {}, n)))
      }

      if (state.status === 'running') { setTimeout(tick, 900); return }

      track.classList.add('done')
      cancelBtn.remove()
      if (state.status === 'error') {
        node.classList.add('failed')
        message.textContent = 'Fehlgeschlagen: ' + state.error
      } else if (state.status === 'cancelled') {
        message.textContent = 'Abgebrochen. Was bis dahin angelegt wurde, bleibt erhalten.'
      }
      onDone(state)
      resolve(state)
    }
    tick()
  })

  return { node, finished }
}
