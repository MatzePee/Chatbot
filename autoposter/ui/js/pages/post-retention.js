import { api } from '../api.js?v=post-retention-1'
import { h, card, confirmDialog } from '../ui.js?v=creatorstudio-mobile-system-20260909'

export async function postRetentionCard(values) {
  let status={active:false}, backendReady=false
  try { status=await api.postRetentionStatus(); backendReady=true }
  catch(e) { /* Source may be uploaded while the old server remains running. */ }
  const days = h('input', {type:'number', min:1, max:36500, step:1, value:values.post_retention_days ?? 30})
  const ppv = h('input', {type:'checkbox', checked:values.post_retention_delete_ppv === true})
  const automatic = h('input', {type:'checkbox', checked:values.post_retention_automatic === true, disabled:status.automatic_active !== true})
  const note = h('p', {role:'status'}, !backendReady ? 'Vorbereitet – der laufende Dienst wurde nicht neu gestartet.' : status.active ? 'Löschfunktion freigegeben.' : 'Noch nicht aktiviert – Löschen ist serverseitig gesperrt.')
  const results = h('div', {role:'status', style:{overflowWrap:'anywhere'}})
  const valid = () => {
    const n = Number(days.value)
    if (!days.value.trim() || !Number.isInteger(n) || n < 1 || n > 36500) throw new Error('Bitte 1–36500 ganze Tage eingeben.')
    return n
  }
  let preview = null
  const invalidate = () => { preview=null; run.disabled=true; results.textContent='Einstellungen geändert – Vorschau erneut laden.' }
  days.addEventListener('input',invalidate); ppv.addEventListener('change',invalidate); automatic.addEventListener('change',invalidate)
  const save = h('button', {type:'button', disabled:!backendReady, onClick:async () => {
    try {
      const n=valid()
      Object.assign(values, await api.saveIntegrations({post_retention_days:n,post_retention_delete_ppv:ppv.checked,post_retention_automatic:automatic.checked}))
      invalidate(); note.textContent = status.active && status.automatic_active && automatic.checked
        ? 'Gespeichert. Tägliches Löschen aktiviert; erster Prüflauf beim nächsten Scheduler-Durchlauf.'
        : 'Gespeichert. ' + (status.active ? 'Automatik ausgeschaltet.' : 'Löschen bleibt gesperrt.')
    } catch(e) { note.textContent='Speichern fehlgeschlagen: '+e.message }
  }}, 'Einstellungen speichern')
  const show = h('button', {type:'button', disabled:!backendReady, onClick:async () => {
    try {
      const n=valid()
      results.textContent='Fanvue-Posts werden ohne Änderung geprüft …'
      const response=await api.postRetentionPreview({days:n,delete_ppv:ppv.checked})
      preview={days:n,delete_ppv:ppv.checked}
      results.textContent=(response.accounts || []).map(a => a.error
        ? `Konto: ${a.error}` : `${a.account_id}: ${a.candidates.length} Kandidaten von ${a.scanned} Posts (${a.status})`).join(' · ') || 'Keine verbundenen Fanvue-Konten.'
      run.disabled=!status.active || response.status!=='ok' || !(response.accounts || []).some(a=>a.candidates.length)
    } catch(e) { preview=null; run.disabled=true; results.textContent='Vorschau fehlgeschlagen: '+e.message }
  }}, 'Vorschau (nichts löschen)')
  const run = h('button', {type:'button', class:'danger', disabled:true, onClick:async () => {
    if (!preview || !status.active) return
    const count = (await api.postRetentionPreview(preview)).accounts.reduce((n,a)=>n+(a.candidates?.length || 0),0)
    if (!count) { invalidate(); return }
    if (!await confirmDialog('Fanvue-Posts löschen', `${count} veröffentlichte Posts älter als ${preview.days} Tage auf Fanvue löschen? PPV ${preview.delete_ppv ? 'einschließen' : 'behalten'}. AutoPost-Verlauf bleibt erhalten.`, {okLabel:'Auf Fanvue löschen',danger:true})) return
    run.disabled=true
    try { const r=await api.runPostRetention({...preview,confirm:'FANVUE_POSTS_LOESCHEN'}); results.textContent=r.message }
    catch(e) { results.textContent='Start fehlgeschlagen: '+e.message }
  }}, 'Jetzt auf Fanvue löschen')
  const refresh = h('button', {type:'button', disabled:!backendReady, onClick:async () => {
    try {
      const r=await api.postRetentionStatus()
      results.textContent=(r.accounts || []).map(a=>`${a.account_id}: ${a.status} · ${a.deleted || 0} gelöscht · ${a.skipped || 0} übersprungen · ${(a.errors || []).length} Fehler${a.errors?.length ? ' · Ursache: ' + a.errors[0].error : ''}`).join(' · ') || 'Noch kein Löschlauf.'
    } catch(e) { results.textContent=e.message }
  }}, 'Laufstatus aktualisieren')
  return card('Fanvue-Posts nach Alter löschen',
    h('p', {class:'hint'}, 'Alle veröffentlichten Feedposts der verbundenen Fanvue-Konten, auch manuell erstellte. Alter ab tatsächlicher Veröffentlichung. Angepinnte Posts werden unabhängig vom Alter immer behalten – auch bei aktivierter PPV-Löschung. Unklarer Preis oder Pin-Status wird immer behalten. Keine lokalen Posts oder Medien werden gelöscht.'),
    note, h('label',{},'Älter als (Tage)',days),
    h('label',{},ppv,'Auch PPV-Posts löschen (Preis > 0)'),
    h('label',{},automatic,status.automatic_active ? 'Täglich automatisch löschen' : 'Täglich automatisch löschen (noch gesperrt)'),
    h('p',{class:'hint'},'Die Automatik startet erst nach dem Anhaken und Speichern. Der Job prüft stündlich und führt höchstens einen erfolgreichen Löschlauf pro Tag aus. Angepinnte Posts und Posts mit unklarem Pin-Status bleiben immer erhalten.'),
    h('div',{class:'row',style:{display:'flex',flexWrap:'wrap',gap:'8px'}},save,show,run,refresh),results)
}
