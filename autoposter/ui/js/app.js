import '/static/live-refresh.js?v=creatorstudio-mobile-system-20260909'
// Einstiegspunkt: Anmeldung prüfen, Rahmen aufbauen, Hash-Router.
import { api } from './api.js?v=creatorstudio-mobile-system-20260909'
import { h, clear, append, toast } from './ui.js?v=creatorstudio-mobile-system-20260909'

import renderDashboard from './pages/dashboard.js?v=creatorstudio-mobile-system-20260909'
import renderLibrary from './pages/library.js?v=creatorstudio-mobile-system-20260909'
import renderAssign from './pages/assign.js?v=creatorstudio-mobile-system-20260909'
import renderMatrix from './pages/matrix.js?v=creatorstudio-mobile-system-20260909'
import renderCalendar from './pages/calendar.js?v=creatorstudio-fanvue-channels-20260910'
import renderChannels from './pages/channels.js?v=creatorstudio-fanvue-channels-20260910'
import renderPersonas from './pages/personas.js?v=creatorstudio-mobile-system-20260909'
import renderSettings from './pages/settings.js?v=creatorstudio-mobile-system-20260909'


const ROUTES = [
  { path: '', label: 'Dashboard',     icon: '◫', render: renderDashboard },
  { path: 'library', label: 'Bibliothek', icon: '▦', render: renderLibrary },
  { path: 'assign', label: 'Zuordnung',  icon: '⇄', render: renderAssign },
  { path: 'matrix', label: 'Matrix',     icon: '⊞', render: renderMatrix },
  { path: 'calendar', label: 'Kalender', icon: '▤', render: renderCalendar },
  { path: 'channels', label: 'Kanäle',   icon: '◉', render: renderChannels },
  { path: 'personas', label: 'Personas', icon: '☺', render: renderPersonas },
  { path: 'settings', label: 'AutoPost-Einstellungen', icon: '⚙', render: renderSettings },

]

const state = { user: null, runtime: null, authEnabled: false }
const root = document.getElementById('root')

function currentPath() {
  const raw = location.hash.replace(/^#\/?/, '')
  return raw.split('?')[0]
}

function currentQuery() {
  const raw = location.hash.replace(/^#\/?/, '')
  const idx = raw.indexOf('?')
  return idx === -1 ? new URLSearchParams() : new URLSearchParams(raw.slice(idx + 1))
}

function buildShell() {
  const nav = h('nav', { class: 'workspace-nav', 'aria-label': 'AutoPost-Seiten' })
  for (const route of ROUTES) {
    nav.appendChild(h('a', {
      href: '#/' + route.path,
      class: 'navlink',
      dataset: { path: route.path },
    }, h('span', { class: 'workspace-icon', 'aria-hidden': 'true' }, route.icon), h('span', { class: 'workspace-label' }, route.label)))
  }

  const banner = h('div', { id: 'banner' })
  fetch('/api/creatorpilot/health').then(r => r.json()).then(s => {
    if (s.preview) view.before(h('div', { class: 'preview-notice', role: 'status' }, 'Geschützter Mitlesemodus · Live lesen & KI-Vorschläge. Nachrichtenversand, Posts und Token-Erneuerungen sind gesperrt.'));
  }).catch(() => {})
  const view = h('div', { id: 'view' })

  const shell = h('div', { class: 'workspace-frame' },
    h('header', { class: 'workspace-topbar' },
      h('a', { class: 'workspace-brand', href: '/', 'aria-label': 'MP CreatorStudio – Startseite' },
        h('img', { src: '/static/creatorpilot.svg', alt: '', width: 32, height: 32 }),
        h('span', {}, 'MP CreatorStudio')),
      h('nav', { class: 'workspace-switcher', 'aria-label': 'Programmbereiche' },
        h('a', { href: '/' }, 'AutoChat'),
        h('a', { href: '/autoposter/', class: 'active', 'aria-current': 'true' }, 'AutoPost'),
        h('a', { href: '/settings/shared' }, 'Einstellungen'),
        h('a', { href: '/system' }, 'System')),
      h('button', { class: 'workspace-menu-button', type: 'button', 'aria-expanded': 'false', 'aria-controls': 'workspace-sidebar' }, '☰ Menü')),
    h('div', { class: 'workspace-body shell' },
      h('button', { class: 'workspace-backdrop', type: 'button', 'aria-label': 'Menü schließen', tabindex: '-1' }),
      h('aside', { class: 'workspace-sidebar', id: 'workspace-sidebar', 'aria-label': 'AutoPost' },
        h('div', { class: 'workspace-section' }, h('strong', {}, 'AutoPost')),
        nav,
        h('div', { class: 'workspace-sidefoot' },
          h('div', { class: 'who' }, state.user.display_name || state.user.email),
          h('div', { class: 'role' }, state.authEnabled ? state.user.role : 'internes Netz'),
          state.authEnabled ? h('button', { onClick: logout }, 'Abmelden') : null)),
      h('main', { class: 'main workspace-scroll' }, banner, view)),
  )
  clear(root).appendChild(shell)
  return { view, banner }
}

async function logout() {
  await api.logout().catch(() => {})
  state.user = null
  location.hash = ''
  start()
}

function markActive() {
  const path = currentPath()
  document.querySelectorAll('.navlink').forEach((a) => {
    const active = a.dataset.path === path
    a.classList.toggle('active', active)
    if (active) a.setAttribute('aria-current', 'page')
    else a.removeAttribute('aria-current')
  })
}

async function refreshBanner(banner) {
  try {
    const s = await api.settings()
    state.runtime = s
    clear(banner)
    if (s.global_pause) {
      banner.appendChild(h('div', { class: 'banner pause' },
        '⛔ Notfall-Stopp aktiv — es wird nichts veröffentlicht und nichts geplant.'))
    } else if (s.dry_run) {
      banner.appendChild(h('div', { class: 'banner dry' },
        '⚠ Trockenlauf aktiv — Posts werden vollständig verarbeitet, aber nicht an die Plattform gesendet.'))
    }
  } catch { /* Banner ist nicht kritisch */ }
}

let shell = null

let routeVersion = 0
async function route() {
  const version = ++routeVersion
  if (!state.user) return
  if (!shell) shell = buildShell()
  markActive()
  refreshBanner(shell.banner)

  const path = currentPath()
  const entry = ROUTES.find((r) => r.path === path) || ROUTES[0]
  const view = shell.view
  clear(view).appendChild(h('div', { class: 'empty' }, 'Lade …'))

  try {
    const node = await entry.render({ query: currentQuery(), user: state.user, state })
    if (version !== routeVersion) return
    clear(view).appendChild(node)
  } catch (err) {
    if (version !== routeVersion) return
    console.error(err)
    clear(view).appendChild(
      h('div', { class: 'page' },
        h('div', { class: 'errbox' }, 'Seite konnte nicht geladen werden: ' + (err.message || err)),
        h('button', { style: { marginTop: '12px' }, onClick: route }, 'Erneut versuchen'),
      ),
    )
  }
}

async function start() {
  // Im internen Netz läuft die Anwendung ohne Anmeldung. Das Backend sagt uns,
  // ob eine Anmeldeseite überhaupt gebraucht wird.
  let mode = { auth_enabled: false }
  try {
    mode = await api.authMode()
  } catch { /* Standard: keine Anmeldung */ }

  try {
    state.user = await api.me()
  } catch {
    state.user = null
  }

  if (!state.user) {
    if (!mode.auth_enabled) {
      clear(root).appendChild(
        h('div', { class: 'page' },
          h('div', { class: 'errbox' },
            'Das Backend ist nicht erreichbar oder liefert keinen Benutzer. ' +
            'Läuft der Server? Details im Terminal.'),
          h('button', { style: { marginTop: '12px' }, onClick: start }, 'Erneut versuchen'),
        ),
      )
      return
    }
    const { default: renderLogin } = await import('./pages/login.js?v=creatorstudio-mobile-system-20260909')
    shell = null
    clear(root).appendChild(renderLogin({ onLogin: (user) => { state.user = user; start() } }))
    return
  }
  state.authEnabled = mode.auth_enabled
  route()
}

window.addEventListener('hashchange', route)
window.addEventListener('unhandledrejection', (e) => {
  if (e.reason && e.reason.status === 401) {
    state.user = null
    shell = null
    start()
  }
})

start()

window.CreatorStudioLive.start(async () => {
  if (!state.user || !shell) return false
  const current = shell.view.firstElementChild
  if (!current) return false
  const version = routeVersion
  const positions = [shell.view, ...shell.view.querySelectorAll('*')].filter(n => n.scrollTop || n.scrollLeft)
    .map(n => [n, n.scrollLeft, n.scrollTop])
  const x = scrollX, y = scrollY
  if (current.refresh) {
    const result = await current.refresh()
    if (result === false) return false
  } else {
    if (window.CreatorStudioLive.dirty(current)) return false
    const entry = ROUTES.find(r => r.path === currentPath()) || ROUTES[0]
    const node = await entry.render({ query: currentQuery(), user: state.user, state })
    if (version !== routeVersion || window.CreatorStudioLive.busy() || window.CreatorStudioLive.dirty(current)) return false
    shell.view.replaceChildren(node)
  }
  if (version !== routeVersion) return false
  positions.forEach(([n, x, y]) => { if (n.isConnected) { n.scrollLeft = x; n.scrollTop = y } })
  scrollTo(x, y)
  await refreshBanner(shell.banner)
  return true
}, document.body)
