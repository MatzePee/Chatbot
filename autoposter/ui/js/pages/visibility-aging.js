import { api } from '../api.js?v=ppv-restore-1'
import { h, clear, card, confirmDialog } from '../ui.js?v=creatorstudio-mobile-system-20260909'

export async function visibilityAgingCard(values) {
  const enabled = h('input', {type:'checkbox', checked:values.visibility_aging_enabled === true})
  const days = h('input', {type:'number', min:1, max:36500, step:1, value:values.visibility_aging_days ?? 7, 'aria-label':'Alter in Tagen', style:{maxWidth:'100%',boxSizing:'border-box'}})
  const message = h('div', {id:'visibility-aging-save-status', role:'status', style:{overflowWrap:'anywhere'}}, 'Keine ungespeicherten Änderungen.')
  enabled.setAttribute('aria-describedby', message.id)
  days.setAttribute('aria-describedby', message.id)
  let savedEnabled = enabled.checked, savedDays = Number(days.value)
  const dirty = () => enabled.checked !== savedEnabled || Number(days.value) !== savedDays || !days.value.trim()
  const changed = () => { message.textContent = dirty() ? 'Ungespeicherte Änderungen – bitte Sichtbarkeit speichern.' : 'Keine ungespeicherten Änderungen.' }
  enabled.addEventListener('change', changed)
  days.addEventListener('input', changed)
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
        const failedPosts = (account.errors || []).filter(error => error.post_uuid).length
        const row = h('div', {}, h('p', {}, `${account.account_id}: ${labels[account.status] || account.status} · ${account.changed || 0} umgestellt · ${account.scanned || 0} geprüft · ${account.skipped || 0} übersprungen · ${failedPosts} nicht bestätigt`),
          when ? h('p', {}, 'Stand: ' + new Date(when).toLocaleString('de-DE', {timeZone:'Europe/Berlin'}) + ' (Europe/Berlin)') : null)
        if (failedPosts && account.changed) row.appendChild(h('p', {}, 'Bestätigte Umstellungen bleiben erfolgreich; einzelne Fehler machen sie nicht rückgängig.'))
        const stages = {precheck:'Vorprüfung (GET) – keine Änderung versucht', patch:'Umstellung (PATCH) fehlgeschlagen', postcheck:'Nachkontrolle (GET) – Änderung angefordert, aber nicht bestätigt'}
        for (const error of account.errors || []) {
          // Old persisted reports did not record the failing request stage.
          const legacy404 = error.error === 'Fanvue Sichtbarkeit: HTTP 404 (Token, Scopes/API-Version prüfen)'
          const text = legacy404 ? 'Fanvue HTTP 404: Ressource nicht gefunden oder für dieses Konto nicht verfügbar. Phase im alten Lauf nicht erfasst.' : error.error
          row.appendChild(h('p', {}, (error.post_uuid ? error.post_uuid + ': ' : '') + (stages[error.stage] ? stages[error.stage] + ' · ' : '') + text))
        }
        results.appendChild(row)
      }
    } catch (e) { clear(results).appendChild(h('p', {}, 'Status nicht verfügbar: ' + e.message)) }
  }
  const save = h('button', {type:'button', class:'primary', onClick:async () => {
    const value = Number(days.value)
    if (!days.value.trim() || !Number.isInteger(value) || value < 1 || value > 36500) {
      message.textContent = 'Bitte eine ganze Zahl von 1 bis 36500 Tagen eingeben.'; return
    }
    save.disabled = enabled.disabled = days.disabled = true
    try {
      Object.assign(values, await api.saveIntegrations({visibility_aging_enabled:enabled.checked, visibility_aging_days:value}))
      savedEnabled = enabled.checked; savedDays = value
      message.textContent = 'Sichtbarkeit gespeichert.'
    } catch(e) { message.textContent = 'Speichern fehlgeschlagen – Änderungen nicht gespeichert: ' + e.message }
    finally { save.disabled = enabled.disabled = days.disabled = false }
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
  const ppvStatus = h('div', {role:'status', style:{overflowWrap:'anywhere'}})
  let ppvReady = false
  try { await api.ppvRestoreStatus(); ppvReady = true }
  catch(e) { ppvStatus.textContent = 'PPV-Wiederherstellung erst nach dem Backend-Neustart verfügbar.' }
  const ppvButton = h('button', {type:'button', disabled:!ppvReady, onClick:async () => {
    ppvButton.disabled = true
    ppvStatus.textContent = 'PPV-Posts werden ohne Änderung geprüft …'
    try {
      const preview = await api.ppvRestorePreview()
      const count = (preview.accounts || []).reduce((n, a) => n + (a.candidates || 0), 0)
      const errors = (preview.accounts || []).filter(a => a.status === 'error')
      if (errors.length) { ppvStatus.textContent = 'Vorschau unvollständig – keine Änderung gestartet. Fanvue-Verbindung prüfen.'; return }
      if (!count) { ppvStatus.textContent = 'Keine veröffentlichten PPV-Posts mit Sichtbarkeit nur für Subscriber gefunden.'; return }
      ppvStatus.textContent = `${count} PPV-Posts in ${(preview.accounts || []).length} Konto/Konten gefunden.`
      const confirmed = await confirmDialog('PPV-Posts für Follower sichtbar machen',
        `${count} veröffentlichte PPV-Posts, die derzeit nur für Subscriber sichtbar sind, für Follower und Subscriber sichtbar machen? Auch manuell erstellte Posts sind enthalten. Preis, Inhalt und Medien bleiben unverändert. Jeder Post wird unmittelbar vor der Änderung erneut geprüft.`,
        {okLabel:'PPVs für Follower sichtbar machen'})
      if (!confirmed) { ppvStatus.textContent = 'Abgebrochen – nichts geändert.'; return }
      const response = await api.runPpvRestore()
      ppvStatus.textContent = response.message
    } catch(e) { ppvStatus.textContent = 'PPV-Prüfung oder Start fehlgeschlagen: ' + e.message }
    finally { ppvButton.disabled = false }
  }}, 'Bestehende PPVs für Follower sichtbar machen')
  const ppvRefresh = async () => {
    try {
      const response = await api.ppvRestoreStatus()
      ppvReady = true; ppvButton.disabled = false
      ppvStatus.textContent = (response.accounts || []).map(a => `${a.account_id}: ${a.status} · ${a.changed || 0} für Follower sichtbar gemacht · ${a.skipped || 0} übersprungen · ${(a.errors || []).length} Fehler`).join(' · ') || 'Noch kein PPV-Lauf.'
    } catch(e) { ppvStatus.textContent = 'PPV-Laufstatus nicht verfügbar: ' + e.message }
  }
  await refresh()
  return card('Fanvue-Sichtbarkeit nach Alter',
    h('p', {class:'hint'}, 'Nur kostenlose veröffentlichte Follower-und-Abonnenten-Posts ab X × 24 Stunden werden auf Abonnenten umgestellt. PPV-Posts (Preis > 0) und Posts ohne eindeutig erkennbaren Preis bleiben unverändert. Inhalt, Medien und Preise bleiben unverändert. Alle Posts der aktiv verbundenen Fanvue-Konten werden geprüft, nicht nur der lokale Kalender.'),
    h('label', {}, enabled, 'Nachts automatisch umstellen'),
    h('p', {class:'hint'}, '00:00 Europe/Berlin (Sommerzeit-sicher), bei Ausfall Nachholung am selben Tag. Standard: aus, 7 Tage.'),
    h('label', {}, 'Alter in Tagen', days),
    h('div', {class:'row', style:{display:'flex',flexWrap:'wrap',gap:'8px'}}, save),
    message,
    h('p', {class:'hint'}, 'Manueller Lauf verwendet ausschließlich das gespeicherte Alter, auch wenn die Nacht-Automatik aus ist. Trockenlauf und Notfall-Stopp verhindern jede Änderung. Laufende Kontoläufe werden nicht doppelt gestartet.'),
    h('div', {class:'row', style:{display:'flex',flexWrap:'wrap',gap:'8px'}}, run, h('button', {type:'button', onClick:async () => { await refresh(); await ppvRefresh() }}, 'Status aktualisieren'), ppvButton),
    ppvStatus,
    results)
}
