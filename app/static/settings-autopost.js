import { api } from '/autoposter/js/api.js?v=creatorstudio-mobile-system-20260909'
import renderSettings from '/autoposter/js/pages/settings.js?v=creatorstudio-channel-autoplan-20260910'

const root = document.getElementById('autopost-settings')
async function load() {
  try {
    // Use the existing authentication and API guards; never invent an admin user.
    const user = await api.me()
    const page = await renderSettings({ user })
    page.querySelectorAll('a[href^="#/"]').forEach(a => {
      a.href = '/autoposter/' + a.getAttribute('href')
    })
    root.replaceChildren(page)
    window.dispatchEvent(new Event('settings-ready'))
  } catch (error) {
    const message = document.createElement('p')
    message.className = 'errbox'
    message.textContent = 'AutoPost-Einstellungen konnten nicht geladen werden: ' + error.message
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
