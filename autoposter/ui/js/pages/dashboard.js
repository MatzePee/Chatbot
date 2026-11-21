import { platformIcon, platformLabel } from '../platform-icon.js?v=persona-hashtags-20261121-1'
import { runtimeCard } from './settings.js?v=persona-hashtags-20261121-1'
import { historyChart } from '../account-history.js?v=persona-hashtags-20261121-1'
import { renderUpcoming } from '../upcoming-posts.js?v=persona-hashtags-20261121-1'
import { api } from '../api.js?v=persona-hashtags-20261121-1'
import { h, card, empty, toast, fmtDate, fmtDateTime, LIFECYCLE, LIFECYCLE_ORDER, spinner, clear, guard } from '../ui.js?v=persona-hashtags-20261121-1'

export function problemTime(raw) {
  if (!raw) return 'Zeitpunkt unbekannt'
  const value = /(?:Z|[+-]\d{2}:?\d{2})$/.test(raw) ? raw : raw + 'Z'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Zeitpunkt unbekannt'
  return new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'long', timeZone: 'Europe/Berlin' }).format(date) + ' (Europe/Berlin)'
}

export default async function renderDashboard() {
  const data = await api.dashboard()
  const page = h('div', { class: 'page stack' })

  // ------------------------------------------------------------ Kopf
  const fillBtn = h('button', { class: 'primary' }, 'Kalender für 14 Tage füllen')
  fillBtn.addEventListener('click', guard(async () => {
    clear(fillBtn).appendChild(spinner())
    fillBtn.disabled = true
    try {
      const r = await api.fillCalendar([], 14, false)
      toast.ok(`${r.created_post_ids.length} Posts geplant, ${r.gaps.length} Lücken offen`)
      if (r.gaps.length) toast.info(r.gaps[0].channel_name + ': ' + r.gaps[0].reason)
      location.reload()
    } finally {
      fillBtn.disabled = false
    }
  }))

  page.appendChild(h('div', { class: 'page-head' },
    h('h1', {}, 'Übersicht'),
    h('div', { class: 'spacer' }),
    h('button', { onClick: () => location.reload() }, 'Aktualisieren'),
    fillBtn,
  ))

  page.appendChild(await runtimeCard())

  // ------------------------------------------------------------ Reichweite
  const inv = h('div', { class: 'grid c3' })
  for (const e of data.inventory.channels) {
    inv.appendChild(h('div', { class: 'card pad' },
      h('div', { class: 'row', style: { marginBottom: '8px' } },
        platformIcon(e.platform),
        h('i', { class: 'dot', style: { background: e.color } }),
        h('b', { style: { flex: '1' } }, e.channel_name),
        h('span', { class: 'hint', style: { textTransform: 'uppercase' } }, e.platform),
      ),
      h('div', { class: 'row', style: { alignItems: 'baseline' } },
        h('span', { class: 'big num' }, e.available),
        h('span', { class: 'hint' }, 'Bilder verfügbar'),
      ),
      h('div', { class: 'row', style: { marginTop: '6px', fontSize: '12px' } },
        h('i', { class: 'led ' + e.traffic_light }),
        e.days_left === null
          ? h('span', { class: 'hint' }, 'keine Prognose')
          : h('span', {}, 'reicht ca. ', h('b', {}, e.days_left), ' Tage',
              e.empty_on ? h('span', { class: 'hint' }, ' · leer am ' + fmtDate(e.empty_on)) : null),
      ),
      h('div', { class: 'hint', style: { marginTop: '6px' } },
        `${e.assigned_total} zugeordnet · ${e.used} verbraucht · ${e.scheduled} eingeplant · ${e.consumption_per_day}/Tag`),
      e.shares_pool_with.length
        ? h('div', { style: { color: '#946000', fontSize: '11px', marginTop: '4px' } },
            `Teilt sich den Pool mit ${e.shares_pool_with.length} weiteren Kanälen`)
        : null,
    ))
  }

  const invCard = card('Wie lange reicht der Bildvorrat?',
    data.inventory.channels.length ? inv : empty('Noch keine aktiven Kanäle.'),
    data.inventory.unassigned_assets > 0
      ? h('a', { href: '#/assign', class: 'warnbox', style: { marginTop: '12px', textDecoration: 'none' } },
          `${data.inventory.unassigned_assets} Bilder sind keinem Kanal zugeordnet und liegen ungenutzt. Jetzt zuordnen →`)
      : null,
  )
  page.appendChild(invCard)

  // Cached account metrics only: no X requests from browser refresh.
  for (const platform of ['x', 'telegram', 'instagram']) {
  const title = platform === 'instagram' ? 'Instagram-Follower' : platform === 'x' ? 'X-Follower' : 'Telegram-Abonnenten / Kanalmitglieder'
  try {
    const paidTelegramIds = new Set(data.channel_health
      .filter(channel => channel.platform === 'telegram' && channel.telegram_mode === 'paid')
      .map(channel => channel.id))
    const accounts = (await (platform === 'instagram' ? api.instagramFollowers() : platform === 'x' ? api.xFollowers() : api.telegramMembers()))
      .filter(account => platform !== 'telegram' || !paidTelegramIds.has(account.channel_id))
    const historyKeys = ['yesterday', 'days7', 'days30']
    const historyText = value => value.count === null ? 'Keine Historie'
      : `${value.count} (${value.growth === null ? 'Wachstum unbekannt' : (value.growth >= 0 ? '+' : '') + value.growth})`
    const measurementText = a => `${problemTime(a.sampled_at)}${a.stale ? ' · veraltet' : ''} · ${a.status}`
    const accountChart = account => {
      const chart = historyChart(account)
      const caption = chart.querySelector('figcaption')
      if (caption) caption.replaceChildren(platformLabel(platform, caption.textContent))
      return chart
    }
    const rows = accounts.flatMap(a => [h('tr', {},
      h('td', {}, platformLabel(platform, a.name)),
      h('td', { class: 'num' }, a.current ?? 'Keine Messung'),
      ...historyKeys.map(key => h('td', { class: 'num' }, historyText(a[key]))),
      h('td', { class: 'hint' }, measurementText(a)),
    ), h('tr', {}, h('td', { colspan: 6 }, accountChart(a)))])
    // CSS display:none excludes the inactive view from both focus and accessibility trees.
    // Both views use text nodes via h(), never account/status HTML.
    const mobileAccounts = h('div', { class: 'x-followers-cards col' }, ...accounts.map(a =>
      h('article', { class: 'x-followers-account' },
        h('h3', {}, platformLabel(platform, a.name)),
        h('dl', { class: 'x-followers-metrics' },
          ...[a.current ?? 'Keine Messung', ...historyKeys.map(key => historyText(a[key]))].map((value, i) =>
            h('div', { class: 'x-followers-metric' },
              h('dt', { class: 'hint' }, ['Aktuell', 'Gestern (Δ)', 'Vor 7 Tagen (Δ)', 'Vor 30 Tagen (Δ)'][i]),
              h('dd', { class: 'num' }, value))),
        ),
        h('div', { class: 'x-followers-measurement hint' },
          h('b', {}, 'Messzeit / Status'), h('div', {}, measurementText(a))),
        accountChart(a),
      )))
    const refreshInstagram = platform === 'instagram' ? h('button',{type:'button',onClick:guard(async event=>{
      const button=event.currentTarget;button.disabled=true;button.textContent='Wird abgerufen …'
      try {const result=await api.refreshInstagramFollowers();toast.info(result.updated ? 'Instagram-Followerzahl aktualisiert' : 'Keine neue Messung verfügbar – Status prüfen');location.reload()}
      finally {button.disabled=false;button.textContent='Instagram-Follower aktualisieren'}
    })},'Instagram-Follower aktualisieren') : null
    page.appendChild(card(platformLabel(platform, title + ' · Stand und Wachstum'),
      refreshInstagram,
      h('div', { class: 'hint' }, 'Vergleich mit dem exakten Kalendertag (Europe/Berlin). Fehlende Messungen werden nicht geschätzt.'),
      accounts.length ? h('div', { class: 'x-followers' },
        h('div', { class: 'x-followers-table table-scroll', tabindex: 0 }, h('table', {},
          h('thead', {}, h('tr', {}, ...['Account', 'Aktuell', 'Gestern (Δ)', 'Vor 7 Tagen (Δ)', 'Vor 30 Tagen (Δ)', 'Messzeit / Status'].map(t => h('th', {}, t)))),
          h('tbody', {}, ...rows))), mobileAccounts) : empty(platform === 'instagram' ? 'Keine Instagram-Kanäle.' : platform === 'x' ? 'Keine X-Accounts.' : 'Keine Telegram-Kanäle.'),
    ))
  } catch { page.appendChild(card(platformLabel(platform, title), empty('Messübersicht derzeit nicht verfügbar.'))) }
  }

  // ------------------------------------------------------------ Lebenszyklus
  const counts = data.lifecycle_counts
  const lc = h('div', { class: 'grid c6' })
  for (const key of LIFECYCLE_ORDER) {
    lc.appendChild(h('a', {
      href: '#/library?lifecycle=' + key,
      class: 'card pad',
      style: { textDecoration: 'none', color: 'inherit' },
    },
      h('div', { class: 'row', style: { gap: '6px' } },
        h('i', { class: 'dot', style: { background: LIFECYCLE[key].color } }),
        h('span', { class: 'hint' }, LIFECYCLE[key].label),
      ),
      h('div', { class: 'big num' }, counts[key] ?? 0),
    ))
  }
  page.appendChild(card('Bestand nach Lebenszyklus', lc))

  // ------------------------------------------------------------ Nächste Posts / Probleme
  const upcoming = h('div', { class: 'col' })
  renderUpcoming(upcoming, data.upcoming_posts, data.channel_health)

  const problems = h('div', { class: 'col' })
  function drawProblems(current) {
    clear(problems)
    if (!current.notifications.length && !current.recent_failures.length) problems.appendChild(empty('Alles ruhig.'))
    const channelPlatform = id => current.channel_health.find(channel => channel.id === id)?.platform
    const notificationPlatform = note => note.platform || (note.entity === 'channel' ? channelPlatform(note.entity_id)
      : note.entity === 'post' ? channelPlatform([...current.upcoming_posts, ...current.recent_failures].find(post => post.id === note.entity_id)?.channel_id) : null)
    for (const n of current.notifications) {
      problems.appendChild(h('div', { class: n.level === 'error' ? 'errbox' : 'warnbox' },
        h('div', {}, h('b', {}, platformLabel(notificationPlatform(n), n.title)), h('div', { class: 'hint' }, n.body)),
        h('div', { class: 'hint' }, problemTime(n.created_at)),
        h('button', { class: 'small', title: 'Meldung ausblenden; behebt nicht die Ursache', onClick: guard(async () => {
          await api.markRead([n.id]); drawProblems(await api.dashboard())
        }) }, 'Zur Kenntnis genommen'),
      ))
    }
    for (const p of current.recent_failures) {
      if (current.notifications.some(n => n.entity === 'post' && n.entity_id === p.id && /^(Preflight fehlgeschlagen:|Veröffentlichung fehlgeschlagen:)/.test(n.title))) continue
      problems.appendChild(h('div', { class: p.retry_ready ? 'warnbox' : 'errbox' },
        h('b', {}, platformLabel(channelPlatform(p.channel_id), p.retry_ready ? 'Post wartet auf erneute Freigabe' : 'Veröffentlichung fehlgeschlagen')),
        h('div', { class: 'hint' }, p.error_message),
        h('div', { class: 'hint' }, problemTime(p.last_attempt_at || p.created_at)),
        h('button', { class: 'small', title: 'Nur diesen Fehlversuch ausblenden; kein erneuter Versand', onClick: guard(async () => {
          await api.ackFailure(p.id); drawProblems(await api.dashboard())
        }) }, 'Zur Kenntnis genommen'),
      ))
    }
  }
  drawProblems(data)
  const checkBtn = h('button', { class: 'small', title: 'Probleme werden zusätzlich alle 60 Minuten geprüft' }, 'Jetzt prüfen')
  checkBtn.addEventListener('click', guard(async () => {
    if (checkBtn.disabled) return
    checkBtn.disabled = true
    checkBtn.textContent = 'Prüfe …'
    try {
      const result = await api.checkProblems()
      drawProblems(await api.dashboard())
      if (result.errors?.length) toast.error(`${result.errors.length} Meldungen konnten noch nicht geprüft werden`)
      else toast.ok('Probleme geprüft')
    } finally {
      checkBtn.disabled = false
      checkBtn.textContent = 'Jetzt prüfen'
    }
  }))
  const problemsCard = card('Probleme', problems)
  const heading = problemsCard.querySelector('h2')
  heading.classList.add('row')
  heading.appendChild(checkBtn)
  page.appendChild(h('div', { class: 'grid c2' },
    card('Als Nächstes geplant', upcoming), problemsCard,
  ))

  // ------------------------------------------------------------ Kanal-Status
  const rows = data.channel_health.map((c) => h('tr', {},
    h('td', {}, h('span', { class: 'row', style: { gap: '6px' } },
      platformIcon(c.platform), h('i', { class: 'dot', style: { background: c.color } }), c.display_name)),
    h('td', { class: 'hint', style: { textTransform: 'uppercase' } }, c.platform),
    h('td', { style: { color: c.health === 'ok' ? 'var(--ok)' : c.health === 'paused' ? 'var(--dim)' : 'var(--err)' } }, c.health),
    h('td', { class: 'hint' }, c.token_expires_at ? fmtDateTime(c.token_expires_at) : '—'),
    h('td', { class: 'hint num' }, `${c.quota_used_today} / ${c.policy ? c.policy.daily_api_quota : '?'}`),
    h('td', { class: 'hint' }, c.last_published_at ? fmtDateTime(c.last_published_at) : '—'),
  ))
  page.appendChild(card('Kanal-Status',
    h('div', { class: 'table-scroll', tabindex: 0, role: 'region', 'aria-label': 'Kanal-Status, seitlich scrollbar' },
      h('table', {},
        h('thead', {}, h('tr', {},
          ...['Kanal', 'Plattform', 'Status', 'Token gültig bis', 'Kontingent heute', 'Zuletzt gepostet']
            .map((t) => h('th', {}, t)))),
        h('tbody', {}, ...rows),
      ),
    ),
  ))

  // ------------------------------------------------------------ Hintergrundjobs
  try {
    const jobs = await api.jobs()
    if (jobs.jobs && jobs.jobs.length) {
      const jrows = jobs.jobs.map((j) => h('tr', {},
        h('td', {}, j.name),
        h('td', { class: 'hint num' }, j.interval_seconds >= 60 ? Math.round(j.interval_seconds / 60) + ' Min.' : j.interval_seconds + ' Sek.'),
        h('td', { class: 'hint num' }, j.runs),
        h('td', { class: 'hint' }, j.last_run ? fmtDateTime(j.last_run) : '—'),
        h('td', { style: { color: j.last_error ? 'var(--err)' : 'var(--dim)' } },
          j.last_error || (j.last_result ? JSON.stringify(j.last_result) : '—')),
        h('td', {}, h('button', {
          class: 'small',
          onClick: guard(async () => { await api.runJob(j.name); toast.ok(j.name + ' ausgeführt'); }),
        }, 'Jetzt')),
      ))
      page.appendChild(card('Hintergrundjobs (' + jobs.mode + ')',
        h('div', { class: 'table-scroll', tabindex: 0, role: 'region', 'aria-label': 'Hintergrundjobs, seitlich scrollbar' },
        h('table', {},
          h('thead', {}, h('tr', {}, ...['Job', 'Intervall', 'Läufe', 'Zuletzt', 'Ergebnis', ''].map((t) => h('th', {}, t)))),
          h('tbody', {}, ...jrows),
        ),
        ),
      ))
    } else if (jobs.note) {
      page.appendChild(card('Hintergrundjobs', h('div', { class: 'hint' }, jobs.note)))
    }
  } catch { /* optional */ }

  return page
}
