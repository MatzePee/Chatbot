import { commonOverview, systemOverview } from '/autoposter/js/pages/settings.js?v=settings-structure-20261120-2'
import { api } from '/autoposter/js/api.js'
const root = document.getElementById('ai-overview')
if (root) {
  try { root.appendChild(await commonOverview()) }
  catch { root.textContent = 'Kostenübersicht derzeit nicht verfügbar.' }
  const status = document.createElement('p')
  const free = document.createElement('input'); free.type = 'checkbox'; free.id = 'ai-free-models'
  const label = document.createElement('label'); label.append(free, 'Nur kostenlose Modelle anzeigen')
  let models = []
  const paint = () => { status.textContent = models.filter(m => !free.checked || m.free).map(m => m.id).join(' · ') }
  free.addEventListener('change', paint)
  const refresh = document.createElement('button'); refresh.type = 'button'; refresh.textContent = 'Modellliste aktualisieren'
  refresh.onclick = async () => { try { const r = await api.openrouterModels(true); models = r.models || []; paint() } catch { status.textContent = 'Modellliste nicht verfügbar.' } }
  const test = document.createElement('button'); test.type = 'button'; test.textContent = 'Verbindung testen'
  test.onclick = async () => { try { const r = await api.openrouterTest(document.querySelector('[name=openrouter_model_text]').value); status.textContent = r.message } catch { status.textContent = 'Verbindungstest fehlgeschlagen.' } }
  root.append(label, refresh, test, status)
}

const system = document.getElementById('system-overview')
if(system) {
  try { system.appendChild(await systemOverview()) }
  catch(error) { system.textContent = 'Systeminformationen konnten nicht geladen werden: ' + error.message }
}
