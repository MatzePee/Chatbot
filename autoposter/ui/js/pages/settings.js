import { api } from '../api.js?v=creatorstudio-mobile-system-20260909'
import { h, clear, card, field, empty, toast, guard, spinner, confirmDialog } from '../ui.js?v=creatorstudio-mobile-system-20260909'

const MODEL_TASKS = [
  ['openrouter_model_caption', 'Bildunterschriften', 'Texte zu Bildern'],
  ['openrouter_model_text', 'Reine Textposts', 'Posts ohne Bild'],
  ['openrouter_model_vision', 'Bildbeschreibung', 'Muss Bilder verstehen können'],
  ['openrouter_model_fallback', 'Ausweichmodell', 'Wird genutzt, wenn das erste scheitert'],
]

export default async function renderSettings({ user }) {
  const [settings, integrations, report, usage, channels] = await Promise.all([
    api.settings(), api.integrations(), api.maintenanceReport(), api.llmUsage(), api.channels(),
  ])

  const page = h('div', { class: 'page stack' },
    h('h1', {}, 'AutoPost-Einstellungen'),
  )

  // ------------------------------------------------------------ Betrieb
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

  page.appendChild(card('Betrieb',
    h('div', { class: 'col' },
      h('div', { class: 'row' }, dryBtn, h('span', { class: 'hint' },
        'Im Trockenlauf wird alles verarbeitet, aber nichts an X oder Fanvue gesendet.')),
      h('div', { class: 'row' }, pauseBtn, h('span', { class: 'hint' },
        'Stoppt sofort jede Veröffentlichung und jede automatische Planung.')),
    ),
  ))

  page.appendChild(await imageCommentCard())

  // ------------------------------------------------------------ OpenRouter
  page.appendChild(await openrouterCard(integrations))

  // ------------------------------------------------------------ Plattformen
  // Zugangsdaten (Client-ID/Secret) werden bewusst NUR unter „Kanäle" gepflegt.
  // Hier stehen nur Werte, die für alle Kanäle einer Plattform gleich sind.
  page.appendChild(platformDefaultsCard(integrations))

  // ------------------------------------------------------------ Verbindungen prüfen
  page.appendChild(await connectionCard(channels))

  // ------------------------------------------------------------ Planung
  page.appendChild(planningCard(integrations, channels))

  // ------------------------------------------------------------ Kosten
  const pct = usage.budget_usd ? Math.min(100, (usage.total_usd / usage.budget_usd) * 100) : 0
  page.appendChild(card('KI-Kosten (laufender Monat)',
    h('div', { class: 'row', style: { alignItems: 'baseline' } },
      h('span', { class: 'big num' }, Number(usage.total_usd).toFixed(2)),
      h('span', { class: 'hint' }, `von ${Number(usage.budget_usd).toFixed(2)} USD Budget`)),
    h('div', { class: 'bar', style: { margin: '8px 0' } }, h('i', { style: { width: pct + '%' } })),
    h('div', { class: 'hint' }, ...(usage.by_task || []).map((t) =>
      h('div', {}, `${t.task}: ${t.calls} Aufrufe · ${Number(t.cost_usd).toFixed(4)} USD`))),
  ))

  // ------------------------------------------------------------ Wartung
  const stats = [
    ['Nicht zugeordnet', report.unassigned],
    ['Ohne Tags', report.untagged],
    ['Archiv-Kandidaten', report.archive_candidates],
    ['Fehlerhaft', report.failed_assets],
    ['Verwaiste Set-Einträge', report.orphan_set_items],
    ['Fast identische Paare', (report.near_duplicates || []).length],
  ]
  page.appendChild(card('Bibliothek aufräumen',
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
  ))

  // ------------------------------------------------------------ Kalender leeren
  page.appendChild(plannedPostsCard())

  // ------------------------------------------------------------ System
  page.appendChild(card('System',
    h('table', {}, h('tbody', {}, ...[
      ['Port', settings.port],
      ['Datenbank', settings.database],
      ['Hintergrundjobs', settings.scheduler_mode],
      ['Anmeldung', user.role === 'admin' && user.email === 'system@local' ? 'deaktiviert (internes Netz)' : 'aktiv'],
      ['Basis-URL', integrations.public_base_url],
    ].map(([k, v]) => h('tr', {},
      h('td', { class: 'hint', style: { width: '40%' } }, k),
      h('td', {}, String(v ?? '—'))))))))

  return page
}


// --------------------------------------------------------------------------- //
async function openrouterCard(integrations) {
  const keyInput = h('input', {
    type: 'password',
    placeholder: integrations.openrouter_api_key_set ? '•••••••• (gespeichert)' : 'sk-or-v1-…',
    autocomplete: 'off',
  })
  const status = h('div', {})
  const modelSelects = {}
  const filterFree = h('input', { type: 'checkbox' })
  let allModels = []

  const paintStatus = (ok, text) => {
    clear(status).appendChild(h('div', { class: ok ? 'okbox' : 'warnbox' }, text))
  }
  paintStatus(
    integrations.openrouter_ready,
    integrations.openrouter_ready
      ? 'Verbunden. Schlüssel ist hinterlegt.'
      : 'Noch kein API-Schlüssel hinterlegt – Textgenerierung ist deaktiviert.',
  )

  // ---- Modellliste ----
  const modelBox = h('div', { class: 'grid c2' })
  const fillSelects = () => {
    const visible = filterFree.checked ? allModels.filter((m) => m.free) : allModels
    for (const [key, label, hint] of MODEL_TASKS) {
      const sel = modelSelects[key]
      const chosen = sel.value || integrations[key] || ''
      clear(sel)
      // Aktuellen Wert immer anbieten, auch wenn er im Filter fehlt.
      if (chosen && !visible.some((m) => m.id === chosen)) {
        sel.appendChild(h('option', { value: chosen, selected: true }, chosen + ' (aktuell)'))
      }
      const pool = key === 'openrouter_model_vision' ? visible.filter((m) => m.vision) : visible
      for (const m of pool) {
        const price = m.prompt_price && Number(m.prompt_price) > 0
          ? ` · ${(Number(m.prompt_price) * 1e6).toFixed(2)} $/M`
          : m.free ? ' · kostenlos' : ''
        sel.appendChild(h('option', { value: m.id, selected: m.id === chosen }, m.id + price))
      }
      if (!pool.length && !chosen) sel.appendChild(h('option', { value: '' }, '(keine Modelle geladen)'))
    }
  }

  for (const [key, label, hint] of MODEL_TASKS) {
    modelSelects[key] = h('select', {})
    modelBox.appendChild(field(label, modelSelects[key], hint))
  }

  const loadModels = guard(async (refresh = false) => {
    const r = await api.openrouterModels(refresh)
    if (r.error) { toast.error(r.error); return }
    allModels = r.models || []
    fillSelects()
    toast.info(`${allModels.length} Modelle geladen${r.cached ? ' (zwischengespeichert)' : ''}`)
  })
  filterFree.addEventListener('change', fillSelects)

  // Beim Aufbau einmal ohne Meldung laden
  try {
    const r = await api.openrouterModels(false)
    allModels = r.models || []
  } catch { /* egal */ }
  fillSelects()

  const budget = h('input', { type: 'number', step: '1', value: integrations.openrouter_monthly_budget_usd })

  const save = guard(async () => {
    const payload = { openrouter_monthly_budget_usd: Number(budget.value) }
    if (keyInput.value.trim()) payload.openrouter_api_key = keyInput.value.trim()
    for (const [key] of MODEL_TASKS) payload[key] = modelSelects[key].value
    const updated = await api.saveIntegrations(payload)
    Object.assign(integrations, updated)
    keyInput.value = ''
    keyInput.placeholder = '•••••••• (gespeichert)'
    toast.ok('OpenRouter gespeichert')
    paintStatus(true, 'Gespeichert. Mit „Verbindung testen" prüfen, ob alles passt.')
  })

  const test = guard(async () => {
    const r = await api.openrouterTest(modelSelects.openrouter_model_text.value)
    paintStatus(r.ok, r.message)
    r.ok ? toast.ok(r.message) : toast.error(r.message)
  })

  return card('OpenRouter verbinden',
    status,
    h('div', { style: { height: '10px' } }),
    field('API-Schlüssel', keyInput,
      'Zu finden unter openrouter.ai → Keys. Wird verschlüsselt gespeichert und nie wieder angezeigt.'),
    h('div', { class: 'row', style: { marginBottom: '12px' } },
      h('button', { class: 'primary', onClick: save }, 'Speichern'),
      h('button', { onClick: test }, 'Verbindung testen'),
      h('button', { onClick: () => loadModels(true) }, 'Modellliste aktualisieren'),
      integrations.openrouter_api_key_set
        ? h('button', { class: 'danger', onClick: guard(async () => {
            if (!(await confirmDialog('Schlüssel entfernen', 'API-Schlüssel wirklich löschen?', { okLabel: 'Löschen', danger: true }))) return
            await api.clearSecret('openrouter_api_key')
            toast.ok('Schlüssel entfernt'); location.reload()
          }) }, 'Schlüssel entfernen')
        : null,
    ),
    h('label', { class: 'inline', style: { marginBottom: '10px' } }, filterFree, 'Nur kostenlose Modelle anzeigen'),
    modelBox,
    field('Monatsbudget (USD)', budget, 'Ist es aufgebraucht, stoppt die Textgenerierung automatisch.'),
  )
}

// --------------------------------------------------------------------------- //
async function connectionCard(channels) {
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
      channel.platform === 'fanvue' ? 'Gemeinsame Verbindung · AutoChat' : channel.uses_own_app
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
      h('a', { class: 'btn', href: '#/channels' }, 'Kanäle verwalten →'),
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
function plannedPostsCard() {
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

function platformDefaultsCard(integrations) {
  const baseUrl = h('input', { type: 'url', value: integrations.public_base_url || '' })
  const fanvueVersion = h('code', {}, integrations.fanvue_api_version || '—')
  const xTier = h('select', {}, ...['free', 'basic', 'pro'].map((t) =>
    h('option', { value: t, selected: t === integrations.x_api_tier }, t)))

  const save = guard(async () => {
    const updated = await api.saveIntegrations({
      x_api_tier: xTier.value,
      public_base_url: baseUrl.value.trim(),
    })
    Object.assign(integrations, updated)
    toast.ok('Gespeichert')
  })

  return card('Plattform-Vorgaben',
    h('div', { class: 'okbox', style: { marginBottom: '12px' } },
      'Fanvue verwendet automatisch die gemeinsame Verbindung von MP CreatorStudio. ' +
      'Nur X benötigt weiterhin eigene Zugangsdaten pro Kanal.'),
    h('div', { class: 'grid c2' },
      h('div', {}, h('div', { class: 'hint' }, 'Fanvue · gemeinsame Verbindung'), fanvueVersion, h('p', {}, h('a', { href: '/settings/shared#fanvue' }, 'Gemeinsame Fanvue-Verbindung →'))),
      field('X API-Tarif', xTier, 'Steuert das voreingestellte Tageskontingent neuer Kanäle.'),
    ),
    h('details', { style: { marginBottom: '12px' } },
      h('summary', {}, 'Erweitert: Standardadresse für Verbindungen'),
      field('Öffentliche Basis-URL', baseUrl,
        'Rückfalladresse für Kanäle ohne eigene Callback-Adresse. Eine unter Kanäle eingetragene ' +
        'Callback-Adresse hat Vorrang. Fanvue verwendet die gemeinsame Verbindung. ' +
        'Diese Adresse beeinflusst die Planung nicht.'),
    ),
    h('div', { class: 'row' },
      h('button', { class: 'primary', onClick: save }, 'Speichern'),
      h('a', { class: 'btn', href: '#/channels' }, 'Zugangsdaten pflegen → Kanäle'),
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
  const autoEvery = h('input', {
    type: 'number', min: '1', max: '168',
    value: integrations.auto_plan_interval_hours ?? 24,
    disabled: !integrations.auto_plan_enabled,
  })
  autoPlan.addEventListener('change', () => {
    autoDays.disabled = !autoPlan.checked
    autoEvery.disabled = !autoPlan.checked
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
      }
    }))
    return h('label', { class: 'inline', style: { padding: '10px 0', alignItems: 'center' } }, toggle,
      h('span', {}, h('b', {}, channel.display_name),
        h('span', { class: 'hint', style: { display: 'block' } },
          `${channel.platform === 'x' ? 'X' : 'Fanvue'}${channel.handle ? ' · ' + channel.handle : ''}`,
          !channel.is_active ? ' · Kanal pausiert – wird nicht befüllt' : '')))
  })

  const save = guard(async () => {
    const updated = await api.saveIntegrations({
      cross_channel_min_gap_minutes: Number(gap.value),
      inventory_warn_days: Number(warn.value),
      auto_plan_enabled: autoPlan.checked,
      auto_plan_days: Number(autoDays.value) || 14,
      auto_plan_interval_hours: Number(autoEvery.value) || 24,
    })
    Object.assign(integrations, updated)
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
        h('span', { class: 'hint' }, 'Läuft alle'),
        h('div', { style: { width: '80px' } }, autoEvery),
        h('span', { class: 'hint' }, 'Stunden · plant voraus für'),
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
