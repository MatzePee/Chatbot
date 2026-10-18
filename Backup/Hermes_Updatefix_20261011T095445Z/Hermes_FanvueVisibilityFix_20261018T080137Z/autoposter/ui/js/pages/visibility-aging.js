import { api } from '../api.js?v=visibility-aging-1'
import { h, clear, card } from '../ui.js?v=creatorstudio-mobile-system-20260909'

export async function visibilityAgingCard(values) {
  const enabled = h('input', {type:'checkbox', checked:values.visibility_aging_enabled === true})
  const days = h('input', {type:'number', min:1, max:36500, step:1, value:values.visibility_aging_days ?? 7, 'aria-label':'Alter in Tagen', style:{maxWidth:'100%',boxSizing:'border-box'}})
  const message = h('div', {role:'status', style:{overflowWrap:'anywhere'}})
  const results = h('div', {role:'status', style:{overflowWrap:'anywhere'}})
  const labels = {ready:'Bereit', interrupted:'Unterbrochen – nächster Lauf übernimmt nach Sperrablauf', ok:'Abgeschlossen', error:'Fehler', running:'Läuft', disabled:'Automatik ausgeschaltet', dry_run:'Trockenlauf – keine Änderungen', paused:'Notfall-Stopp aktiv', already_done:'Heute bereits abgeschlossen', retry_wait:'Wiederholung wartet'}
  const refresh = async () => {
    try {
      const r = await api.visibilityAgingStatus()
      clear(results)
      if (r.runtime_status) results.appendChild(h('p', {}, labels[r.runtime_status] || r.runtime_status))
      if (r.last_run) {
        results.appendChild(h('p', {}, 'Letzte Prüfung: ' + (labels[r.last_run.status] || r.last_run.status) + (r.last_run.accounts?.length ? '' : ' – keine aktiven Fanvue-Konten geprüft')))
        for (const account of r.last_run.accounts || []) {
          if (account.error) results.appendChild(h('p', {}, account.error))
        }
      }
      if (!(r.accounts || []).length) results.appendChild(h('p', {}, 'Noch kein Kontolauf vorhanden.'))
      for (const account of r.accounts || []) {
        const when = account.finished_at || account.started_at
        const row = h('div', {}, h('p', {}, `${account.account_id}: ${labels[account.status] || account.status} · ${account.changed || 0} umgestellt · ${account.scanned || 0} geprüft · ${account.skipped || 0} übersprungen`),
          when ? h('p', {}, 'Stand: ' + new Date(when).toLocaleString('de-DE', {timeZone:'Europe/Berlin'}) + ' (Europe/Berlin)') : null)
        for (const error of account.errors || []) row.appendChild(h('p', {}, (error.post_uuid ? error.post_uuid + ': ' : '') + error.error))
        results.appendChild(row)
      }
    } catch (e) { clear(results).appendChild(h('p', {}, 'Status nicht verfügbar: ' + e.message)) }
  }
  const save = h('button', {onClick:async () => {
    const value = Number(days.value)
    if (!days.value.trim() || !Number.isInteger(value) || value < 1 || value > 36500) {
      message.textContent = 'Bitte eine ganze Zahl von 1 bis 36500 Tagen eingeben.'; return
    }
    save.disabled = true
    try {
      Object.assign(values, await api.saveIntegrations({visibility_aging_enabled:enabled.checked, visibility_aging_days:value}))
      message.textContent = 'Sichtbarkeit gespeichert.'
    } catch(e) { message.textContent = 'Speichern fehlgeschlagen: ' + e.message }
    finally { save.disabled = false }
  }}, 'Sichtbarkeit speichern')
  const run = h('button', {onClick:async () => {
    run.disabled = true
    message.textContent = 'Prüfung wird angefordert …'
    try {
      const r = await api.runVisibilityAging()
      message.textContent = r.message
      await refresh()
    } catch(e) { message.textContent = 'Prüfung fehlgeschlagen: ' + e.message }
    finally { run.disabled = false }
  }}, 'Jetzt prüfen und umstellen')
  await refresh()
  return card('Fanvue-Sichtbarkeit nach Alter',
    h('p', {class:'hint'}, 'Veröffentlichte Follower-und-Abonnenten-Posts ab X × 24 Stunden nur noch für Abonnenten. Inhalt, Medien und Preise bleiben unverändert. Alle Posts der aktiv verbundenen Fanvue-Konten werden geprüft, nicht nur der lokale Kalender.'),
    h('label', {}, enabled, 'Nachts automatisch umstellen'),
    h('p', {class:'hint'}, '00:00 Europe/Berlin (Sommerzeit-sicher), bei Ausfall Nachholung am selben Tag. Standard: aus, 7 Tage.'),
    h('label', {}, 'Alter in Tagen', days),
    h('p', {class:'hint'}, 'Manueller Lauf verwendet ausschließlich das gespeicherte Alter, auch wenn die Nacht-Automatik aus ist. Trockenlauf und Notfall-Stopp verhindern jede Änderung. Laufende Kontoläufe werden nicht doppelt gestartet.'),
    h('div', {class:'row', style:{display:'flex',flexWrap:'wrap',gap:'8px'}}, save, run, h('button', {onClick:refresh}, 'Status aktualisieren')),
    message, results)
}
