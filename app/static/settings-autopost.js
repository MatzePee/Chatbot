import { api } from '/autoposter/js/api.js?v=creatorstudio-mobile-system-20260909'
import renderSettings from '/autoposter/js/pages/settings.js?v=settings-navigation-20261101'
import renderChannels from '/autoposter/js/pages/channels.js?v=settings-navigation-20261101'
import renderPersonas from '/autoposter/js/pages/personas.js?v=creatorstudio-persona-types-20260910'

const root = document.getElementById('autopost-settings')
const pages = { autopost: renderSettings, channels: renderChannels, personas: renderPersonas }
const section = root.dataset.settingsPage || 'autopost'
async function load() {
  try {
    // Use the existing authentication and API guards; never invent an admin user.
    const user = await api.me()
    const page = await (pages[section] || renderSettings)({ user })
    page.querySelectorAll('a[href^="#/"]').forEach(a => {
      a.href = '/autoposter/' + a.getAttribute('href')
    })
    root.replaceChildren(page)
    window.dispatchEvent(new Event('settings-ready'))
  } catch (error) {
    const message = document.createElement('p')
    message.className = 'errbox'
    message.textContent = 'Einstellungen konnten nicht geladen werden: ' + error.message
    const retry = document.createElement('button')
    retry.type = 'button'
    retry.textContent = 'Erneut versuchen'
    retry.addEventListener('click', load)
    const login = document.createElement('a')
    login.href = '/autoposter/#/settings'
    login.textContent = 'Bei AutoPost anmelden'
    root.replaceChildren(message, retry, login)
  }
}
load()
