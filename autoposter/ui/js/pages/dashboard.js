import { api } from '../api.js?v=creatorstudio-layout-20260909'
import { h, card, empty, toast, fmtDate, fmtDateTime, LIFECYCLE, LIFECYCLE_ORDER, spinner, clear, guard, append, confirmDialog } from '../ui.js?v=creatorstudio-layout-20260909'

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

  // Nach Änderungen an der .env und wenn ein Job hängt – erspart die
  // SSH-Sitzung auf dem Server.
  const restartBtn = h('button', { title: 'Dienst neu starten' }, 'Neu starten')
  restartBtn.addEventListener('click', guard(async () => {
    const ok = await confirmDialog('Dienst neu starten',
      'Der Dienst startet neu und ist dabei einige Sekunden nicht erreichbar. '
      + 'Bereits geplante Posts bleiben erhalten; ein gerade laufender '
      + 'Veröffentlichungsvorgang wird sauber zu Ende geführt.',
      { okLabel: 'Neu starten', danger: true })
    if (!ok) return
    clear(restartBtn).appendChild(spinner())
    append(restartBtn, [' starte neu …'])
    restartBtn.disabled = true
    try {
      const r = await api.restartService()
      toast.ok(r.message || 'Neustart angestoßen')
      // Erst laden, wenn der Dienst wieder da sein kann – sonst zeigt der
      // Browser einen Verbindungsfehler und es sieht kaputt aus.
      setTimeout(() => location.reload(), 12000)
    } catch (err) {
      restartBtn.disabled = false
      clear(restartBtn).appendChild(document.createTextNode('Neu starten'))
      throw err
    }
  }))

  page.appendChild(h('div', { class: 'page-head' },
    h('h1', {}, 'Übersicht'),
    h('div', { class: 'spacer' }),
    h('button', { onClick: () => location.reload() }, 'Aktualisieren'),
    fillBtn,
  ))

  // ------------------------------------------------------------ Reichweite
  const inv = h('div', { class: 'grid c3' })
  for (const e of data.inventory.channels) {
    inv.appendChild(h('div', { class: 'card pad' },
      h('div', { class: 'row', style: { marginBottom: '8px' } },
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
  if (!data.upcoming_posts.length) upcoming.appendChild(empty('Nichts eingeplant.'))
  for (const p of data.upcoming_posts) {
    const ch = data.channel_health.find((c) => c.id === p.channel_id)
    upcoming.appendChild(h('div', { class: 'row', style: { fontSize: '12px', border: '1px solid var(--line)', borderRadius: '8px', padding: '5px 8px' } },
      h('i', { class: 'dot', style: { background: ch ? ch.color : '#666' } }),
      h('span', { class: 'hint num' }, fmtDateTime(p.scheduled_at)),
      h('span', { style: { flex: '1', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } },
        p.body_text || '(kein Text)'),
      h('span', { class: 'hint' }, p.status),
    ))
  }

  const problems = h('div', { class: 'col' })
  if (!data.notifications.length && !data.recent_failures.length) problems.appendChild(empty('Alles ruhig.'))
  for (const n of data.notifications) {
    problems.appendChild(h('div', { class: n.level === 'error' ? 'errbox' : 'warnbox' },
      h('div', {}, h('b', {}, n.title), h('div', { class: 'hint' }, n.body)),
    ))
  }
  for (const p of data.recent_failures) {
    problems.appendChild(h('div', { class: 'errbox' },
      h('b', {}, 'Veröffentlichung fehlgeschlagen'),
      h('div', { class: 'hint' }, p.error_message),
    ))
  }

  page.appendChild(h('div', { class: 'grid c2' },
    card('Als Nächstes geplant', upcoming),
    card('Probleme', problems),
  ))

  // ------------------------------------------------------------ Kanal-Status
  const rows = data.channel_health.map((c) => h('tr', {},
    h('td', {}, h('span', { class: 'row', style: { gap: '6px' } },
      h('i', { class: 'dot', style: { background: c.color } }), c.display_name)),
    h('td', { class: 'hint', style: { textTransform: 'uppercase' } }, c.platform),
    h('td', { style: { color: c.health === 'ok' ? 'var(--ok)' : c.health === 'paused' ? 'var(--dim)' : 'var(--err)' } }, c.health),
    h('td', { class: 'hint' }, c.token_expires_at ? fmtDateTime(c.token_expires_at) : '—'),
    h('td', { class: 'hint num' }, `${c.quota_used_today} / ${c.policy ? c.policy.daily_api_quota : '?'}`),
    h('td', { class: 'hint' }, c.last_published_at ? fmtDateTime(c.last_published_at) : '—'),
  ))
  page.appendChild(card('Kanal-Status',
    h('div', { style: { overflowX: 'auto' } },
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
        h('table', {},
          h('thead', {}, h('tr', {}, ...['Job', 'Intervall', 'Läufe', 'Zuletzt', 'Ergebnis', ''].map((t) => h('th', {}, t)))),
          h('tbody', {}, ...jrows),
        ),
      ))
    } else if (jobs.note) {
      page.appendChild(card('Hintergrundjobs', h('div', { class: 'hint' }, jobs.note)))
    }
  } catch { /* optional */ }

  return page
}
