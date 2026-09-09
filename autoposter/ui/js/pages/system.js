// System: installierte Version, Updates einspielen, nach GitHub veröffentlichen.
import { api } from '../api.js?v=creatorstudio-mobile-system-20260909'
import {
  h, append, clear, card, field, empty, toast, guard, spinner, confirmDialog, fmtDateTime,
} from '../ui.js?v=creatorstudio-mobile-system-20260909'

export default async function renderSystem() {
  const page = h('div', { class: 'page stack' },
    h('h1', {}, 'System'))

  page.appendChild(await updateCard())
  page.appendChild(await publishCard())
  return page
}

// --------------------------------------------------------------------------- //
// Version und Update
// --------------------------------------------------------------------------- //
async function updateCard() {
  const box = h('div', { class: 'col' })
  const body = h('div', {})

  const draw = (state) => {
    clear(body)
    if (!state.is_git) {
      append(body, [
        h('div', { class: 'warnbox' },
          h('b', {}, 'Keine Update-Verwaltung. '),
          'Diese Installation ist kein Git-Arbeitsverzeichnis. Updates brauchen eine ',
          'Installation per git clone. Einrichten auf dem Server mit ',
          h('code', {}, './run.sh git init'), '.'),
        h('div', { class: 'row', style: { marginTop: '10px', alignItems: 'baseline' } },
          h('span', { class: 'hint' }, 'Installiert:'),
          h('b', { class: 'num' }, state.current || 'unbekannt')),
      ])
      return
    }

    const checkBtn = h('button', {}, 'Jetzt nach Updates sehen')
    checkBtn.addEventListener('click', guard(async () => {
      clear(checkBtn).appendChild(spinner())
      append(checkBtn, [' sehe nach …'])
      try { draw(await api.updateCheck()) } finally { /* draw ersetzt den Knopf */ }
    }))

    const installBtn = h('button', { class: 'primary' }, `Auf ${state.latest} aktualisieren`)
    installBtn.addEventListener('click', guard(async () => {
      const ok = await confirmDialog('Update einspielen',
        `Version ${state.latest} wird installiert und der Dienst startet neu. `
        + 'Datenbank und Konfiguration bleiben unangetastet und werden vorher gesichert. '
        + 'Die Oberfläche ist für einige Sekunden nicht erreichbar.',
        { okLabel: 'Aktualisieren' })
      if (!ok) return
      clear(installBtn).appendChild(spinner())
      append(installBtn, [' spiele ein …'])
      installBtn.disabled = true
      try {
        const r = await api.updateInstall()
        toast.ok(r.message || 'Update eingespielt')
        clear(body).appendChild(h('div', { class: 'okbox' },
          'Update eingespielt. Der Dienst startet neu — in etwa 10 Sekunden neu laden.'))
        setTimeout(() => location.reload(), 12000)
      } catch (err) {
        installBtn.disabled = false
        clear(installBtn).appendChild(document.createTextNode(`Auf ${state.latest} aktualisieren`))
        throw err
      }
    }))

    append(body, [
      h('div', { class: 'row', style: { alignItems: 'baseline', gap: '18px' } },
        h('div', {}, h('div', { class: 'hint' }, 'Installiert'),
          h('div', { class: 'big num' }, state.current || 'unbekannt')),
        state.latest
          ? h('div', {}, h('div', { class: 'hint' }, 'Neueste Version'),
              h('div', { class: 'big num' }, state.latest))
          : null,
      ),
      state.error ? h('div', { class: 'errbox' }, state.error) : null,
      state.update_available
        ? h('div', { class: 'warnbox' },
            h('b', {}, `Version ${state.latest} steht bereit.`))
        : (state.latest && !state.error
            ? h('div', { class: 'okbox' }, 'Diese Installation ist aktuell.')
            : null),
      (state.changelog || []).length
        ? h('div', {},
            h('label', { class: 'lbl' }, 'Was sich ändert'),
            h('ul', { style: { margin: '0 0 4px 18px', fontSize: '12px', color: 'var(--muted)' } },
              ...state.changelog.slice(0, 20).map((line) => h('li', {}, line))))
        : null,
      h('div', { class: 'hint' },
        state.checked_at ? 'Zuletzt geprüft: ' + fmtDateTime(state.checked_at) : 'Noch nicht geprüft'),
      h('div', { class: 'row', style: { marginTop: '10px' } },
        checkBtn,
        state.update_available ? installBtn : null),
    ])
  }

  try {
    draw(await api.version())
  } catch (err) {
    body.appendChild(h('div', { class: 'errbox' }, err.message))
  }
  box.appendChild(body)

  return card('Version und Updates',
    h('p', { class: 'hint', style: { marginTop: '-4px' } },
      'Als Update gilt ein Git-Tag der Form v1.2.3. Eingespielt wird nur auf Knopfdruck — ',
      'nie von allein. Vor jedem Update werden Datenbank und Konfiguration gesichert.'),
    box)
}

// --------------------------------------------------------------------------- //
// Veröffentlichen
// --------------------------------------------------------------------------- //
async function publishCard() {
  const body = h('div', {})
  let state = null

  const load = guard(async () => { state = await api.publishStatus(); draw() })

  function drawSettings() {
    const conf = state.settings || {}
    const remote = h('input', { value: conf.git_remote_url || '', placeholder: 'https://github.com/name/repo.git' })
    const branch = h('input', { value: conf.git_branch || 'main' })
    const user = h('input', { value: conf.git_user_name || '', placeholder: 'GitHub-Benutzername' })
    const email = h('input', { value: conf.git_user_email || '', placeholder: 'mail@example.com' })
    const token = h('input', {
      type: 'password', autocomplete: 'off',
      placeholder: conf.github_token_set ? '•••••••• (gespeichert)' : 'ghp_… oder github_pat_…',
    })
    const clearToken = h('input', { type: 'checkbox' })

    const save = guard(async () => {
      state = await api.saveGitSettings({
        git_remote_url: remote.value, git_branch: branch.value,
        git_user_name: user.value, git_user_email: email.value,
        github_token: token.value, clear_token: clearToken.checked,
      })
      token.value = ''; clearToken.checked = false
      toast.ok('Gespeichert')
      draw()
    })

    return h('details', { style: { marginTop: '10px' } },
      h('summary', { style: { cursor: 'pointer', fontSize: '13px' } },
        'GitHub-Zugang' + (conf.github_token_set ? ' (eingerichtet)' : ' — noch nicht eingerichtet')),
      h('div', { class: 'stack', style: { marginTop: '10px' } },
        h('div', { class: 'grid c2' },
          field('Repository-URL', remote),
          field('Branch', branch),
          field('Benutzername', user, 'Gilt als Commit-Autor und für die Anmeldung.'),
          field('E-Mail', email),
        ),
        field('Personal Access Token', token,
          'Braucht das Recht „repo". Leer lassen heißt: unverändert lassen.'),
        conf.github_token_set
          ? h('label', { class: 'inline' }, clearToken, 'Gespeicherten Token löschen')
          : null,
        h('button', { class: 'primary', onClick: save }, 'Zugang speichern'),
      ),
    )
  }

  function draw() {
    clear(body)
    if (!state.is_git) {
      append(body, [
        h('div', { class: 'warnbox' }, state.error),
        drawSettings(),
      ])
      return
    }

    const blocking = (state.issues || []).filter((i) => i.level === 'error')
    const message = h('input', { value: '', placeholder: 'Was hat sich geändert?' })
    const tagInput = h('input', { placeholder: 'leer = nur hochladen, ohne neue Version' })
    const doPush = h('input', { type: 'checkbox', checked: true })

    const suggestions = h('div', { class: 'row' },
      ...Object.entries(state.suggestions || {}).map(([kind, value]) =>
        h('button', {
          class: 'chip',
          title: { patch: 'Fehlerbehebung', minor: 'Neue Funktion', major: 'Großer Umbau' }[kind],
          onClick: () => { tagInput.value = value },
        }, `${value} (${kind})`)))

    const publishBtn = h('button', {
      class: 'primary',
      disabled: blocking.length > 0,
      title: blocking.length ? 'Erst die Befunde oben beheben' : '',
    }, 'Veröffentlichen')
    publishBtn.addEventListener('click', guard(async () => {
      const tag = tagInput.value.trim()
      const ok = await confirmDialog('Veröffentlichen',
        tag
          ? `Der aktuelle Stand wird als Version ${tag} auf GitHub veröffentlicht. `
            + 'Andere Installationen sehen sie dann als Update.'
          : 'Der aktuelle Stand wird hochgeladen, ohne eine neue Version zu setzen. '
            + 'Für andere Installationen ändert sich damit nichts.',
        { okLabel: 'Veröffentlichen' })
      if (!ok) return
      clear(publishBtn).appendChild(spinner())
      append(publishBtn, [' lade hoch …'])
      publishBtn.disabled = true
      try {
        const r = await api.publishRelease({
          message: message.value || 'Aktualisierung', tag, push: doPush.checked,
        })
        toast.ok(r.message)
        if ((r.steps || []).length) {
          clear(body).appendChild(h('div', { class: 'okbox' },
            h('b', {}, r.message),
            h('ul', { style: { margin: '6px 0 0 18px' } }, ...r.steps.map((x) => h('li', {}, x)))))
          setTimeout(load, 2500)
          return
        }
        await load()
      } finally {
        publishBtn.disabled = false
        clear(publishBtn).appendChild(document.createTextNode('Veröffentlichen'))
      }
    }))

    const sync = state.sync || {}
    append(body, [
      h('div', { class: 'row', style: { gap: '18px', alignItems: 'baseline' } },
        h('div', {}, h('div', { class: 'hint' }, 'Stand'), h('b', { class: 'num' }, state.current)),
        state.latest_tag
          ? h('div', {}, h('div', { class: 'hint' }, 'Letzte Version'), h('b', { class: 'num' }, state.latest_tag))
          : null,
        h('div', {}, h('div', { class: 'hint' }, 'Branch'), h('b', {}, state.branch)),
        sync.known
          ? h('div', { class: 'hint' }, `${sync.ahead} vorne · ${sync.behind} hinten (Stand des letzten Abrufs)`)
          : null,
      ),

      ...blocking.map((i) => h('div', { class: 'errbox' }, i.message)),

      h('div', { style: { marginTop: '10px' } },
        h('label', { class: 'lbl' }, `Geänderte Dateien (${(state.files || []).length})`),
        (state.files || []).length
          ? h('div', {
              style: {
                maxHeight: '180px', overflowY: 'auto', fontSize: '12px',
                fontFamily: 'ui-monospace, monospace', border: '1px solid var(--line)',
                borderRadius: '8px', padding: '8px',
              },
            }, ...state.files.map((f) => h('div', {},
                h('span', { class: 'hint', style: { display: 'inline-block', width: '26px' } }, f.code),
                f.path)))
          : empty('Keine Änderungen — alles ist gesichert.'),
      ),

      field('Beschreibung', message),
      h('div', {},
        h('label', { class: 'lbl' }, 'Neue Version'),
        suggestions,
        h('div', { style: { height: '6px' } }),
        tagInput,
        h('div', { class: 'hint' },
          'Nur ein Tag der Form v1.2.3 gilt bei anderen Installationen als Update. '
          + 'Ohne Tag wird der Stand nur gesichert.'),
      ),
      h('label', { class: 'inline', style: { margin: '10px 0' } }, doPush,
        'Zu GitHub hochladen (aus = nur lokal sichern)'),
      h('div', { class: 'row' }, publishBtn),
      drawSettings(),
    ])
  }

  try {
    await load()
  } catch (err) {
    body.appendChild(h('div', { class: 'errbox' }, err.message))
  }

  return card('Veröffentlichen',
    h('p', { class: 'hint', style: { marginTop: '-4px' } },
      'Sichert den aktuellen Stand auf GitHub. Vor dem Hochladen wird geprüft, dass weder ',
      '.env noch data/ noch ein echter Schlüssel dabei ist — das blockiert, es warnt nicht.'),
    body)
}
