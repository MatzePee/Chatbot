import { api } from '../api.js?v=creatorstudio-mobile-system-20260909'
import { watermarkEditor } from '../watermark.js?v=creatorstudio-mobile-system-20260909'
import { h, clear, empty, field, modal, toast, guard, fmtDateTime } from '../ui.js?v=creatorstudio-mobile-system-20260909'

export default async function renderChannels() {
  let [channels, personas] = await Promise.all([api.channels(), api.personas()])
  const list = h('div', { class: 'grid c3' })

  const reload = () => { location.hash = '#/channels'; location.reload() }

  function draw() {
    clear(list)
    if (!channels.length) {
      list.appendChild(empty('Noch keine Kanäle. Lege einen X- oder Fanvue-Kanal an.'))
      return
    }
    for (const c of channels) {
      list.appendChild(h('div', { class: 'card pad col' },
        h('div', { class: 'row' },
          h('i', { class: 'dot', style: { background: c.color, width: '11px', height: '11px' } }),
          h('b', { style: { flex: '1' } }, c.display_name),
          h('span', { class: 'hint', style: { textTransform: 'uppercase' } }, c.platform),
        ),
        h('div', { class: 'hint' }, c.handle),
        h('div', { class: 'row', style: { fontSize: '12px' } },
          h('span', { style: { color: c.health === 'ok' ? 'var(--ok)' : c.health === 'paused' ? 'var(--dim)' : 'var(--err)' } },
            '● ' + c.health),
          c.token_expires_at ? h('span', { class: 'hint' }, 'Token bis ' + fmtDateTime(c.token_expires_at)) : null,
        ),
        c.health_note ? h('div', { style: { fontSize: '11px', color: 'var(--warn)' } }, c.health_note) : null,
        h('div', { class: 'hint', style: { fontSize: '11px' } },
          c.platform === 'fanvue' ? 'Gemeinsame Verbindung · AutoChat' : c.uses_own_app
            ? `Eigene App${c.oauth_app_name ? ' „' + c.oauth_app_name + '"' : ''} · ${c.oauth_client_id.slice(0, 12)}…`
            : (c.app_configured ? 'Globale App aus den Einstellungen' : 'Keine App hinterlegt')),
        // Eine Client-ID ohne Secret ist der häufigste stille Fehler: Der Kanal
        // sieht eingerichtet aus, der Token-Tausch scheitert aber mit
        // „invalid_client", weil gar keine Anmeldung mitgeht.
        c.uses_own_app && !c.has_client_secret
          ? h('div', { style: { fontSize: '11px', color: 'var(--err)' } },
              '⚠ Client-Secret fehlt — Verbinden wird mit „invalid_client" scheitern.')
          : null,
        c.verified_account
          ? h('div', { style: { fontSize: '11px', color: 'var(--ok)' } },
              `Zuletzt geprüft: @${c.verified_account}` +
              (c.last_verified_at ? ' · ' + fmtDateTime(c.last_verified_at) : ''))
          : null,
        h('div', { class: 'hint', style: { fontSize: '11px' } },
          `${c.policy ? c.policy.posts_per_day : '?'}/Tag · NSFW max ${c.nsfw_level} · ` +
          `${c.quota_used_today}/${c.policy ? c.policy.daily_api_quota : '?'} heute` +
          (c.platform === 'fanvue' && c.platform_side_scheduling ? ' · plattformseitige Planung' : '')),
        h('div', { class: 'row' },
          c.platform === 'fanvue'
            ? h('a', { class: 'btn small', href: '/settings/shared#fanvue' }, 'Gemeinsame Verbindung')
            : c.is_connected
            ? h('button', { class: 'small', onClick: guard(async () => { await api.disconnect(c.id); toast.ok('Getrennt'); reload() }) }, 'Trennen')
            : h('button', {
                class: 'small primary',
                onClick: guard(async () => {
                  const { authorize_url } = await api.oauthStart(c.platform, c.id)
                  window.open(authorize_url, '_blank', 'width=700,height=850')
                  toast.info('Autorisierung im neuen Fenster abschließen, dann Seite neu laden.')
                }),
              }, 'Verbinden'),
          h('button', {
            class: 'small',
            onClick: guard(async () => { await api.pauseChannel(c.id, c.is_active); toast.ok('Status geändert'); reload() }),
          }, c.is_active ? 'Pausieren' : 'Fortsetzen'),
          c.platform !== 'fanvue' ? h('button', { class: 'small', onClick: () => openApp(c) }, 'API-Zugang') : null,
          h('button', { class: 'small', onClick: (e) => runTest(c, e.target) }, 'Testen'),
          h('button', { class: 'small', onClick: () => openPolicy(c) }, 'Einstellungen'),
          h('div', { class: 'spacer' }),
          h('button', {
            class: 'small danger', title: 'Kanal endgültig löschen',
            onClick: () => openDelete(c),
          }, 'Löschen'),
        ),
      ))
    }
  }

  // ------------------------------------------------------------ Verbindungstest
  const runTest = guard(async (channel, btn) => {
    const label = btn.textContent
    btn.textContent = '…'; btn.disabled = true
    try {
      const r = await api.testChannel(channel.id)
      modal(`Verbindungstest — ${r.channel_name}`, () =>
        h('div', { class: 'col' },
          h('div', { class: r.ok ? 'okbox' : 'errbox' }, r.message),
          r.warning ? h('div', { class: 'warnbox' }, r.warning) : null,
          h('table', {}, h('tbody', {}, ...[
            ['Plattform', r.platform],
            ['App', r.uses_own_app ? 'eigene App' + (r.app_name ? ` (${r.app_name})` : '') : 'globale App'],
            ['Account', r.account ? '@' + r.account : '—'],
            ['Account-ID', r.account_id || '—'],
            ['Scopes', (r.scopes || []).join(' ') || '—'],
            ['Token gültig bis', r.token_expires_at ? fmtDateTime(r.token_expires_at) : '—'],
            ['Redirect-URI', r.redirect_uri],
          ].map(([k, v]) => h('tr', {},
            h('td', { class: 'hint', style: { width: '35%' } }, k),
            h('td', { style: { wordBreak: 'break-all' } }, String(v)))))),
          channel.platform !== 'fanvue' && !r.ok && r.step === 'oauth'
            ? h('button', { class: 'primary', onClick: guard(async () => {
                const { authorize_url } = await api.oauthStart(channel.platform, channel.id)
                window.open(authorize_url, '_blank', 'width=700,height=850')
              }) }, 'Jetzt verbinden')
            : null,
        ))
      r.ok ? toast.ok(r.message) : toast.error(r.message)
      if (r.ok) reload()
    } finally {
      btn.textContent = label; btn.disabled = false
    }
  })

  // ------------------------------------------------------------ API-Zugang
  function openApp(channel) {
    if (channel.platform === 'fanvue') return;
    modal(`API-Zugang — ${channel.display_name}`, (close) => {
      const clientId = h('input', { value: channel.oauth_client_id || '', autocomplete: 'off' })
      const secret = h('input', {
        type: 'password', autocomplete: 'off',
        placeholder: channel.has_client_secret ? '•••••••• (gespeichert)' : '',
      })
      const appName = h('input', { value: channel.oauth_app_name || '', placeholder: 'z. B. „Sally Haupt-App"' })

      return h('div', {},
        channel.platform === 'x'
          ? h('div', { class: 'warnbox', style: { marginBottom: '12px' } },
              'Bei X gehört zu jeder API-App genau ein Account. Für jedes X-Profil im ' +
              'Entwicklerportal eine eigene App anlegen und hier deren Zugangsdaten eintragen. ' +
              'Dieselbe Client-ID an zwei Kanälen wird abgelehnt.')
          : h('div', { class: 'hint', style: { marginBottom: '12px' } },
              'Leer lassen, um die globale App aus den Einstellungen zu verwenden.'),
        field('Name der App (nur zur Orientierung)', appName),
        field('Client-ID', clientId, 'Leer = globale App aus den Einstellungen verwenden'),
        field('Client-Secret', secret, 'Wird verschlüsselt gespeichert.'),
        field('Redirect-URI (im Entwicklerportal eintragen)',
          h('div', { class: 'row' },
            h('input', { value: channel.redirect_uri || '', readonly: true }),
            h('button', { onClick: () => {
              navigator.clipboard?.writeText(channel.redirect_uri || '')
              toast.info('Kopiert')
            } }, 'Kopieren')),
          'Muss exakt so hinterlegt sein, sonst schlägt die Autorisierung fehl.'),
        h('div', { class: 'row' },
          h('button', { class: 'primary', onClick: guard(async () => {
            await api.updateChannel(channel.id, {
              oauth_client_id: clientId.value.trim(),
              oauth_client_secret: secret.value.trim() || null,
              oauth_app_name: appName.value.trim(),
            })
            toast.ok('API-Zugang gespeichert')
            close(); reload()
          }) }, 'Speichern'),
          h('button', { onClick: close }, 'Abbrechen'),
        ),
      )
    })
  }

  // ------------------------------------------------------------ Löschen
  // Bewusst kein knappes „Wirklich löschen?": Am Kanal hängt der gesamte
  // Verlauf, und der ist danach fort. Der Dialog nennt deshalb erst die
  // Zahlen und gibt den Knopf erst frei, wenn der Name abgetippt ist.
  function openDelete(channel) {
    modal(`Kanal löschen — ${channel.display_name}`, (close) => {
      const numbers = h('div', { class: 'hint' }, 'Ermittle, was am Kanal hängt …')
      const confirmName = h('input', { placeholder: channel.display_name, autocomplete: 'off' })
      const delBtn = h('button', { class: 'danger', disabled: true },
        'Endgültig löschen')
      const row = h('div', { class: 'row', style: { marginTop: '12px' } },
        delBtn, h('button', { onClick: close }, 'Abbrechen'))

      confirmName.addEventListener('input', () => {
        delBtn.disabled = confirmName.value.trim() !== channel.display_name.trim()
      })

      delBtn.addEventListener('click', guard(async () => {
        delBtn.disabled = true
        delBtn.textContent = 'lösche …'
        try {
          await api.deleteChannel(channel.id)
        } catch (err) {
          delBtn.disabled = false
          delBtn.textContent = 'Endgültig löschen'
          throw err
        }
        toast.ok(`Kanal „${channel.display_name}" gelöscht`)
        close(); reload()
      }))

      api.channelDeletionPreview(channel.id).then((p) => {
        const rows = [
          ['Veröffentlichte Posts', p.posts_published],
          ['Geplante und offene Posts', p.posts_open],
          ['Bildzuordnungen', p.assignments],
          ['Verlaufseinträge (welches Bild lief hier schon)', p.usages],
        ]
        clear(numbers)
        numbers.className = 'col'
        numbers.appendChild(h('table', {}, h('tbody', {}, ...rows.map(([k, v]) =>
          h('tr', {},
            h('td', { class: 'hint' }, k),
            h('td', { class: 'num', style: { textAlign: 'right', width: '80px' } }, String(v)))))))
        if (p.is_connected) {
          numbers.appendChild(h('div', { class: 'hint' },
            'Die gespeicherte OAuth-Verbindung wird mitgelöscht. Beim Profil auf '
            + 'der Plattform ändert das nichts – dort bleibt der Zugriff bestehen, '
            + 'bis Sie ihn in den Kontoeinstellungen entziehen.'))
        }
      }).catch((err) => {
        clear(numbers)
        numbers.className = 'errbox'
        numbers.textContent = err.message
      })

      return h('div', { class: 'col' },
        h('div', { class: 'errbox' },
          'Das lässt sich nicht rückgängig machen. Die Bilder selbst bleiben in '
          + 'der Bibliothek – gelöscht werden nur die Posts, Zuordnungen und der '
          + 'Verlauf dieses Kanals. Bereits Veröffentlichtes bleibt auf der '
          + 'Plattform online, es verschwindet nur hier aus der Übersicht.'),
        numbers,
        field(`Zum Bestätigen den Kanalnamen eintippen: ${channel.display_name}`, confirmName),
        row,
      )
    })
  }

  // ------------------------------------------------------------ Anlegen
  function openCreate() {
    modal('Kanal anlegen', (close) => {
      const platform = h('select', {}, h('option', { value: 'x' }, 'X (Twitter)'), h('option', { value: 'fanvue' }, 'Fanvue'))
      const name = h('input', {})
      const handle = h('input', { placeholder: '@sallylarsen' })
      const color = h('input', { type: 'color', value: '#1d9bf0' })
      const persona = h('select', {}, h('option', { value: '' }, 'keine'),
        ...personas.map((p) => h('option', { value: p.id }, p.name)))
      const nsfw = h('select', {}, ...['sfw', 'suggestive', 'explicit'].map((v) =>
        h('option', { value: v, selected: v === 'suggestive' }, v)))
      const tz = h('input', { value: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Berlin' })
      const clientId = h('input', { autocomplete: 'off', placeholder: 'leer = globale App' })
      const clientSecret = h('input', { type: 'password', autocomplete: 'off' })
      const appName = h('input', { placeholder: 'Name der App (optional)' })
      const appHint = h('div', { class: 'warnbox', style: { marginBottom: '10px' } },
        'Bei X gehört zu jeder API-App genau ein Account. Für dieses Profil eine eigene App im ' +
        'Entwicklerportal anlegen und die Zugangsdaten hier eintragen.')
      const platformSched = h('input', { type: 'checkbox' })
      const schedRow = h('label', { class: 'inline', style: { display: 'none' } },
        platformSched, 'Planung an Fanvue übergeben (publishAt) statt lokal halten')

      const appFields = h('div', {}, field('Name der App', appName), field('Client-ID', clientId), field('Client-Secret', clientSecret))
      const connectHint = h('div', { class: 'warnbox', style: { margin: '12px 0' } }, 'Nach dem Anlegen den X-Kanal per OAuth verbinden.')
      platform.addEventListener('change', () => {
        appFields.style.display = platform.value === 'fanvue' ? 'none' : ''
        connectHint.style.display = platform.value === 'fanvue' ? 'none' : ''
        schedRow.style.display = platform.value === 'fanvue' ? 'flex' : 'none'
        appHint.textContent = platform.value === 'x'
          ? 'Bei X gehört zu jeder API-App genau ein Account. Für dieses Profil eine eigene App im Entwicklerportal anlegen und die Zugangsdaten hier eintragen.'
          : 'Die gemeinsame Fanvue-Verbindung wird automatisch mitverwendet. Keine zusätzlichen API-Daten oder Anmeldung nötig. Das Handle muss zum verbundenen Fanvue-Konto gehören.'
        handle.placeholder = platform.value === 'fanvue' ? 'sallylarsen' : '@sallylarsen'
      })

      return h('div', {},
        h('div', { class: 'row' },
          h('div', { style: { flex: '1' } }, field('Plattform', platform)),
          h('div', { style: { width: '90px' } }, field('Farbe', color)),
        ),
        field('Anzeigename', name),
        field('Handle', handle),
        h('div', { class: 'row' },
          h('div', { style: { flex: '1' } }, field('Persona', persona)),
          h('div', { style: { flex: '1' } }, field('Max. NSFW-Level', nsfw)),
        ),
        field('Zeitzone', tz),
        schedRow,
        h('h2', { style: { fontSize: '13px', margin: '16px 0 8px', color: 'var(--muted)' } }, 'API-Zugang'),
        appHint,
        appFields,
        connectHint,
        h('button', {
          class: 'primary', style: { width: '100%', justifyContent: 'center' },
          onClick: guard(async () => {
            if (!name.value.trim()) { toast.error('Anzeigename fehlt'); return }
            const isX = platform.value === 'x'
            await api.createChannel({
              platform: platform.value,
              display_name: name.value,
              handle: handle.value,
              color: color.value,
              persona_id: persona.value || null,
              timezone: tz.value,
              nsfw_level: nsfw.value,
              platform_side_scheduling: platformSched.checked,
              default_audience: isX ? '' : 'subscribers',
              oauth_client_id: isX ? clientId.value.trim() : '',
              oauth_client_secret: isX ? clientSecret.value.trim() : '',
              oauth_app_name: isX ? appName.value.trim() : '',
              policy: {
                posts_per_day: 2, min_gap_minutes: 180, jitter_minutes: 12,
                max_images_per_post: isX ? 4 : 20,
                text_only_ratio: isX ? 0.3 : 0,
                allowed_time_windows: [{ dow: [0, 1, 2, 3, 4, 5, 6], from: '09:00', to: '22:00' }],
                reuse_cooldown_days: 0, phash_min_distance: 6, phash_lookback_posts: 20,
                daily_api_quota: isX ? 25 : 100,
                auto_approve: false, auto_retry_on_fail: true, allow_recycling: false,
                set_handling: null,
                exclusive_pool_group: null,
              },
            })
            toast.ok(isX ? 'Kanal angelegt — jetzt verbinden' : 'Kanal angelegt — verwendet die gemeinsame Fanvue-Verbindung')
            close(); reload()
          }),
        }, 'Anlegen'),
      )
    })
  }

  // ------------------------------------------------------------ Einstellungen
  function openPolicy(channel) {
    modal(`Einstellungen — ${channel.display_name}`, (close) => {
      const p = { ...channel.policy }
      const color = h('input', { type: 'color', value: channel.color || '#6366f1' })
      const redirectBase = h('input', {
        value: channel.oauth_redirect_base || '',
        placeholder: 'leer = PUBLIC_BASE_URL aus der .env',
        autocomplete: 'off',
      })
      const redirectShow = h('code', { style: { fontSize: '11px', wordBreak: 'break-all' } })
      const paintRedirect = () => {
        const base = (redirectBase.value.trim() || '').replace(/\/+$/, '')
        redirectShow.textContent = base
          ? `${base}/api/v1/oauth/${channel.platform}/callback`
          : channel.redirect_uri || '—'
      }
      redirectBase.addEventListener('input', paintRedirect)
      paintRedirect()
      const numField = (key, label, hint, step = '1') => {
        const inp = h('input', { type: 'number', step, value: p[key] })
        inp.addEventListener('input', () => { p[key] = Number(inp.value) })
        return field(label, inp, hint)
      }
      const check = (key, label) => {
        const inp = h('input', { type: 'checkbox', checked: !!p[key] })
        inp.addEventListener('change', () => { p[key] = inp.checked })
        return h('label', { class: 'inline' }, inp, label)
      }
      const personaSel = h('select', {}, h('option', { value: '' }, 'keine'),
        ...personas.map((x) => h('option', { value: x.id, selected: x.id === channel.persona_id }, x.name)))
      const poolGroup = h('input', { value: p.exclusive_pool_group || '', placeholder: 'leer = eigener Pool' })
      const watermark = watermarkEditor(channel)

      // Sets: auf Fanvue gehört eine Bildstrecke in EINEN Post, auf X wirken
      // einzelne Bilder besser. Leer heißt "nach Plattform".
      const platformDefault = channel.platform === 'x'
        ? 'einzeln, in Set-Reihenfolge'
        : 'gemeinsam in einem Post'
      const setHandling = h('select', {},
        h('option', { value: '', selected: !p.set_handling },
          `Nach Plattform (${platformDefault})`),
        h('option', { value: 'together', selected: p.set_handling === 'together' },
          'Immer gemeinsam in einem Post'),
        h('option', { value: 'single', selected: p.set_handling === 'single' },
          'Immer einzeln, in Set-Reihenfolge'))
      setHandling.addEventListener('change', () => { p.set_handling = setHandling.value || null })
      const mono = { height: '90px', fontFamily: 'ui-monospace, monospace', fontSize: '12px' }
      const windows = h('textarea', { style: mono })
      windows.value = JSON.stringify(p.allowed_time_windows, null, 1)
      const imageWindows = h('textarea', { style: mono, placeholder: '[]' })
      imageWindows.value = (p.image_time_windows || []).length ? JSON.stringify(p.image_time_windows, null, 1) : ''
      const textWindows = h('textarea', { style: mono, placeholder: '[]' })
      textWindows.value = (p.text_time_windows || []).length ? JSON.stringify(p.text_time_windows, null, 1) : ''

      return h('div', {},
        h('div', { class: 'grid c2' },
          field('Farbe im Kalender', color),
          field('Persona', personaSel),
        ),
        channel.platform !== 'fanvue' ? field('Redirect-URI dieses Kanals', redirectBase,
          'Nur nötig, wenn die Basis-URL des Servers nicht zu der URL passt, die '
          + 'im Portal der Plattform hinterlegt ist — etwa wenn Sie die Oberfläche '
          + 'über localhost aufrufen, der Server sich aber unter seiner LAN-Adresse kennt.') : h('div', { class: 'okbox' }, 'Fanvue verwendet die gemeinsame Verbindung. Keine zusätzliche Redirect-URI nötig.'),
        channel.platform !== 'fanvue' ? h('div', { class: 'hint', style: { marginTop: '-6px', marginBottom: '10px' } },
          'Wird verschickt als: ', redirectShow) : null,
        h('div', { class: 'grid c3' },
          numField('posts_per_day', 'Posts pro Tag', null, '0.5'),
          numField('min_gap_minutes', 'Mindestabstand (Min.)'),
          numField('jitter_minutes', 'Zufallsversatz (Min.)', 'Gegen erkennbare Bot-Muster'),
          numField('text_only_ratio', 'Anteil reiner Textposts', '0–1, nur X', '0.1'),
          numField('max_images_per_post', 'Max. Bilder pro Post'),
          numField('daily_api_quota', 'Tageskontingent API'),
          numField('reuse_cooldown_days', 'Wiederverwendung nach (Tagen)', '0 = auf diesem Kanal nie wiederverwenden'),
          numField('phash_min_distance', 'Ähnlichkeits-Mindestabstand', 'Nur gegen eigene Historie'),
          numField('phash_lookback_posts', 'Ähnlichkeit prüfen gegen letzte N'),
          numField('stagger_minutes', 'Fester Versatz (Min.)',
            'Verschiebt alle Slots dieses Kanals, damit nicht mehrere Kanäle gleichzeitig posten'),
          channel.platform === 'fanvue'
            ? numField('free_post_ratio', 'Anteil Free-Posts', '0 = nur Abonnenten, 1 = alles frei', '0.1')
            : null,
        ),
        field('Gemeinsamer Pool (Gruppe)', poolGroup,
          'Nur setzen, wenn ein Bild bewusst nur auf EINEM Kanal der Gruppe laufen soll.'),
        field('Bild-Sets', setHandling,
          'Gemeinsam: alle Bilder eines Sets landen in einem Post (bis zur Obergrenze oben). '
          + 'Einzeln: jedes Bild bekommt einen eigenen Post, aber in der Reihenfolge des Sets.'),
        h('div', { class: 'col', style: { margin: '10px 0' } },
          check('auto_approve', 'Erzeugte Posts automatisch freigeben'),
          h('div', { class: 'hint', style: { margin: '-4px 0 6px 26px' } },
            'Aus: neue Posts liegen als „zu prüfen" im Kalender und gehen erst nach Ihrer '
            + 'Freigabe raus. An: sie gehen direkt in „geplant". Posts ohne Text bleiben '
            + 'in jedem Fall zur Prüfung liegen.'),
          check('auto_retry_on_fail', 'Bei Fehlschlag erneut versuchen'),
          check('allow_recycling', 'Recycling erlauben'),
        ),
        h('label', { class: 'lbl', style: { marginTop: '14px' } }, 'Wasserzeichen'),
        h('div', { class: 'hint', style: { marginTop: '-4px', marginBottom: '8px' } },
          'Größe und Abstände sind relativ zum Bild – dasselbe Zeichen sitzt auf Hoch- '
          + 'und Querformat gleich. Standard ist unten links mit kleinem Abstand.'),
        watermark.node,
        field('Zeitfenster allgemein (JSON)', windows,
          'dow: 0 = Montag … 6 = Sonntag. Gilt, wenn unten kein eigenes Fenster gesetzt ist.'),
        h('div', { class: 'grid c2' },
          field('Zeitfenster für Posts MIT Bild', imageWindows,
            'Leer lassen = allgemeines Fenster verwenden'),
          field('Zeitfenster für reine TEXTposts', textWindows,
            'Leer lassen = allgemeines Fenster verwenden'),
        ),
        h('button', {
          class: 'primary', style: { width: '100%', justifyContent: 'center' },
          onClick: guard(async () => {
            const parseWindows = (el, label) => {
              const raw = el.value.trim()
              if (!raw) return []
              try { return JSON.parse(raw) } catch { throw new Error(label + ': kein gültiges JSON') }
            }
            try {
              p.allowed_time_windows = parseWindows(windows, 'Zeitfenster allgemein')
              p.image_time_windows = parseWindows(imageWindows, 'Bild-Zeitfenster')
              p.text_time_windows = parseWindows(textWindows, 'Text-Zeitfenster')
            } catch (err) {
              toast.error(err.message); return
            }
            if (!p.allowed_time_windows.length) {
              toast.error('Das allgemeine Zeitfenster darf nicht leer sein'); return
            }
            p.exclusive_pool_group = poolGroup.value || null
            await api.updateChannel(channel.id, {
              policy: p,
              persona_id: personaSel.value || null,
              color: color.value,
              oauth_redirect_base: redirectBase.value.trim(),
              ...watermark.values(),
            })
            toast.ok('Einstellungen gespeichert')
            close(); reload()
          }),
        }, 'Speichern'),
      )
    }, { wide: true })
  }

  draw()

  const livePage = h('div', { class: 'page stack' },
    h('div', { class: 'page-head' },
      h('h1', {}, 'Kanäle'),
      h('div', { class: 'spacer' }),
      h('button', { class: 'primary', onClick: openCreate }, 'Kanal anlegen'),
    ),
    list,
  )
  livePage.refresh = async () => {
    const data = await Promise.all([api.channels(), api.personas()])
    if (window.CreatorStudioLive.busy()) return false
    ;[channels, personas] = data
    draw()
  }
  return livePage

}
