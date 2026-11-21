import { postRetentionCard } from './post-retention.js?v=instagram-ai-20261121-1'
import { visibilityAgingCard } from './visibility-aging.js?v=instagram-ai-20261121-1'
import { api } from '../api.js?v=instagram-ai-20261121-1'
import { h, clear, card, field, empty, toast, guard, spinner, confirmDialog } from '../ui.js?v=instagram-ai-20261121-1'

export async function commonOverview() {
  const response = await fetch(window.CreatorStudioCharacters.url('/settings/shared/ai-usage'))
  if (!response.ok) throw new Error('Kostenübersicht nicht verfügbar')
  const usage = await response.json()
  const page = h('div', {class:'stack'})
  page.appendChild(card('Erfasste AutoPost-Kosten · laufender Monat',
    h('div', {class:'big num'}, Number(usage.total_usd).toFixed(2) + ' USD'),
    h('p', {class:'hint'}, 'Gesamtverbrauch aller Charaktere. Erfasst sind AutoPost-Aufrufe.'),
    h('p', {class:'hint'}, 'AutoPost-Budget je Charakter: ' + Number(usage.budget_usd).toFixed(2) + ' USD'),
    h('div', {}, ...(usage.by_character || []).map(row => h('p', {}, row.name + ': ' + Number(row.cost_usd).toFixed(2) + ' USD'))),
    h('div', {class:'hint'}, ...(usage.by_task || []).map(row => h('p', {}, row.task + ': ' + row.calls + ' Aufrufe · ' + Number(row.cost_usd).toFixed(4) + ' USD')))))
  return page
}

export async function systemOverview() {
  const [settings, integrations, user] = await Promise.all([api.settings(), api.integrations(), api.me()])
  const rows = [
    ['Port', settings.port], ['Datenbank', settings.database], ['Hintergrundjobs', settings.scheduler_mode],
    ['Anmeldung', user.email === 'system@local' ? 'Internes Netz' : 'Aktiv'], ['Basis-URL', integrations.public_base_url],
  ].map(([key,value]) => h('tr', {}, h('td', {class:'hint'}, key), h('td', {}, String(value ?? '—'))))
  return card('Installation', h('table', {}, h('tbody', {}, ...rows)))
}

export default async function renderSettings({ user }) {
  const [integrations, channels] = await Promise.all([api.integrations(), api.channels()])

  const page = h('div', { class: 'page stack' },
    h('h1', {}, 'AutoPost-Einstellungen'),
  )

  // ------------------------------------------------------------ Betrieb
  page.appendChild(h('a', {class:'btn', href:'/autoposter/'}, 'Betriebssteuerung im Dashboard öffnen'))

  const group = (title, ...children) => h('section', {class:'settings-subsection'}, h('h2', {}, title), ...children)
  page.appendChild(group('Automatische Planung', planningCard(integrations, channels)))
  page.appendChild(group('Veröffentlichung & Kommentare', await imageCommentCard()))
  page.appendChild(group('Sichtbarkeit & Aufbewahrung', await visibilityAgingCard(integrations), await postRetentionCard(integrations)))
  page.appendChild(group('Erweiterte Vorgaben', platformDefaultsCard(integrations)))

  // ------------------------------------------------------------ OpenRouter
  page.appendChild(h('a', { href: '/settings/shared?view=ai' }, 'KI-Zugang, Aufgabenmodelle und Budget öffnen'))

  // ------------------------------------------------------------ Plattformen
  // Zugangsdaten (Client-ID/Secret) werden bewusst NUR unter „Kanäle" gepflegt.
  // Hier stehen nur Werte, die für alle Kanäle einer Plattform gleich sind.


  // ------------------------------------------------------------ Verbindungen prüfen


  // ------------------------------------------------------------ Planung


  // ------------------------------------------------------------ Wartung


  return page
}


// --------------------------------------------------------------------------- //
export async function connectionCard(channels) {
  const results = h('div', { class: 'col' })

  /** Eine Zeile je Kanal. Jede Zeile hält ihren eigenen Container, damit ein
   *  Test nur die eigene Anzeige austauscht und nicht die der Nachbarn. */
  function channelRow(channel) {
    const container = h('div', { class: 'card pad', style: { padding: '10px 12px' } })
    const statusBox = h('div', { style: { marginTop: '6px', fontSize: '12px' } })
    const warnBox = h('div', {})
    const btn = h('button', { class: 'small' }, 'Testen')

    const paint = (result) => {
      clear(statusBox); clear(warnBox)
      if (!result) {
        statusBox.className = 'hint'
        statusBox.textContent = channel.is_connected
          ? 'verbunden, noch nicht geprüft'
          : 'nicht verbunden'
      } else {
        statusBox.className = result.ok ? 'okbox' : 'errbox'
        statusBox.textContent = result.message
        if (result.account) statusBox.textContent += ` (@${result.account})`
        if (result.warning) {
          warnBox.appendChild(h('div', { class: 'warnbox', style: { marginTop: '6px' } }, result.warning))
        }
      }
    }

    btn.addEventListener('click', guard(async () => {
      btn.textContent = '…'; btn.disabled = true
      try {
        const result = await api.testChannel(channel.id)
        paint(result)
        result.ok
          ? toast.ok(`${channel.display_name}: ${result.message}`)
          : toast.error(`${channel.display_name}: ${result.message}`)
      } finally {
        btn.textContent = 'Testen'; btn.disabled = false
      }
    }))

    container.appendChild(h('div', { class: 'row' },
      h('i', { class: 'dot', style: { background: channel.color } }),
      h('b', { style: { flex: '1' } }, channel.display_name),
      h('span', { class: 'hint', style: { textTransform: 'uppercase' } }, channel.platform),
      btn,
    ))
    container.appendChild(h('div', { class: 'hint', style: { fontSize: '11px', marginTop: '4px' } },
      channel.platform === 'instagram' ? (channel.instagram_provider === 'upload_post' ? 'Instagram über Upload-Post · Direktupload' : 'Instagram über Buffer · FTPS-Webspace') : channel.platform === 'telegram' ? 'Eigener Telegram-Publikationsbot' : channel.platform === 'fanvue' ? 'Verbindung dieses Charakters · AutoChat' : channel.uses_own_app
        ? 'eigene App' + (channel.oauth_app_name ? ` „${channel.oauth_app_name}"` : '')
        : 'globale App',
      channel.oauth_client_id ? ` · ${channel.oauth_client_id.slice(0, 14)}…` : '',
    ))
    container.appendChild(statusBox)
    container.appendChild(warnBox)
    paint(null)

    return { container, paint }
  }

  const rows = new Map()
  if (!channels.length) {
    results.appendChild(empty('Noch keine Kanäle angelegt.'))
  } else {
    for (const channel of channels) {
      const row = channelRow(channel)
      rows.set(channel.id, row)
      results.appendChild(row.container)
    }
  }

  const testAll = h('button', { class: 'primary' }, 'Alle Verbindungen testen')
  testAll.addEventListener('click', guard(async () => {
    clear(testAll).appendChild(spinner()); testAll.disabled = true
    try {
      const response = await api.testAllChannels()
      for (const result of response.results) {
        const row = rows.get(result.channel_id)
        if (row) row.paint(result)
      }
      response.ok_count === response.total
        ? toast.ok(`Alle ${response.total} Kanäle in Ordnung`)
        : toast.error(`${response.total - response.ok_count} von ${response.total} Kanälen mit Problem`)
    } finally {
      clear(testAll).appendChild(document.createTextNode('Alle Verbindungen testen'))
      testAll.disabled = false
    }
  }))

  return card('Verbindungen prüfen',
    h('p', { class: 'hint', style: { marginTop: 0 } },
      'Der Test ruft den tatsächlich verbundenen Account ab. Bei X zeigt sich dabei sofort, ' +
      'ob eine App auf das falsche Profil zeigt — zu jeder X-App gehört genau ein Account.'),
    h('div', { class: 'row', style: { marginBottom: '12px' } },
      testAll,
      h('a', { class: 'btn', href: '/settings/shared?section=channels' }, 'Kanäle verwalten →'),
    ),
    results,
  )
}


// --------------------------------------------------------------------------- //
/** Nur plattformweite Werte – KEINE Zugangsdaten. Die gehören zum Kanal. */
/**
 * Kalender leerräumen. Zwei Schritte mit Absicht: erst zählen lassen, dann mit
 * der konkreten Zahl im Rückfragedialog bestätigen. Veröffentlichte Posts sind
 * ausgenommen – sie sind die Grundlage der Verbrauchsrechnung.
 */
export function plannedPostsCard() {
  const onlyFuture = h('input', { type: 'checkbox' })
  const info = h('div', { class: 'col', style: { gap: '6px' } })

  const showBreakdown = (result) => {
    clear(info)
    const entries = Object.entries(result.by_channel || {})
    if (!entries.length) {
      info.appendChild(h('div', { class: 'okbox' }, 'Es gibt keine geplanten Posts.'))
      return
    }
    info.appendChild(h('div', { class: 'hint' },
      entries.map(([name, count]) => `${name}: ${count}`).join(' · ')))
  }

  const button = h('button', { class: 'danger' }, 'Alle geplanten Posts löschen')
  button.addEventListener('click', guard(async () => {
    clear(button).appendChild(spinner()); button.disabled = true
    let preview
    try {
      preview = await api.deletePlanned({ dryRun: true, onlyFuture: onlyFuture.checked })
    } finally {
      clear(button).appendChild(document.createTextNode('Alle geplanten Posts löschen'))
      button.disabled = false
    }
    showBreakdown(preview)
    if (!preview.would_delete) { toast.info('Es gibt nichts zu löschen'); return }

    const detail = Object.entries(preview.by_channel)
      .map(([name, count]) => `${name}: ${count}`).join(', ')
    const ok = await confirmDialog(
      'Geplante Posts löschen',
      `${preview.would_delete} Posts werden gelöscht (${detail}). `
      + `${preview.kept_published} veröffentlichte Posts bleiben erhalten. `
      + 'Das lässt sich nicht rückgängig machen.',
      { okLabel: `${preview.would_delete} löschen`, danger: true },
    )
    if (!ok) return

    const result = await api.deletePlanned({ dryRun: false, onlyFuture: onlyFuture.checked })
    toast.ok(`${result.deleted} Posts gelöscht, ${result.freed_assets} Bilder wieder frei`)
    showBreakdown({ by_channel: {} })
  }))

  return card('Kalender leeren',
    h('p', { class: 'hint', style: { marginTop: '-4px' } },
      'Löscht Entwürfe, zu prüfende, freigegebene, eingeplante und fehlgeschlagene Posts. ',
      'Veröffentlichte Posts bleiben immer erhalten – sie sind die Grundlage der ',
      'Verbrauchs- und Reichweitenrechnung. Die belegten Bilder werden wieder verfügbar.'),
    h('label', { class: 'inline', style: { marginBottom: '10px' } }, onlyFuture,
      'Nur Posts in der Zukunft (Vergangenes stehen lassen)'),
    info,
    h('div', { class: 'row', style: { marginTop: '10px' } }, button),
  )
}

export function platformDefaultsCard(integrations) {
  const fanvueVersion = h('code', {}, integrations.fanvue_api_version || '—')
  const xTier = h('select', {}, ...['free', 'basic', 'pro'].map((t) =>
    h('option', { value: t, selected: t === integrations.x_api_tier }, t)))

  const save = guard(async () => {
    const updated = await api.saveIntegrations({
      x_api_tier: xTier.value,
    })
    Object.assign(integrations, updated)
    window.CreatorStudioLive?.saved(xTier)
    toast.ok('Gespeichert')
  })

  return card('Plattform-Vorgaben',
    h('div', { class: 'okbox', style: { marginBottom: '12px' } },
      'Fanvue verwendet das verbundene Konto dieses Charakters für AutoChat und AutoPost. ' +
      'Nur X benötigt weiterhin eigene Zugangsdaten pro Kanal.'),
    h('div', { class: 'grid c2' },
      h('div', {}, h('div', { class: 'hint' }, 'Fanvue · Konto dieses Charakters'), fanvueVersion, h('p', {}, h('a', { href: '/settings/shared?section=channels&view=accounts#fanvue' }, 'Fanvue-Konto verwalten →'))),
      field('X API-Tarif', xTier, 'Steuert das voreingestellte Tageskontingent neuer Kanäle.'),
    ),
    h('div', { class: 'row' },
      h('button', { class: 'primary', onClick: save }, 'Speichern'),
      h('a', { class: 'btn', href: '/settings/shared?section=channels' }, 'Zugangsdaten pflegen → Kanäle'),
    ),
  )
}


// --------------------------------------------------------------------------- //
function planningCard(integrations, channels) {
  const gap = h('input', { type: 'number', min: '0', value: integrations.cross_channel_min_gap_minutes ?? 0 })
  const warn = h('input', { type: 'number', min: '1', value: integrations.inventory_warn_days ?? 14 })

  const autoPlan = h('input', { type: 'checkbox', checked: !!integrations.auto_plan_enabled })
  const autoDays = h('input', {
    type: 'number', min: '1', max: '90',
    value: integrations.auto_plan_days ?? 14,
    disabled: !integrations.auto_plan_enabled,
  })
  autoPlan.addEventListener('change', () => {
    autoDays.disabled = !autoPlan.checked
  })

  const channelToggles = channels.map((channel) => {
    const toggle = h('input', {
      type: 'checkbox', checked: channel.auto_plan_enabled !== false,
      'aria-label': `Autobefüllen für ${channel.display_name}`,
    })
    toggle.addEventListener('change', guard(async () => {
      toggle.disabled = true
      try {
        const updated = await api.updateChannel(channel.id, { auto_plan_enabled: toggle.checked })
        Object.assign(channel, updated)
        toast.ok(`Autobefüllen für ${channel.display_name} ${channel.auto_plan_enabled ? 'eingeschaltet' : 'ausgeschaltet'}`)
      } finally {
        toggle.checked = channel.auto_plan_enabled !== false
        toggle.disabled = false
        window.CreatorStudioLive?.saved(toggle)
      }
    }))
    return h('label', { class: 'inline', style: { padding: '10px 0', alignItems: 'center' } }, toggle,
      h('span', {}, h('b', {}, channel.display_name),
        h('span', { class: 'hint', style: { display: 'block' } },
          `${channel.platform === 'instagram' ? 'Instagram' : channel.platform === 'telegram' ? 'Telegram' : channel.platform === 'x' ? 'X' : 'Fanvue'}${channel.handle ? ' · ' + channel.handle : ''}`,
          !channel.is_active ? ' · Kanal pausiert – wird nicht befüllt' : '')))
  })

  const save = guard(async () => {
    const updated = await api.saveIntegrations({
      cross_channel_min_gap_minutes: Number(gap.value),
      inventory_warn_days: Number(warn.value),
      auto_plan_enabled: autoPlan.checked,
      auto_plan_days: Number(autoDays.value) || 14,
    })
    Object.assign(integrations, updated)
    for(const control of [gap,warn,autoPlan,autoDays])window.CreatorStudioLive?.saved(control)
    toast.ok('Gespeichert')
  })

  return card('Planung',
    h('div', { class: 'warnbox', style: { marginBottom: '12px', display: 'block' } },
      h('label', { class: 'inline' }, autoPlan,
        h('b', {}, 'Automatisch nachplanen')),
      h('div', { class: 'hint', style: { marginTop: '4px' } },
        'Ist das an, legt der Server von allein Posts für die kommenden Tage an – ',
        'nach den Regeln des Kanals, nicht nach den Vorgaben aus dem Planungsdialog. ',
        'Wer seinen Kalender selbst plant, lässt das aus.'),
      h('div', { class: 'row', style: { marginTop: '8px', alignItems: 'center', gap: '8px' } },
        h('span', { class: 'hint' }, 'Täglich um 00:00 Uhr (Europe/Berlin, Sommer-/Winterzeit) · plant voraus für'),
        h('div', { style: { width: '80px' } }, autoDays),
        h('span', { class: 'hint' }, 'Tage')),
    ),
    h('div', { style: { marginBottom: '20px' } },
      h('h3', {}, 'Autobefüllen je Kanal'),
      h('p', { class: 'hint' },
        'Die Kanalschalter werden sofort gespeichert. Automatisch befüllt werden nur eingeschaltete, aktive Kanäle, ' +
        'wenn der Hauptschalter „Automatisch nachplanen“ gespeichert und aktiv ist. ' +
        'Bestehende Posts und manuelles Befüllen bleiben unverändert.'),
      h('div', { class: 'grid c2' }, ...channelToggles),
      channels.length ? null : empty('Noch keine Kanäle vorhanden. Lege zuerst unter Kanäle eine Verbindung an.'),
    ),
    h('div', { class: 'grid c2' },
      field('Mindestabstand ZWISCHEN Kanälen (Minuten)', gap,
        '0 = Kanäle dürfen gleichzeitig posten (Standard). Ein Wert größer 0 '
        + 'sperrt die Kanäle gegeneinander und verschiebt damit deren Zeitfenster. '
        + 'Der Mindestabstand INNERHALB eines Kanals steht davon unabhängig '
        + 'unter Kanäle → Einstellungen.'),
      field('Warnung bei Bildvorrat unter (Tagen)', warn),
    ),
    h('button', { class: 'primary', onClick: save }, 'Speichern'),
  )
}


async function imageCommentCard() {
  const config = await api.imageComment()
  const enabled = h('input', { type: 'checkbox', checked: config.enabled })
  const text = h('textarea', { rows: 3, maxlength: 250, value: config.text, placeholder: 'Dein Kommentar unter dem Bild …' })
  const url = h('input', { type: 'url', value: config.url, placeholder: 'https://…', maxlength: 300 })
  const preview = h('div', { style: { whiteSpace: 'pre-wrap', padding: '12px', background: 'var(--bg-2)', borderRadius: '12px' } })
  const paint = () => { preview.textContent = [text.value.trim(), url.value.trim()].filter(Boolean).join('\n') || 'Hier erscheint dein Kommentar.' }
  text.addEventListener('input', paint); url.addEventListener('input', paint); paint()
  const box = card('Auto-Kommentar nach X-Bildposts',
    h('label', { class: 'inline' }, enabled, 'Nach neuen X-Bildposts automatisch kommentieren'),
    h('p', { class: 'hint' }, 'Text und Link werden als direkte Antwort unter dem Bild veröffentlicht. Gilt für alle X-Kanäle, einmal pro Bildpost oder Bilderset. Fanvue und Videos erhalten keinen Kommentar. Im Mitlesemodus und Trockenlauf wird nichts gesendet.'),
    field('Kommentartext', text), field('Link', url),
    h('div', { class: 'hint' }, 'Vorschau · Text und Link müssen zusammen in das X-Zeichenlimit passen.'), preview,
    h('button', { class: 'primary', onClick: guard(async () => {
      await api.saveImageComment({ enabled: enabled.checked, text: text.value, url: url.value })
      window.CreatorStudioLive?.saved(box)
      toast.ok('X-Kommentar gespeichert')
    }) }, 'Kommentar speichern'),
  )
  return box
}

export async function runtimeCard() {
  const settings = await api.settings()
  const dryBtn = h('button', {})
  const pauseBtn = h('button', {})
  const paint = () => {
    clear(dryBtn).appendChild(document.createTextNode('Trockenlauf ' + (settings.dry_run ? 'AN' : 'AUS')))
    dryBtn.className = settings.dry_run ? 'primary' : ''
    clear(pauseBtn).appendChild(document.createTextNode('⛔ Notfall-Stopp ' + (settings.global_pause ? 'AKTIV' : 'aus')))
    pauseBtn.className = settings.global_pause ? 'danger' : ''
  }
  paint()
  dryBtn.addEventListener('click', guard(async () => {
    Object.assign(settings, await api.setRuntime({ dry_run: !settings.dry_run })); paint()
    toast.ok(settings.dry_run ? 'Trockenlauf aktiv – es wird nichts gesendet' : 'Trockenlauf aus – Posts gehen wirklich raus')
  }))
  pauseBtn.addEventListener('click', guard(async () => {
    Object.assign(settings, await api.setRuntime({ global_pause: !settings.global_pause })); paint()
  }))

  return card('Veröffentlichung',
    h('div', { class: 'col' },
      h('div', { class: 'row' }, dryBtn, h('span', { class: 'hint' },
        'Im Trockenlauf wird alles verarbeitet, aber nichts an X oder Fanvue gesendet.')),
      h('div', { class: 'row' }, pauseBtn, h('span', { class: 'hint' },
        'Stoppt sofort jede Veröffentlichung und jede automatische Planung.')),
    ),
  )

}

export async function libraryCleanupCard() {
  const report = await api.maintenanceReport()
  const stats = [
    ['Nicht zugeordnet', report.unassigned],
    ['Ohne Tags', report.untagged],
    ['Archiv-Kandidaten', report.archive_candidates],
    ['Fehlerhaft', report.failed_assets],
    ['Verwaiste Set-Einträge', report.orphan_set_items],
    ['Fast identische Paare', (report.near_duplicates || []).length],
  ]
  return card('Bibliothek aufräumen',
    h('div', { class: 'grid c3' }, ...stats.map(([label, value]) =>
      h('div', { class: 'card pad' }, h('div', { class: 'hint' }, label), h('div', { class: 'big num' }, value)))),
    h('div', { class: 'row', style: { marginTop: '12px' } },
      h('button', { onClick: guard(async () => {
        const r = await api.archiveUsed(30); toast.ok(`${r.archived} Bilder archiviert`); location.reload()
      }) }, 'Verbrauchte (>30 Tage) archivieren'),
      h('button', { onClick: guard(async () => {
        const r = await api.rebuildCounters(); toast.ok(`${r.refreshed} Zähler neu berechnet`); location.reload()
      }) }, 'Zähler neu berechnen'),
    ),
  )
}
