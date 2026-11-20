import { platformDefaultsCard, connectionCard } from './settings.js?v=image-warning-20261121-1'
import { api } from '../api.js?v=image-warning-20261121-1'
import { platformIcon } from '../platform-icon.js?v=image-warning-20261121-1'
import { watermarkEditor } from '../watermark.js?v=image-warning-20261121-1'
import { h, clear, empty, field, modal, toast, guard, fmtDateTime } from '../ui.js?v=image-warning-20261121-1'

function callbackEditor(channel = null) {
  const suffix = '/api/v1/oauth/x/callback'
  const initial = channel?.redirect_uri || `${location.origin}${suffix}`
  const input = h('input', {
    type: 'url', value: initial, autocomplete: 'off', spellcheck: false,
    'aria-label': 'Callback-Adresse (Redirect-URI)',
    placeholder: `https://dein-server.de${suffix}`,
  })
  const value = () => {
    const raw = input.value.trim().replace(/\/+$/, '')
    if (!raw) return ''
    let url
    try { url = new URL(raw) } catch { throw new Error('Bitte eine vollständige Callback-Adresse mit http:// oder https:// eintragen.') }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || /[\s\\]/.test(raw)) {
      throw new Error('Bitte eine gültige Callback-Adresse ohne Zugangsdaten, Abfrage oder Fragment eintragen.')
    }
    if (!url.pathname.endsWith(suffix)) {
      throw new Error(`Die Callback-Adresse muss auf ${suffix} enden.`)
    }
    return raw.slice(0, -suffix.length).replace(/\/+$/, '')
  }
  const setAddress = (address) => {
    input.value = address
    input.dispatchEvent(new Event('input', { bubbles: true }))
  }
  return {
    values: () => channel && input.value === initial ? {} : { oauth_redirect_base: value() },
    node: field('Callback-Adresse (Redirect-URI)', h('div', { class: 'col' },
      input,
      h('div', { class: 'row' },
        h('button', { type: 'button', class: 'small', onClick: () => setAddress(`${location.origin}${suffix}`) }, 'Diese Serveradresse verwenden'),
        h('button', { type: 'button', class: 'small', onClick: guard(async () => {
          const base = value()
          if (!base) { toast.info('Zum Kopieren zuerst eine Callback-Adresse eintragen.'); return }
          if (!navigator.clipboard?.writeText) { input.focus(); input.select(); toast.info('Adresse markiert – bitte kopieren.'); return }
          await navigator.clipboard.writeText(`${base}${suffix}`)
          toast.info('Kopiert')
        }) }, 'Kopieren'))),
      'Die Adresse muss auf diesen Server führen und exakt so im X-Entwicklerportal hinterlegt sein. Vor dem Verbinden speichern. Leer = gemeinsame Basis-URL aus den AutoPost-Einstellungen.'),
  }
}

export default async function renderChannels() {
  let [channels, personas] = await Promise.all([api.channels(), api.personas()])
  const list = h('div', { class: 'col channel-list', style: { gap: '16px', minWidth: '0' } })

  const reload = () => {
    const target = '/settings/shared?section=channels'
    location.href = window.CreatorStudioCharacters?.url(target) || target
  }

  function draw() {
    clear(list)
    if (!channels.length) {
      list.appendChild(empty('Noch keine Kanäle. X kannst du hier anlegen. Die beiden Fanvue-Kanäle erscheinen automatisch nach Einrichtung der gemeinsamen Verbindung.'))
      return
    }
    for (const c of channels) {
      list.appendChild(h('div', { class: 'card pad col', style: { marginBottom: '0' } },
        h('div', { class: 'row' },
          h('i', { class: 'dot', style: { background: c.color, width: '11px', height: '11px' } }),
          platformIcon(c.platform), h('b', { style: { flex: '1 1 160px', minWidth: '0' } }, c.display_name),
          h('span', { class: 'hint', style: { textTransform: 'uppercase' } }, c.platform),
        ),
        h('div', { class: 'hint' }, c.handle),
        c.platform === 'telegram' ? h('b', {}, c.telegram_mode === 'paid' ? 'Telegram Pay · Sterne' : 'Telegram Gratis') : null,
        c.fanvue_audience ? h('div', { class: 'hint' }, c.fanvue_audience === 'subscribers'
          ? 'Subscriber-Posts · nur für zahlende Abonnenten'
          : 'Follower-Posts · für Follower und Abonnenten') : null,
        h('div', { class: 'row', style: { fontSize: '13px' } },
          h('span', { style: { color: c.health === 'ok' ? '#17683c' : c.health === 'paused' ? '#526074' : '#b42332' } },
            '● ' + c.health),
          c.token_expires_at ? h('span', { class: 'hint' }, 'Token bis ' + fmtDateTime(c.token_expires_at)) : null,
        ),
        c.health_note ? h('div', { style: { fontSize: '13px', color: c.health === 'ok' ? '#17683c' : c.health === 'paused' ? '#526074' : '#80520b' } }, c.health_note) : null,
        h('div', { class: 'hint', style: { fontSize: '13px' } },
          c.platform === 'instagram' ? (c.instagram_provider === 'upload_post' ? 'Instagram über Upload-Post · direkter Datei-Upload' : 'Instagram über Buffer · FTPS-Webspace') : c.platform === 'telegram' ? 'Eigener Telegram-Publikationsbot · kein Benachrichtigungsbot' : c.platform === 'fanvue' ? 'Gemeinsame Verbindung · AutoChat' : c.uses_own_app
            ? `Eigene App${c.oauth_app_name ? ' „' + c.oauth_app_name + '"' : ''} · ${c.oauth_client_id.slice(0, 12)}…`
            : (c.app_configured ? 'Globale App aus den Einstellungen' : 'Keine App hinterlegt')),
        // Eine Client-ID ohne Secret ist der häufigste stille Fehler: Der Kanal
        // sieht eingerichtet aus, der Token-Tausch scheitert aber mit
        // „invalid_client", weil gar keine Anmeldung mitgeht.
        c.uses_own_app && !c.has_client_secret
          ? h('div', { style: { fontSize: '13px', color: '#b42332' } },
              '⚠ Client-Secret fehlt — Verbinden wird mit „invalid_client" scheitern.')
          : null,
        c.verified_account
          ? h('div', { style: { fontSize: '13px', color: '#17683c' } },
              `Zuletzt geprüft: @${c.verified_account}` +
              (c.last_verified_at ? ' · ' + fmtDateTime(c.last_verified_at) : ''))
          : null,
        h('div', { class: 'hint', style: { fontSize: '13px' } },
          `${c.policy ? c.policy.posts_per_day : '?'} Post(s)${c.platform === 'telegram' && c.telegram_interval_days > 1 ? ' alle ' + c.telegram_interval_days + ' Tage' : '/Tag'} · ` +
          `${c.quota_used_today}/${c.policy ? c.policy.daily_api_quota : '?'} heute` +
          (c.platform === 'fanvue' && c.platform_side_scheduling ? ' · plattformseitige Planung' : '')),
        h('div', { class: 'row' },
          c.platform === 'fanvue'
            ? h('a', { class: 'btn small', href: '/settings/shared?section=autochat#fanvue' }, 'Gemeinsame Verbindung')
            : c.platform === 'instagram'
            ? h('button', {class:'small', onClick:guard(() => c.instagram_provider === 'upload_post' ? openUploadPost(c) : openBuffer(c))}, c.instagram_provider === 'upload_post' ? 'Upload-Post-Zugang' : 'Buffer & Webspace')
            : c.platform === 'telegram'
            ? h('button', {class:'small', onClick: () => openTelegram(c)}, 'Bot-Zugang')
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
          c.platform === 'instagram' && c.is_connected ? h('button',{class:'small',onClick:guard(async()=>{await api.disconnect(c.id);toast.ok('Getrennt');reload()})},'Trennen') : null,
          c.platform === 'x' ? h('button', { class: 'small', onClick: () => openApp(c) }, 'API-Zugang') : null,
          c.platform === 'telegram' && c.has_bot_token ? h('button', {class:'small',onClick:guard(async()=>{ await api.disconnect(c.id); toast.ok('Getrennt'); reload() })}, 'Trennen') : null,
          h('button', { class: 'small', onClick: (e) => runTest(c, e.target) }, 'Testen'),
          h('button', { class: 'small', onClick: () => openPolicy(c) }, 'Einstellungen'),
          c.platform === 'telegram' && c.telegram_mode !== 'paid' ? h('button', {class:'small', onClick: guard(async () => {
            await api.createTelegramPay(c.id)
            toast.ok('Pay-Kanal bereit. Rechte testen, Bilder/Sets zuordnen und aktivieren.')
            await reload()
          })}, 'Pay-Kanal anlegen') : null,
          h('div', { class: 'spacer' }),
          !c.fanvue_audience ? h('button', {
            class: 'small danger', title: 'Kanal endgültig löschen',
            onClick: () => openDelete(c),
          }, 'Löschen') : null,
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
          h('table', {}, h('tbody', {}, ...(channel.platform === 'instagram' && channel.instagram_provider === 'upload_post' ? [['Verbindung','Upload-Post → Instagram'],['Account',r.account || '—'],['Instagram-ID',r.account_id || '—'],['Prüfung','Nur Profil und Kontoverbindung gelesen. Kein Beitrag, kein Medienupload.']] : channel.platform === 'instagram' ? [['Verbindung','Buffer → Instagram'],['Account',r.account || '—'],['Buffer-Konto-ID',r.account_id || '—'],['Prüfung','FTPS-Testdatei hochgeladen, über HTTPS gelesen und entfernt. Kein Instagram-Post.']] : channel.platform === 'telegram' ? [
            ['Plattform','Telegram'], ['Bot-ID',r.account_id || '—'], ['Bot',r.account || '—'], ['Kanal-ID',r.chat_id || '—'], ['Prüfung','Nur lesend – keine Testnachricht'],
          ] : [
            ['Plattform', r.platform],
            ['App', r.uses_own_app ? 'eigene App' + (r.app_name ? ` (${r.app_name})` : '') : 'globale App'],
            ['Account', r.account ? '@' + r.account : '—'],
            ['Account-ID', r.account_id || '—'],
            ['Scopes', (r.scopes || []).join(' ') || '—'],
            ['Token gültig bis', r.token_expires_at ? fmtDateTime(r.token_expires_at) : '—'],
            ['Redirect-URI', r.redirect_uri],
          ]).map(([k, v]) => h('tr', {},
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

  async function openUploadPost(channel) {
    const saved=await api.uploadPostSettings(channel.id)
    modal(`Instagram über Upload-Post — ${channel.display_name}`,close=>{
      const key=h('input',{type:'password',autocomplete:'new-password','aria-label':'Upload-Post API Key',placeholder:saved.has_api_key?'Gespeichert – leer lassen zum Beibehalten':'API Key aus Upload-Post'})
      const profile=h('select',{'aria-label':'Upload-Post-Profil'},h('option',{value:''},'Zuerst Profile laden'))
      if(saved.profile)profile.appendChild(h('option',{value:saved.profile,selected:true},saved.profile+' (gespeichert)'))
      const status=h('div',{class:'hint',role:'status','aria-live':'polite'})
      const store=async()=>{await api.saveUploadPost(channel.id,{api_key:key.value.trim(),profile:profile.value});key.value='';status.textContent='Gespeichert. Kanal pausiert; automatische Planung aus.'}
      return h('div',{class:'col'},
        h('p',{},'Bilder und Videos werden direkt vom CreatorStudio an Upload-Post übertragen. Für diesen Versandweg wird kein FTP-Zugang und keine öffentliche Medien-Subdomain benötigt.'),
        field('Upload-Post API Key',key,'Verschlüsselt gespeichert. Leer = vorhandenen Schlüssel beibehalten.'),
        h('button',{onClick:guard(async e=>{const button=e.currentTarget;button.disabled=true;try{
          await store();const rows=await api.uploadPostProfiles(channel.id);const old=profile.value;clear(profile).appendChild(h('option',{value:''},'Profil auswählen'))
          for(const row of rows)profile.appendChild(h('option',{value:row.profile,selected:row.profile===old},`${row.label} · @${row.handle || '?'}${row.reauth_required?' · neu verbinden':''}`))
          if(!old){const match=rows.find(r=>r.handle.toLowerCase()===channel.handle.replace(/^@/,'').toLowerCase());if(match)profile.value=match.profile}
          status.textContent=rows.length?'Profil auswählen und Verbindung testen.':'Keine verbundenen Instagram-Profile gefunden. Konto in Upload-Post verbinden.'
        }finally{button.disabled=false}})},'Key speichern & Profile laden'),
        field('Upload-Post-Profil',profile,`Das Instagram-Konto muss zu ${channel.handle} passen. Der Profilname beim Anbieter darf anders lauten.`),
        h('p',{class:'hint'},'Verbindung testen liest nur Profil und Instagram-Konto. Es wird nichts veröffentlicht. Anschließend Medien diesem Kanal zuordnen, Kanal fortsetzen und einen einzelnen Beitrag testen. Einen bisherigen Buffer-Kanal für dasselbe Konto bitte pausieren.'),
        status,
        h('div',{class:'row'},
          h('button',{class:'primary',onClick:guard(async e=>{const b=e.currentTarget;b.disabled=true;try{await store();const result=await api.testChannel(channel.id);status.className=result.ok?'okbox':'errbox';status.textContent=result.message;if(result.ok){close();reload()}}finally{b.disabled=false}})},'Speichern & Verbindung testen'),
          h('button',{onClick:guard(async()=>{await store();close();reload()})},'Speichern'),
          h('button',{onClick:close},'Schließen')))
    })
  }

  async function openBuffer(channel) {
    const saved = await api.bufferSettings(channel.id)
    modal(`Instagram über Buffer — ${channel.display_name}`, close => {
      const key = h('input', {type:'password', autocomplete:'new-password', placeholder:saved.has_api_key ? 'Gespeichert – leer lassen zum Beibehalten' : 'Buffer API Key', 'aria-label':'Buffer API Key'})
      const password = h('input', {type:'password', autocomplete:'new-password', placeholder:saved.has_ftp_password ? 'Gespeichert – leer lassen zum Beibehalten' : '', 'aria-label':'FTPS-Passwort'})
      const host = h('input', {value:saved.ftp_host, placeholder:'FTP-Server aus dem ALL-INKL KAS', 'aria-label':'FTPS-Host'})
      const user = h('input', {value:saved.ftp_user, autocomplete:'off', 'aria-label':'FTPS-Benutzer'})
      const port = h('input', {type:'number', min:1, max:65535, value:saved.ftp_port || 21})
      const directory = h('input', {value:saved.ftp_directory || '/'})
      const base = h('input', {type:'url', value:saved.public_base_url || 'https://sally.sallylarsen.net', 'aria-label':'Öffentliche Medienadresse'})
      const account = h('select', {'aria-label':'Instagram-Konto in Buffer'}, h('option',{value:''},'Zuerst Konten laden'))
      if(saved.buffer_channel_id) account.appendChild(h('option',{value:saved.buffer_channel_id, selected:true},`${channel.handle} (gespeichert)`))
      const status = h('div', {class:'hint', role:'status', 'aria-live':'polite'})
      const payload = () => ({api_key:key.value.trim(),ftp_password:password.value,ftp_host:host.value.trim(),ftp_port:Number(port.value)||21,ftp_user:user.value.trim(),ftp_directory:directory.value.trim()||'/',public_base_url:base.value.trim(),buffer_channel_id:account.value})
      const store = async () => { await api.saveBuffer(channel.id,payload()); key.value=''; password.value=''; status.textContent='Gespeichert. Der Kanal ist pausiert; automatische Planung ist aus.' }
      const load = guard(async e => {
        const button = e.currentTarget; button.disabled=true
        try {
          await store()
          const rows = await api.bufferAccounts(channel.id)
          const selected = account.value
          clear(account).appendChild(h('option',{value:''},'Instagram-Konto auswählen'))
          for(const row of rows) account.appendChild(h('option',{value:row.id, selected:row.id===selected},`${row.name}${row.isDisconnected?' · getrennt':''}${row.isLocked?' · gesperrt':''}`))
          if(!selected) { const match=rows.find(r=>r.name.replace(/^@/,'').toLowerCase()===channel.handle.replace(/^@/,'').toLowerCase()); if(match) account.value=match.id }
          status.textContent=rows.length ? 'Instagram-Konto auswählen und anschließend speichern oder Verbindung testen.' : 'Keine Instagram-Konten gefunden. Verbindung in Buffer prüfen.'
        } finally {button.disabled=false}
      })
      return h('div',{class:'col'},
        h('p',{},'Dein Creator-Konto wird über Buffer veröffentlicht. Das Studio überträgt nur die Medien des jeweiligen Posts auf deinen Webspace. Kein Facebook-Konto und keine Meta-App nötig.'),
        field('Buffer API Key',key,'Verschlüsselt gespeichert. Leer = unverändert.'),
        h('div',{class:'grid c2'},field('FTPS-Host',host),field('Port',port)),
        field('FTPS-Benutzer',user),field('FTPS-Passwort',password,'Explizites FTP mit TLS. Verschlüsselt gespeichert. Leer = unverändert.'),
        field('FTP-Verzeichnis',directory,'Bei einem eigenen FTP-Zugang auf das Subdomain-Verzeichnis normalerweise /. Kein lokaler Serverpfad.'),
        field('Öffentliche Medienadresse (HTTPS)',base,'Muss auf dasselbe Verzeichnis zeigen und ein gültiges SSL-Zertifikat haben.'),
        h('button',{onClick:load},'Zugang speichern & Instagram-Konten laden'),
        field('Instagram-Konto in Buffer',account,`Muss zum Kanal-Handle ${channel.handle} passen.`),
        h('p',{class:'hint'},'Bilder und Bildkarussells (bis 10), einzelne MP4-Videos als Reels. Die Planung bleibt im Studio; erst zum Versandzeitpunkt wird an Buffer übergeben. Öffentliche Kopien werden 7 Tage nach bestätigter Veröffentlichung entfernt. Ungewöhnliche Bildformate erhalten weiße Ränder statt Beschnitt.'),
        h('p',{class:'hint'},'„Verbindung testen“ lädt eine kleine Testdatei hoch, liest sie über HTTPS und löscht sie. Es wird kein Instagram-Beitrag veröffentlicht.'),
        status,
        h('div',{class:'row'},
          h('button',{class:'primary',onClick:guard(async()=>{await store();close();reload()})},'Speichern'),
          h('button',{onClick:guard(async e=>{const b=e.currentTarget;b.disabled=true;try{await store();const r=await api.testChannel(channel.id);status.className=r.ok?'okbox':'errbox';status.textContent=r.message;if(r.ok){close();reload()}}finally{b.disabled=false}})},'Speichern & Verbindung testen'),
          h('button',{onClick:close},'Schließen')))
    })
  }

  function openTelegram(channel) {
    modal(`Telegram Bot-Zugang — ${channel.display_name}`, close => {
      const token = h('input', {type:'password',autocomplete:'new-password', 'aria-label':'Bot-Token',placeholder:channel.has_bot_token?'•••••••• (gespeichert)':''})
      const destination = h('input', {value:channel.handle || '', 'aria-label':'Zielkanal',placeholder:'@kanalname oder -100…'})
      return h('div', {class:'col'},
        h('p', {}, 'Eigene geplante Telegram-Beiträge. Bot als Administrator mit „Nachrichten posten“ hinzufügen. Kein Zugriff auf Benachrichtigungsbot, keine Webhooks/Updates.'),
        h('p',{class:'hint'},'Gratis- und zugehöriger Pay-Kanal teilen Bot-Zugang und Ziel. Änderungen und Trennen betreffen beide.'),
        field('Bot-Token',token,'Verschlüsselt gespeichert. Leer/maskiert = unverändert.'),
        field('Zielkanal',destination,'Öffentlicher @Name oder private numerische -100… Kanal-ID.'),
        h('p',{class:'hint'},'Speichern verbindet noch nicht. Danach „Testen“ prüft getMe/getChat/getChatMember ohne Testnachricht. Aktivierung/Planung bleiben unverändert.'),
        h('button',{class:'primary',onClick:guard(async()=>{await api.saveTelegram(channel.id,{bot_token:token.value.trim(),destination:destination.value.trim()});token.value='';toast.ok('Gespeichert – jetzt Rechte testen');close();reload()})},'Speichern'),
        h('button',{onClick:close},'Abbrechen'))
    })
  }

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
      const clearSecret = h('input', { type: 'checkbox' })
      const callback = callbackEditor(channel)

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
        field('Client-Secret', secret, 'Wird verschlüsselt gespeichert. Leer/maskiert = unverändert.'),
        h('label', { class: 'chk' }, clearSecret, 'Gespeichertes Client-Secret entfernen'),
        callback.node,
        h('div', { class: 'row' },
          h('button', { class: 'primary', onClick: guard(async () => {
            await api.updateChannel(channel.id, {
              oauth_client_id: clientId.value.trim(),
              oauth_client_secret: secret.value.trim() || null,
              oauth_clear_client_secret: clearSecret.checked,
              oauth_app_name: appName.value.trim(),
              ...callback.values(),
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
      const confirmName = h('input', { placeholder: channel.display_name, autocomplete: 'off', 'aria-label':'Kanalnamen bestätigen' })
      let previewReady = false, needsDetach = false
      const detach = h('input', {type:'checkbox'})
      const externalBox = h('div',{class:'warnbox',hidden:true})
      const delBtn = h('button', { class: 'danger', disabled: true },
        'Endgültig löschen')
      const row = h('div', { class: 'row', style: { marginTop: '12px' } },
        delBtn, h('button', { onClick: close }, 'Abbrechen'))

      const updateDelete = () => { delBtn.disabled = !previewReady || confirmName.value.trim() !== channel.display_name.trim() || (needsDetach && !detach.checked) }
      confirmName.addEventListener('input', updateDelete)
      detach.addEventListener('change', updateDelete)

      delBtn.addEventListener('click', guard(async () => {
        delBtn.disabled = true
        delBtn.textContent = 'lösche …'
        try {
          await api.deleteChannel(channel.id, needsDetach ? {detach_external:true, confirm_name:confirmName.value.trim()} : undefined)
        } catch (err) {
          updateDelete()
          delBtn.textContent = 'Endgültig löschen'
          throw err
        }
        toast.ok(`Kanal „${channel.display_name}" gelöscht`)
        close(); reload()
      }))

      api.channelDeletionPreview(channel.id).then((p) => {
        needsDetach = !!p.requires_external_detach
        externalBox.hidden = !needsDetach
        if(needsDetach) externalBox.append(
          h('p',{},'Zu diesem Kanal sind externe Versandvorgänge oder hochgeladene Dateien gespeichert. Du kannst den Kanal trotzdem ausschließlich aus dem CreatorStudio entfernen.'),
          h('p',{},'Das bricht keine geplanten oder laufenden Beiträge bei Buffer/Upload-Post ab. Bitte diese dort prüfen und bei Bedarf abbrechen. Kopien auf dem Webspace bleiben erhalten.'),
          h('label',{class:'inline'},detach,h('span',{},'Ich möchte den Kanal nur im CreatorStudio löschen. Externe Beiträge und Dateikopien bleiben bestehen.')))
        previewReady = true
        updateDelete()
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
            'Die gespeicherte Verbindung wird mitgelöscht. Beim Profil auf '
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
        externalBox,
        field(`Zum Bestätigen den Kanalnamen eintippen: ${channel.display_name}`, confirmName),
        row,
      )
    })
  }

  // ------------------------------------------------------------ Anlegen
  function openCreate() {
    modal('Kanal anlegen', (close) => {
      const platform = h('select', {'aria-label':'Plattform'}, h('option', { value: 'x' }, 'X (Twitter)'), h('option',{value:'telegram'},'Telegram-Kanal'), h('option',{value:'instagram'},'Instagram über Buffer'), h('option',{value:'instagram_upload_post'},'Instagram über Upload-Post · Direktupload'))
      const name = h('input', {})
      const handle = h('input', { placeholder: '@sallylarsen' })
      const color = h('input', { type: 'color', value: '#1d9bf0' })
      const persona = h('select', {}, h('option', { value: '' }, 'keine'),
        ...personas.map((p) => h('option', { value: p.id }, p.name)))
      const tz = h('input', { value: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Berlin' })
      const clientId = h('input', { autocomplete: 'off', placeholder: 'leer = globale App' })
      const clientSecret = h('input', { type: 'password', autocomplete: 'off' })
      const appName = h('input', { placeholder: 'Name der App (optional)' })
      const callback = callbackEditor()
      const appHint = h('div', { class: 'warnbox', style: { marginBottom: '10px' } },
        'Bei X gehört zu jeder API-App genau ein Account. Für dieses Profil eine eigene App im ' +
        'Entwicklerportal anlegen und die Zugangsdaten hier eintragen.')
      const platformSched = h('input', { type: 'checkbox' })
      const schedRow = h('label', { class: 'inline', style: { display: 'none' } },
        platformSched, 'Planung an Fanvue übergeben (publishAt) statt lokal halten')

      const appFields = h('div', {}, field('Name der App', appName), field('Client-ID', clientId), field('Client-Secret', clientSecret), callback.node)
      const connectHint = h('div', { class: 'warnbox', style: { margin: '12px 0' } }, 'Nach dem Anlegen den X-Kanal per OAuth verbinden.')
      platform.addEventListener('change', () => {
        appFields.style.display = platform.value === 'x' ? '' : 'none'
        connectHint.style.display = platform.value === 'x' ? '' : 'none'
        schedRow.style.display = platform.value === 'fanvue' ? 'flex' : 'none'
        appHint.textContent = platform.value === 'instagram_upload_post' ? 'Instagram über Upload-Post: direkter Datei-Upload. Nach dem Anlegen API-Key speichern, Profil auswählen und Verbindung testen. Der Kanal startet pausiert.' : platform.value === 'instagram' ? 'Instagram über Buffer. Nach dem Anlegen Buffer-Key und FTPS-Webspace unter „Buffer & Webspace“ einrichten. Der Kanal startet pausiert.' : platform.value === 'telegram' ? 'Telegram: eigene Beiträge mit eigener Planung. Nach dem Anlegen Bot-Token und Kanal unter Bot-Zugang speichern und Rechte testen. Neue Telegram-Kanäle sind pausiert; automatische Planung ist aus.' : platform.value === 'x'
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
              platform: platform.value === 'instagram_upload_post' ? 'instagram' : platform.value,
              instagram_provider: platform.value === 'instagram_upload_post' ? 'upload_post' : 'buffer',
              display_name: name.value,
              handle: handle.value,
              color: color.value,
              persona_id: persona.value || null,
              timezone: tz.value,
              nsfw_level: 'explicit',
              platform_side_scheduling: platformSched.checked,
              default_audience: platform.value === 'fanvue' ? 'subscribers' : '',
              oauth_client_id: isX ? clientId.value.trim() : '',
              oauth_client_secret: isX ? clientSecret.value.trim() : '',
              oauth_app_name: isX ? appName.value.trim() : '',
              ...(isX ? callback.values() : {}),
              policy: {
                posts_per_day: ['telegram','instagram','instagram_upload_post'].includes(platform.value) ? 1 : 2, min_gap_minutes: 180, jitter_minutes: 12,
                max_images_per_post: isX ? 4 : 10,
                text_only_ratio: 0.3,
                allowed_time_windows: [{ dow: [0, 1, 2, 3, 4, 5, 6], from: '09:00', to: '22:00' }],
                reuse_cooldown_days: 0, phash_min_distance: 6, phash_lookback_posts: 20,
                daily_api_quota: isX ? 25 : 100,
                auto_approve: false, auto_retry_on_fail: !['telegram','instagram','instagram_upload_post'].includes(platform.value), allow_recycling: false,
                set_handling: null,
                exclusive_pool_group: null,
              },
            })
            toast.ok(isX ? 'Kanal angelegt — jetzt verbinden' : platform.value === 'instagram_upload_post' ? 'Instagram-Kanal pausiert angelegt — jetzt Upload-Post-Zugang einrichten' : platform.value === 'instagram' ? 'Instagram-Kanal pausiert angelegt — jetzt Buffer & Webspace einrichten' : 'Telegram-Kanal pausiert angelegt — jetzt Bot-Zugang einrichten')
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
      const paid = channel.platform === 'telegram' && channel.telegram_mode === 'paid'
      const interval = h('input', {type:'number', min:1, max:365, step:1, value:channel.telegram_interval_days || 1})
      const start = h('input', {type:'date', value:channel.telegram_start_date || ''})
      const content = h('select', {}, ...[['mixed','Einzelmedien und Sets zufällig'],['images','Nur Einzelbilder und Einzelvideos'],['sets','Nur Sets']].map(([value,label]) => h('option',{value,selected:value===channel.telegram_content},label)))
      const name = h('input', { value: channel.display_name, maxLength: 120, autocomplete: 'off', 'aria-label': 'Anzeigename' })
      const color = h('input', { type: 'color', value: channel.color || '#6366f1' })
      const callback = channel.platform === 'x' ? callbackEditor(channel) : null
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
      const textGuidelines = h('textarea', {'aria-label':'Telegram-Textvorgaben',maxLength:4000,value:p.text_guidelines || ''})
      textGuidelines.addEventListener('input',()=>{p.text_guidelines=textGuidelines.value})
      const sendAltText = h('input', { type: 'checkbox', checked: channel.x_send_alt_text !== false })
      const madeWithAi = h('input', { type: 'checkbox', checked: !!channel.x_made_with_ai })

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
        channel.platform === 'telegram' ? h('div', {class:'card pad col'},
          h('b', {}, paid ? 'Telegram Pay' : 'Telegram Gratis'),
          h('div', {class:'grid c2'}, field('Alle … Tage posten', interval, '1 = täglich, 3 = alle drei Tage. Anzahl unten gilt pro aktivem Tag.'), field('Startdatum des Rhythmus', start)),
          paid ? field('Pay-Angebote auswählen', content, 'Nur zugeordnete Bilder/Videos mit Einzelpreis oder vollständige Sets mit eigenem Gesamtpreis. 2–10 Medien je Set.') : null,
          paid ? h('div', {class:'hint'}, 'Bot und Telegram-Ziel werden mit dem Gratis-Kanal geteilt. Sternepreise in der Bibliothek festlegen. Sets bleiben vollständig und geordnet.') : null,
        ) : null,
        field('Anzeigename', name, 'Nur die Bezeichnung in AutoPost; Handle und Konto bleiben unverändert.'),
        h('div', { class: 'grid c2' },
          field('Farbe im Kalender', color),
          field('Persona', personaSel),
        ),
        channel.platform === 'telegram' ? h('div',{class:'hint'},'Telegram: eigener Zeitplan, eigene Persona, eigene Medienzuordnung. Text 4096 / Caption 1024 Zeichen; Album 2–10 Medien. JPEG/PNG und H.264/AAC-MP4; keine automatischen Wiederholungen bei Telegram. Bot-Zugang separat verwalten.') : channel.platform === 'instagram' ? h('div',{class:'hint'},channel.instagram_provider === 'upload_post' ? 'Instagram über Upload-Post: lokale Planung, direkter Datei-Upload, Bilder/Karussells oder einzelne Videos als Reel. Verbindung unter „Upload-Post-Zugang“ verwalten.' : 'Instagram über Buffer: lokale Planung, Bilder/Karussells oder einzelne Videos als Reel. Verbindung unter „Buffer & Webspace“ verwalten.') : callback ? callback.node : h('div', { class: 'okbox' }, 'Fanvue verwendet die gemeinsame Verbindung. ', h('a', { href: '/settings/shared?section=autochat#fanvue' }, 'Callback-Adresse in den gemeinsamen Einstellungen ändern.')),
        h('div', { class: 'grid c3' },
          numField('posts_per_day', 'Posts pro Tag', null, '0.5'),
          numField('min_gap_minutes', 'Mindestabstand (Min.)'),
          numField('jitter_minutes', 'Zufallsversatz (Min.)', 'Gegen erkennbare Bot-Muster'),
          paid ? null : numField('text_only_ratio', 'Anteil reiner Textposts', '0–1, X und Telegram', '0.1'),
          paid ? null : numField('max_images_per_post', 'Max. Bilder pro Post'),
          numField('daily_api_quota', 'Tageskontingent API'),
          numField('reuse_cooldown_days', 'Wiederverwendung nach (Tagen)', '0 = auf diesem Kanal nie wiederverwenden'),
          numField('phash_min_distance', 'Ähnlichkeits-Mindestabstand', 'Nur gegen eigene Historie'),
          numField('phash_lookback_posts', 'Ähnlichkeit prüfen gegen letzte N'),
          numField('stagger_minutes', 'Fester Versatz (Min.)',
            'Verschiebt alle Slots dieses Kanals, damit nicht mehrere Kanäle gleichzeitig posten'),
          channel.platform === 'fanvue' && !channel.fanvue_audience
            ? numField('free_post_ratio', 'Anteil Free-Posts', '0 = nur Abonnenten, 1 = alles frei', '0.1')
            : null,
        ),
        channel.platform === 'telegram' ? field('Telegram-Textvorgaben',textGuidelines,'Nur für diesen Kanal; ergänzt Persona und Telegram-Regeln bei manueller und automatischer Generierung.') : null,
        field('Gemeinsamer Pool (Gruppe)', poolGroup,
          'Nur setzen, wenn ein Bild bewusst nur auf EINEM Kanal der Gruppe laufen soll.'),
        paid ? h('div',{class:'hint'},'Pay-Sets werden immer vollständig verkauft: 2–10 Medien in der festgelegten Reihenfolge. Einzelbilder und Videos verwenden ihren eigenen Sternepreis.') : field('Bild-Sets', setHandling,
          'Gemeinsam: alle Bilder eines Sets landen in einem Post (bis zur Obergrenze oben). '
          + 'Einzeln: jedes Bild bekommt einen eigenen Post, aber in der Reihenfolge des Sets.'),
        h('div', { class: 'col', style: { margin: '10px 0' } },
          check('auto_approve', 'Erzeugte Posts automatisch freigeben'),
          h('div', { class: 'hint', style: { margin: '-4px 0 6px 26px' } },
            'Aus: neue Posts liegen als „zu prüfen" im Kalender und gehen erst nach Ihrer '
            + 'Freigabe raus. An: sie gehen direkt in „geplant". Posts ohne Text bleiben '
            + 'in jedem Fall zur Prüfung liegen.'),
          ['telegram','instagram'].includes(channel.platform) ? null : check('auto_retry_on_fail', 'Bei Fehlschlag erneut versuchen'),
          check('allow_recycling', 'Recycling erlauben'),
        ),
        h('label', { class: 'lbl', style: { marginTop: '14px' } }, 'Wasserzeichen'),
        h('div', { class: 'hint', style: { marginTop: '-4px', marginBottom: '8px' } },
          'Größe und Abstände sind relativ zum Bild – dasselbe Zeichen sitzt auf Hoch- '
          + 'und Querformat gleich. Standard ist unten links mit kleinem Abstand.'),
        watermark.node,
        channel.platform === 'x' ? h('div', { class: 'col', style: { margin: '16px 0' } },
          h('h3', {}, 'X-Medienkennzeichnung'),
          h('label', { class: 'inline' }, sendAltText, 'Bildbeschreibung an X senden (ALT)'),
          h('p', { class: 'hint' },
            'X zeigt bei Bildern mit Beschreibung das ALT-Abzeichen an. Es ist kein Wasserzeichen. ' +
            'Ausschalten verhindert die Übermittlung bei neuen Uploads; dabei entfällt die Bildbeschreibung für Screenreader. ' +
            'Bereits veröffentlichte Bilder bleiben unverändert.'),
          h('label', { class: 'inline' }, madeWithAi, 'Medien als KI-generiert kennzeichnen'),
          h('p', { class: 'hint' },
            'Kennzeichnet künftig veröffentlichte Medienposts dieses X-Kanals mit dem offiziellen KI-Label. ' +
            'Gilt auch für bereits geplante Posts. Reine Textposts und Auto-Kommentare bleiben unverändert.'),
        ) : null,
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
            const displayName = name.value.trim()
            if (!displayName) { toast.error('Anzeigename fehlt'); return }
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
            if (paid) {p.text_only_ratio=0; p.set_handling='together'}
            p.exclusive_pool_group = poolGroup.value || null
            await api.updateChannel(channel.id, {
              display_name: displayName,
              ...(channel.platform === 'telegram' ? {telegram_interval_days:Number(interval.value), telegram_start_date:start.value || null, telegram_content:content.value} : {}),
              policy: p,
              persona_id: personaSel.value || null,
              color: color.value,
              ...(callback ? callback.values() : {}),
              ...watermark.values(),
              ...(channel.platform === 'x' ? {
                x_send_alt_text: sendAltText.checked,
                x_made_with_ai: madeWithAi.checked,
              } : {}),
            })
            toast.ok('Einstellungen gespeichert')
            close(); reload()
          }),
        }, 'Speichern'),
      )
    }, { wide: true })
  }

  draw()

  const livePage = h('div', { class: 'page stack channels-page' },
    h('div', { class: 'page-head' },
      h('h1', {}, 'Kanäle'),
      h('div', { class: 'spacer' }),
      h('button', { class: 'primary', onClick: openCreate }, 'Kanal anlegen'),
      h('a', { class: 'btn', href: '/settings/shared?section=autochat#fanvue' }, 'Fanvue-Verbindung'),
    ),
    list,
  )
  livePage.refresh = async () => {
    const data = await Promise.all([api.channels(), api.personas()])
    if (window.CreatorStudioLive.busy()) return false
    ;[channels, personas] = data
    draw()
  }
  const integrations = await api.integrations()
  livePage.appendChild(platformDefaultsCard(integrations))
  livePage.appendChild(await connectionCard(channels))
  return livePage

}
