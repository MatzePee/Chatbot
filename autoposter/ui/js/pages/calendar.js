// Kalender: ganzer Monat auf einer Seite, mit Planer, Tagesdetail und Sammelaktionen.
import { api } from '../api.js?v=creatorstudio-mobile-system-20260909'
import {
  h, append, clear, empty, field, modal, toast, guard, spinner, confirmDialog,
  fmtTime, fmtDate, fmtDateTime, toLocalInput, attachPreview,
} from '../ui.js?v=creatorstudio-mobile-system-20260909'
import { openPostEditor } from '../posteditor.js?v=creatorstudio-translation-20260910'
import { runProgress } from '../progress.js?v=creatorstudio-mobile-system-20260909'

const WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
const WEEKDAY_LONG = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
const MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli',
  'August', 'September', 'Oktober', 'November', 'Dezember']

const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const sameDay = (a, b) => a.toDateString() === b.toDateString()

/** Alle Tage, die im Monatsraster erscheinen – inklusive angeschnittener Wochen. */
function monthGrid(year, month) {
  const first = new Date(year, month, 1)
  const start = new Date(first)
  start.setDate(1 - ((first.getDay() + 6) % 7))
  const days = []
  for (let i = 0; i < 42; i++) {
    const d = new Date(start)
    d.setDate(start.getDate() + i)
    days.push(d)
    if (i >= 27 && d.getMonth() !== month && d.getDay() === 0) break
  }
  return days
}

export default async function renderCalendar() {
  const today = new Date()
  const state = {
    year: today.getFullYear(),
    month: today.getMonth(),
    posts: [],
    byDay: {},
    blackouts: [],
    channels: [],
    activeChannels: new Set(),
    channelsInitialized: false,
    statusFilter: new Set(),
    selection: new Set(),
  }

  const grid = h('div', { class: 'monthgrid' })
  const title = h('h1', { style: { margin: 0, minWidth: '210px' } })
  const chanRow = h('div', { class: 'row', style: { fontSize: '12px' } })
  const statRow = h('div', { class: 'row', style: { fontSize: '12px' } })
  const selBar = h('div', { class: 'selbar', style: { display: 'none' } })
  // Eigener Bereich, damit die Auswahlleiste beim Neuzeichnen nicht den
  // laufenden Fortschritt mitreißt.
  const runBox = h('div', {})

  // ------------------------------------------------------------ Laden
  async function load() {
    const [data, channels] = await Promise.all([
      api.month(state.year, state.month + 1),
      api.channels(),
    ])
    // Defensiv: eine unerwartete Antwort soll die Seite nicht mit einem
    // nichtssagenden Folgefehler abschießen.
    state.posts = Array.isArray(data && data.posts) ? data.posts : []
    state.byDay = (data && data.by_day) || {}
    state.blackouts = Array.isArray(data && data.blackouts) ? data.blackouts : []
    state.channels = Array.isArray(channels) ? channels : []
    if (!data || !Array.isArray(data.posts)) {
      toast.error('Der Monat konnte nicht geladen werden – der Server antwortete unerwartet.')
    }
    if (!state.channelsInitialized) { channels.forEach(c => state.activeChannels.add(c.id)); state.channelsInitialized = true }
    draw()
    attachRunningJobs()
  }

  // Läuft noch etwas – etwa weil der Dialog geschlossen oder die Seite neu
  // geladen wurde? Dann den Fortschritt wieder anzeigen statt ihn zu verlieren.
  let attached = null
  async function attachRunningJobs() {
    if (attached) return
    let active = []
    try { active = await api.activeRuns() } catch { return }
    if (!active.length) return
    attached = active[0].id
    const progress = runProgress(attached, { label: active[0].label })
    clear(runBox).appendChild(progress.node)
    await progress.finished
    attached = null
    clear(runBox)
    await load()
  }

  const channelOf = (post) => state.channels.find((c) => c.id === post.channel_id)

  function visiblePosts() {
    return state.posts.filter((p) =>
      state.activeChannels.has(p.channel_id) &&
      (!state.statusFilter.size || state.statusFilter.has(p.status)))
  }

  // ------------------------------------------------------------ Kopfzeilen
  function drawFilters() {
    clear(chanRow)
    chanRow.appendChild(h('span', { class: 'hint' }, 'Kanäle:'))
    for (const c of state.channels) {
      chanRow.appendChild(h('button', {
        class: 'chip' + (state.activeChannels.has(c.id) ? '' : ' off'),
        onClick: () => {
          state.activeChannels.has(c.id)
            ? state.activeChannels.delete(c.id)
            : state.activeChannels.add(c.id)
          draw()
        },
      }, h('i', { class: 'dot', style: { background: c.color } }), c.display_name))
    }

    clear(statRow)
    statRow.appendChild(h('span', { class: 'hint' }, 'Status:'))
    const states = [
      ['needs_review', 'zu prüfen'], ['scheduled', 'geplant'],
      ['published', 'veröffentlicht'], ['failed', 'fehlgeschlagen'], ['draft', 'Entwurf'],
    ]
    for (const [key, label] of states) {
      statRow.appendChild(h('button', {
        class: 'chip' + (state.statusFilter.has(key) ? ' on' : ''),
        onClick: () => {
          state.statusFilter.has(key) ? state.statusFilter.delete(key) : state.statusFilter.add(key)
          draw()
        },
      }, label))
    }
  }

  function drawSelectionBar() {
    clear(selBar)
    if (!state.selection.size) { selBar.style.display = 'none'; return }
    selBar.style.display = 'flex'
    const ids = () => Array.from(state.selection)

    // Texte neu erzeugen kann dauern – der Knopf zeigt das an, statt still zu warten.
    const genBtn = () => {
      const btn = h('button', { class: 'small' }, 'Texte neu erzeugen')
      btn.addEventListener('click', guard(async () => {
        const selected = ids()
        btn.disabled = true
        const { run_id, label } = await api.bulkGenerateStart(selected)
        attached = run_id
        const progress = runProgress(run_id, { label })
        clear(runBox).appendChild(progress.node)

        const stateAfter = await progress.finished
        attached = null
        btn.disabled = false
        state.selection.clear()
        await load()
        clear(runBox)
        if (stateAfter.status === 'done') {
          const r = stateAfter.result || {}
          r.failed
            ? toast.error(`${r.generated} erzeugt, ${r.failed} fehlgeschlagen`)
            : toast.ok(`${r.generated} Texte neu erzeugt`)
        }
      }))
      return btn
    }

    append(selBar, [
      h('b', {}, `${state.selection.size} Post(s) ausgewählt`),
      h('button', { class: 'small', onClick: guard(async () => {
        await api.bulkMove(ids(), 24 * 60); toast.ok('Um einen Tag verschoben')
        state.selection.clear(); await load()
      }) }, '+1 Tag'),
      h('button', { class: 'small', onClick: guard(async () => {
        await api.bulkMove(ids(), -24 * 60); toast.ok('Um einen Tag zurück')
        state.selection.clear(); await load()
      }) }, '−1 Tag'),
      h('button', { class: 'small', onClick: guard(async () => {
        await api.bulkMove(ids(), 7 * 24 * 60); toast.ok('Um eine Woche verschoben')
        state.selection.clear(); await load()
      }) }, '+1 Woche'),
      h('button', { class: 'small', onClick: guard(async () => {
        await api.bulkMove(ids(), 0, 'scheduled'); toast.ok('Freigegeben')
        state.selection.clear(); await load()
      }) }, 'Freigeben'),
      genBtn(),
      h('button', { class: 'small danger', onClick: guard(async () => {
        if (!(await confirmDialog('Posts löschen',
          `${state.selection.size} Posts wirklich löschen? Veröffentlichte bleiben erhalten.`,
          { okLabel: 'Löschen', danger: true }))) return
        const r = await api.bulkDelete(ids())
        toast.ok(`${r.deleted} gelöscht` + (r.skipped_published ? `, ${r.skipped_published} veröffentlichte übersprungen` : ''))
        state.selection.clear(); await load()
      }) }, 'Löschen'),
      h('div', { class: 'spacer' }),
      h('button', { class: 'small', onClick: () => { state.selection.clear(); draw() } }, 'Auswahl aufheben'),
    ])
  }

  // ------------------------------------------------------------ Monatsraster
  function draw() {
    title.textContent = `${MONTHS[state.month]} ${state.year}`
    drawFilters()
    clear(grid)

    for (const label of WEEKDAYS) {
      grid.appendChild(h('div', { class: 'monthhead' }, label))
    }

    const posts = visiblePosts()
    const byDate = new Map()
    for (const post of posts) {
      const key = iso(new Date(post.scheduled_at))
      if (!byDate.has(key)) byDate.set(key, [])
      byDate.get(key).push(post)
    }

    for (const day of monthGrid(state.year, state.month)) {
      const key = iso(day)
      const dayPosts = (byDate.get(key) || [])
        .sort((a, b) => (a.scheduled_at > b.scheduled_at ? 1 : -1))
      const outside = day.getMonth() !== state.month
      const isToday = sameDay(day, new Date())
      const inBlackout = state.blackouts.some((b) =>
        new Date(b.starts_at) <= day && day <= new Date(b.ends_at))

      const cell = h('div', {
        class: 'monthday'
          + (outside ? ' outside' : '')
          + (isToday ? ' today' : '')
          + (inBlackout ? ' blackout' : '')
          + (!outside && !dayPosts.length && !inBlackout ? ' emptyday' : ''),
      })

      const addBtn = h('button', {
        class: 'dayadd', title: 'Post für diesen Tag anlegen',
        onClick: (e) => { e.stopPropagation(); openAddDialog(day) },
      }, '+')

      cell.appendChild(h('div', { class: 'monthday-head' },
        h('span', {
          class: 'daynum',
          onClick: () => openDayDialog(day, dayPosts),
          title: 'Tagesübersicht öffnen',
        }, String(day.getDate())),
        dayPosts.length
          ? h('span', { class: 'daycount' }, String(dayPosts.length))
          : null,
        h('div', { class: 'spacer' }),
        addBtn,
      ))

      const list = h('div', { class: 'daylist' })
      for (const post of dayPosts.slice(0, 4)) list.appendChild(postChip(post))
      if (dayPosts.length > 4) {
        list.appendChild(h('button', {
          class: 'daymore',
          onClick: () => openDayDialog(day, dayPosts),
        }, `+${dayPosts.length - 4} weitere`))
      }
      cell.appendChild(list)

      // Posts per Drag & Drop auf einen anderen Tag ziehen.
      cell.addEventListener('dragover', (e) => { e.preventDefault(); cell.classList.add('drop-ok') })
      cell.addEventListener('dragleave', () => cell.classList.remove('drop-ok'))
      cell.addEventListener('drop', guard(async (e) => {
        e.preventDefault(); cell.classList.remove('drop-ok')
        const id = e.dataTransfer.getData('text/plain')
        const post = state.posts.find((p) => p.id === id)
        if (!post || !post.scheduled_at) return
        const orig = new Date(post.scheduled_at)
        const target = new Date(day)
        target.setHours(orig.getHours(), orig.getMinutes(), 0, 0)
        await api.updatePost(post.id, { scheduled_at: target.toISOString() })
        toast.ok('Verschoben auf ' + fmtDate(target))
        await load()
      }))

      grid.appendChild(cell)
    }
    drawSelectionBar()
  }

  function postChip(post) {
    const ch = channelOf(post)
    const selected = state.selection.has(post.id)
    // Kein Text und ein hinterlegter Grund: das ist ein Fehlschlag, kein Entwurf.
    const genError = !post.body_text && (post.generation_meta || {}).error
    const chip = h('div', {
      class: 'postchip st-' + post.status + (selected ? ' selected' : '')
        + (genError ? ' notext' : ''),
      draggable: true,
      title: genError
        ? `${fmtTime(post.scheduled_at)} · Text fehlt: ${genError}`
        : `${fmtTime(post.scheduled_at)} · ${ch ? ch.display_name : ''} · ${post.status}`,
      onClick: (e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey) {
          selected ? state.selection.delete(post.id) : state.selection.add(post.id)
          draw()
        } else {
          openEditor(post)
        }
      },
    },
      h('i', { class: 'dot', style: { background: ch ? ch.color : '#666' } }),
      h('span', { class: 'num' }, fmtTime(post.scheduled_at)),
      post.media_asset_ids.length
        ? h('span', { class: 'chiptype' }, '▣')
        : h('span', { class: 'chiptype' }, '¶'),
      ch && ch.platform === 'fanvue'
        ? h('span', {
            class: 'chipaud',
            style: { color: post.audience === 'followers-and-subscribers' ? '#6ee7b7' : '#80570d' },
          }, post.audience === 'followers-and-subscribers' ? 'F' : 'S')
        : null,
      h('span', { class: 'chiptext' },
        post.body_text || (genError ? '⚠ Text fehlgeschlagen' : '(kein Text)')),
    )
    chip.addEventListener('dragstart', (e) => e.dataTransfer.setData('text/plain', post.id))
    if (post.media_asset_ids.length) {
      attachPreview(chip, `/api/v1/media/${post.media_asset_ids[0]}/file`, { delay: 450, size: 300 })
    }
    return chip
  }

  const openEditor = (post) => openPostEditor(post, state.channels, { onChanged: load })

  // ------------------------------------------------------------ Tagesdetail
  function openDayDialog(day, dayPosts) {
    modal(`${WEEKDAY_LONG[(day.getDay() + 6) % 7]}, ${fmtDate(day)}`, (close) => {
      const list = h('div', { class: 'col' })
      if (!dayPosts.length) {
        list.appendChild(empty('Für diesen Tag ist nichts geplant.'))
      }
      for (const post of dayPosts) {
        const ch = channelOf(post)
        const thumb = post.media_asset_ids.length
          ? h('img', {
              src: `/api/v1/media/${post.media_asset_ids[0]}/thumb?size=256`,
              class: 'daythumb', alt: '',
            })
          : h('div', { class: 'daythumb ph' }, '¶')
        if (post.media_asset_ids.length) {
          attachPreview(thumb, `/api/v1/media/${post.media_asset_ids[0]}/file`, { size: 360 })
        }

        list.appendChild(h('div', { class: 'dayrow' },
          thumb,
          h('div', { style: { flex: '1', minWidth: '0' } },
            h('div', { class: 'row', style: { gap: '6px' } },
              h('i', { class: 'dot', style: { background: ch ? ch.color : '#666' } }),
              h('b', {}, ch ? ch.display_name : '—'),
              h('span', { class: 'hint num' }, fmtTime(post.scheduled_at)),
              h('span', { class: 'chip', style: { pointerEvents: 'none' } }, post.status),
            ),
            h('div', { class: 'hint', style: { marginTop: '3px' } },
              post.body_text || '(kein Text)'),
          ),
          h('div', { class: 'col', style: { gap: '4px' } },
            h('button', { class: 'small', onClick: () => { close(); openEditor(post) } }, 'Bearbeiten'),
            h('button', { class: 'small', onClick: guard(async () => {
              await api.duplicatePost(post.id, { copies: 1, shift_days: 1 })
              toast.ok('Auf den Folgetag kopiert'); close(); await load()
            }) }, 'Duplizieren'),
            h('button', { class: 'small danger', onClick: guard(async () => {
              await api.deletePost(post.id); toast.ok('Gelöscht'); close(); await load()
            }) }, 'Löschen'),
          ),
        ))
      }

      return h('div', { class: 'col' },
        list,
        h('div', { class: 'row', style: { marginTop: '10px' } },
          h('button', { class: 'primary', onClick: () => { close(); openAddDialog(day) } },
            '+ Post für diesen Tag'),
          h('button', { onClick: close }, 'Schließen'),
        ),
      )
    }, { wide: true })
  }

  // ------------------------------------------------------------ Post anlegen
  function openAddDialog(day) {
    modal(`Post anlegen — ${fmtDate(day)}`, (close) => {
      const channelSel = h('select', {},
        ...state.channels.map((c) => h('option', { value: c.id }, `${c.display_name} (${c.platform})`)))
      const timeInput = h('input', { type: 'time', value: '18:00' })
      const typeSel = h('select', {},
        h('option', { value: 'image_single' }, 'Mit Bild'),
        h('option', { value: 'text_only' }, 'Nur Text'))
      const textArea = h('textarea', { placeholder: 'Text – leer lassen und unten automatisch erzeugen' })
      const tagsInput = h('input', { placeholder: 'Bild-Tags eingrenzen (optional)' })
      const autoBox = h('div', { class: 'hint' })

      const buildWhen = () => {
        const [hh, mm] = (timeInput.value || '18:00').split(':')
        const when = new Date(day)
        when.setHours(Number(hh), Number(mm), 0, 0)
        return when
      }

      const createManual = guard(async () => {
        const post = await api.createPost({
          channel_id: channelSel.value,
          type: typeSel.value,
          scheduled_at: buildWhen().toISOString(),
          body_text: textArea.value,
          status: 'draft',
        })
        toast.ok('Post angelegt')
        close(); await load()
        openEditor(post)
      })

      const createAuto = guard(async () => {
        clear(autoBox).appendChild(spinner())
        append(autoBox, [' erzeuge …'])
        const isText = typeSel.value === 'text_only'
        const dayIso = iso(day)
        const result = await api.planRange({
          channel_ids: [channelSel.value],
          date_from: dayIso,
          date_to: dayIso,
          image_posts_per_day: isText ? 0 : 1,
          text_posts_per_day: isText ? 1 : 0,
          time_from: timeInput.value || '18:00',
          time_to: timeInput.value || '18:00',
          weekdays: [0, 1, 2, 3, 4, 5, 6],
          tags_any: tagsInput.value.split(',').map((t) => t.trim()).filter(Boolean),
          fill_gaps_only: false,
          generate_text: true,
          dry_run: false,
        })
        if (!result.created_post_ids.length) {
          const reason = result.gaps.length ? result.gaps[0].reason : 'kein freier Zeitpunkt'
          clear(autoBox)
          autoBox.appendChild(h('div', { class: 'errbox' }, 'Nichts angelegt: ' + reason))
          return
        }
        toast.ok('Post automatisch erzeugt')
        close(); await load()
      })

      return h('div', {},
        h('div', { class: 'grid c3' },
          field('Kanal', channelSel),
          field('Uhrzeit', timeInput),
          field('Art', typeSel),
        ),
        field('Text (optional)', textArea),
        field('Bild-Tags (optional)', tagsInput,
          'Nur Bilder mit diesen Tags kommen für die automatische Erzeugung infrage.'),
        autoBox,
        h('div', { class: 'row', style: { marginTop: '10px' } },
          h('button', { class: 'primary', onClick: createAuto }, 'Automatisch erzeugen'),
          h('button', { onClick: createManual }, 'Leeren Post anlegen'),
          h('div', { class: 'spacer' }),
          h('button', { onClick: close }, 'Abbrechen'),
        ),
      )
    })
  }

  // ------------------------------------------------------------ Automatisch planen
  // Getrennt nach Plattform, weil sich die Fragen unterscheiden: X kennt Bild-
  // und Textposts, Fanvue keine reinen Texte, dafür zwei Sichtbarkeiten. Ein
  // gemeinsamer Dialog müsste die Hälfte der Felder je nach Kanal ausgrauen.
  function openPlanner(platform) {
    const isFanvue = platform === 'fanvue'
    modal(`Automatisch planen — ${isFanvue ? 'Fanvue' : 'X'}`, (close) => {
      const start = new Date()
      const end = new Date(); end.setDate(end.getDate() + 14)

      const fromInput = h('input', { type: 'date', value: iso(start) })
      const toInput = h('input', { type: 'date', value: iso(end) })
      // X: Bild und Text. Fanvue: Abonnenten und Follower.
      const imageCount = h('input', { type: 'number', min: '0', max: '20', value: isFanvue ? '0' : '1' })
      const textCount = h('input', { type: 'number', min: '0', max: '20', value: '0' })
      const subCount = h('input', { type: 'number', min: '0', max: '20', value: '2' })
      const freeCount = h('input', { type: 'number', min: '0', max: '20', value: '1' })
      const timeFrom = h('input', { type: 'time', value: '09:00' })
      const timeTo = h('input', { type: 'time', value: '22:00' })
      const gapInput = h('input', { type: 'number', min: '0', placeholder: 'aus Kanal-Einstellungen' })
      const tagsInput = h('input', { placeholder: 'z. B. beach, gym (optional)' })
      const fillOnly = h('input', { type: 'checkbox', checked: true })
      const genText = h('input', { type: 'checkbox', checked: true })

      const channelBox = h('div', { class: 'row' })
      // Pausierte Kanäle überspringt der Planer stillschweigend. Wer sie hier
      // anwählen kann, wartet danach vergeblich auf Posts – deshalb sind sie
      // sichtbar gesperrt statt unsichtbar wirkungslos.
      const ofPlatform = state.channels.filter((c) => c.platform === platform)
      const usable = ofPlatform.filter((c) => c.is_active !== false)
      const paused = ofPlatform.filter((c) => c.is_active === false)
      const chosen = new Set(usable.map((c) => c.id))
      for (const c of usable) {
        const btn = h('button', {
          class: 'chip on',
          onClick: () => {
            chosen.has(c.id) ? chosen.delete(c.id) : chosen.add(c.id)
            btn.className = 'chip' + (chosen.has(c.id) ? ' on' : '')
            checkFeasibility()
          },
        }, h('i', { class: 'dot', style: { background: c.color } }), c.display_name)
        channelBox.appendChild(btn)
      }
      for (const c of paused) {
        channelBox.appendChild(h('button', {
          class: 'chip', disabled: true,
          title: 'Pausiert — unter Kanäle auf „Fortsetzen" klicken',
        }, h('i', { class: 'dot', style: { background: c.color } }),
           c.display_name + ' (pausiert)'))
      }

      const dayBox = h('div', { class: 'row' })
      const activeDays = new Set([0, 1, 2, 3, 4, 5, 6])
      WEEKDAYS.forEach((label, index) => {
        const btn = h('button', {
          class: 'chip on',
          onClick: () => {
            activeDays.has(index) ? activeDays.delete(index) : activeDays.add(index)
            btn.className = 'chip' + (activeDays.has(index) ? ' on' : '')
          },
        }, label)
        dayBox.appendChild(btn)
      })

      const preview = h('div', {})
      const feasibility = h('div', { class: 'hint' })

      // Wie viele Posts passen überhaupt in das Fenster? day_slots verteilt sie
      // auf die Mitte gleich großer Abschnitte, der Abstand ist also
      // Fenster / Anzahl. Liegt der unter dem Mindestabstand, verwirft der
      // Planer stillschweigend Zeitpunkte – das soll man vorher sehen.
      const minutesOf = (value) => {
        const [hh, mm] = (value || '0:00').split(':').map(Number)
        return hh * 60 + mm
      }
      const effectiveGap = () => {
        if (gapInput.value) return Number(gapInput.value)
        const gaps = state.channels
          .filter((c) => chosen.has(c.id) && c.policy)
          .map((c) => c.policy.min_gap_minutes)
          .filter((v) => typeof v === 'number')
        return gaps.length ? Math.max(...gaps) : 0
      }
      const wantedPerDay = () => isFanvue
        ? Math.max(0, ...usable.filter(c => chosen.has(c.id)).map(c =>
            (c.fanvue_audience === 'followers-and-subscribers' ? 0 : Number(subCount.value) || 0)
            + (c.fanvue_audience === 'subscribers' ? 0 : Number(freeCount.value) || 0)))
        : (Number(imageCount.value) || 0) + (Number(textCount.value) || 0)
      const checkFeasibility = () => {
        const want = wantedPerDay()
        let window = minutesOf(timeTo.value) - minutesOf(timeFrom.value)
        if (window <= 0) window += 24 * 60
        const gap = effectiveGap()
        if (!want || !gap) { clear(feasibility); return }

        const spacing = Math.floor(window / want)
        const fits = Math.max(1, Math.floor(window / gap))
        clear(feasibility)
        if (spacing >= gap) {
          feasibility.className = 'hint'
          feasibility.textContent =
            `${want} Posts pro Tag, ${spacing} Minuten auseinander – Mindestabstand ${gap} Minuten ist eingehalten.`
        } else {
          feasibility.className = 'warnbox'
          append(feasibility, [
            h('b', {}, `Nur ${fits} statt ${want} Posts pro Tag möglich. `),
            `${want} Posts in ${timeFrom.value}–${timeTo.value} lägen ${spacing} Minuten `
            + `auseinander, der Mindestabstand verlangt ${gap}. `,
            h('div', { style: { marginTop: '4px' } },
              'Abhilfe: Fenster verbreitern, Anzahl senken – oder den Mindestabstand hier '
              + `auf ${spacing} setzen.`),
            h('button', {
              class: 'small', style: { marginTop: '6px' },
              onClick: () => { gapInput.value = String(spacing); checkFeasibility() },
            }, `Mindestabstand auf ${spacing} Minuten setzen`),
          ])
        }
      }
      for (const el of [imageCount, textCount, subCount, freeCount, timeFrom, timeTo, gapInput]) {
        el.addEventListener('input', checkFeasibility)
        el.addEventListener('change', checkFeasibility)
      }

      const payload = () => ({
        channel_ids: Array.from(chosen),
        date_from: fromInput.value,
        date_to: toInput.value,
        // Bei Fanvue steuern die Sichtbarkeiten, bei X die Post-Arten. Die
        // jeweils andere Seite bleibt 0 – das Backend erkennt daran, welche
        // Betriebsart gemeint ist.
        image_posts_per_day: isFanvue ? 0 : Number(imageCount.value) || 0,
        text_posts_per_day: isFanvue ? 0 : Number(textCount.value) || 0,
        sub_posts_per_day: isFanvue ? Number(subCount.value) || 0 : 0,
        free_posts_per_day: isFanvue ? Number(freeCount.value) || 0 : 0,
        time_from: timeFrom.value || '09:00',
        time_to: timeTo.value || '22:00',
        weekdays: Array.from(activeDays).sort(),
        min_gap_minutes: gapInput.value ? Number(gapInput.value) : null,
        tags_any: tagsInput.value.split(',').map((t) => t.trim()).filter(Boolean),
        fill_gaps_only: fillOnly.checked,
        generate_text: genText.checked,
      })

      const showPreview = (result) => {
        clear(preview)
        const capacity = h('div', { class: 'col' }, ...result.capacity.map((c) =>
          h('div', { class: c.enough ? 'okbox' : 'warnbox' },
            c.enough
              ? `${c.channel_name}: ${c.needed_images} Bilder nötig, ${c.available_images} verfügbar`
              : `${c.channel_name}: ${c.needed_images} Bilder nötig, nur ${c.available_images} verfügbar — ${c.missing} fehlen`)))

        const slotList = h('div', { style: { maxHeight: '220px', overflowY: 'auto', fontSize: '12px' } },
          ...result.slots.map((s) => {
            const c = state.channels.find((x) => x.id === s.channel_id)
            return h('div', { class: 'row', style: { gap: '6px' } },
              h('i', { class: 'dot', style: { background: c ? c.color : '#666' } }),
              h('span', { class: 'hint num' }, fmtDateTime(s.scheduled_at)),
              h('span', { class: 'hint' }, s.type === 'text_only' ? 'Text' : 'Bild'),
              c && c.platform === 'fanvue'
                ? h('span', { class: 'hint' },
                    s.audience === 'followers-and-subscribers' ? 'Free' : 'Sub')
                : null,
            )
          }))

        append(preview, [
          h('div', { style: { height: '10px' } }),
          capacity,
          h('div', { class: 'grid c2', style: { marginTop: '10px' } },
            h('div', {},
              h('label', { class: 'lbl' }, `${result.slots.length} geplante Zeitpunkte`),
              result.slots.length ? slotList : empty('Nichts zu planen.')),
            h('div', {},
              h('label', { class: 'lbl' }, `${result.gaps.length} Lücken`),
              result.gaps.length
                ? h('div', { style: { maxHeight: '220px', overflowY: 'auto', fontSize: '12px', color: 'var(--warn)' } },
                    ...result.gaps.slice(0, 40).map((g) =>
                      h('div', {}, `${g.channel_name}: ${g.reason}`)))
                : empty('Keine Lücken.')),
          ),
        ])
      }

      const previewBtn = h('button', {}, 'Vorschau')
      const runBtn = h('button', { class: 'primary' }, 'Jetzt planen')

      previewBtn.addEventListener('click', guard(async () => {
        if (!chosen.size) { toast.error('Kein Kanal ausgewählt'); return }
        clear(previewBtn).appendChild(spinner()); previewBtn.disabled = true
        try {
          showPreview(await api.planRange({ ...payload(), dry_run: true }))
        } finally {
          clear(previewBtn).appendChild(document.createTextNode('Vorschau'))
          previewBtn.disabled = false
        }
      }))

      runBtn.addEventListener('click', guard(async () => {
        if (!chosen.size) { toast.error('Kein Kanal ausgewählt'); return }
        runBtn.disabled = true; previewBtn.disabled = true

        // Der Lauf kann Minuten dauern – deshalb im Hintergrund starten und
        // den Fortschritt anzeigen, statt die Anfrage offen zu halten.
        const { run_id, label } = await api.planRangeStart({ ...payload(), dry_run: false })
        attached = run_id
        const progress = runProgress(run_id, { label })
        clear(preview).appendChild(progress.node)

        const state = await progress.finished
        attached = null
        runBtn.disabled = false; previewBtn.disabled = false
        await load()
        if (state.status === 'done') {
          const r = state.result || {}
          toast.ok(`${r.created || 0} Posts angelegt`
            + (r.gaps ? `, ${r.gaps} Lücken offen` : ''))
          close()
        } else if (state.status === 'cancelled') {
          toast.info('Abgebrochen – bereits angelegte Posts bleiben erhalten')
        }
      }))

      setTimeout(checkFeasibility, 0)

      return h('div', {},
        h('div', { class: 'grid c2' },
          field('Von', fromInput),
          field('Bis', toInput),
        ),
        isFanvue
          ? h('div', {},
              h('div', { class: 'grid c2' },
                field('Posts für Abonnenten pro Tag', subCount,
                  'Nur für zahlende Abonnenten sichtbar'),
                field('Posts für Follower pro Tag', freeCount,
                  'Für alle Follower frei sichtbar'),
              ),
              h('div', { class: 'hint', style: { marginTop: '-4px' } },
                'Die Anzahl gilt je passendem Kanal: Subscriber-Posts im Subscriber-Kanal, '
                + 'Follower-Posts im Follower-Kanal. Bestehende gemischte Kanäle verwenden beide Angaben.'))
          : h('div', { class: 'grid c2' },
              field('Bildposts pro Tag', imageCount),
              field('Textposts pro Tag', textCount),
            ),
        h('div', { class: 'grid c3' },
          field('Uhrzeit von', timeFrom),
          field('Uhrzeit bis', timeTo),
          field('Mindestabstand (Min.)', gapInput, 'leer = Kanal-Regel'),
        ),
        feasibility,
        field('Kanäle', channelBox),
        field('Wochentage', dayBox),
        field('Bild-Tags eingrenzen', tagsInput,
          'Nur Bilder mit mindestens einem dieser Tags werden verplant.'),
        h('div', { class: 'col', style: { margin: '10px 0' } },
          h('label', { class: 'inline' }, fillOnly,
            'Nur Lücken füllen (bestehende Posts bleiben unangetastet)'),
          h('label', { class: 'inline' }, genText, 'Texte automatisch erzeugen'),
        ),
        h('div', { class: 'row' }, previewBtn, runBtn,
          h('div', { class: 'spacer' }),
          h('button', { onClick: close }, 'Abbrechen')),
        preview,
      )
    }, { wide: true })
  }

  // ------------------------------------------------------------ Navigation
  const shift = (delta) => () => {
    const d = new Date(state.year, state.month + delta, 1)
    state.year = d.getFullYear()
    state.month = d.getMonth()
    state.selection.clear()
    load()
  }

  await load()

  const livePage = h('div', { class: 'page stack' },
    h('div', { class: 'page-head' },
      title,
      h('button', { onClick: shift(-1) }, '‹'),
      h('button', { onClick: () => {
        const n = new Date(); state.year = n.getFullYear(); state.month = n.getMonth(); load()
      } }, 'Heute'),
      h('button', { onClick: shift(1) }, '›'),
      h('div', { class: 'spacer' }),
      h('button', { onClick: () => {
        for (const post of visiblePosts()) {
          if (post.status !== 'published') state.selection.add(post.id)
        }
        state.selection.size ? draw() : toast.info('Nichts auszuwählen')
      } }, 'Alle im Monat auswählen'),
      h('button', { onClick: guard(load) }, 'Neu laden'),
      // Nur anbieten, was es auch gibt: Ein Fanvue-Knopf ohne Fanvue-Kanal
      // führt in einen Dialog, in dem kein Kanal auswählbar ist.
      state.channels.some((c) => c.platform === 'x')
        ? h('button', { class: 'primary', onClick: () => openPlanner('x') },
            '⚡ X planen')
        : null,
      state.channels.some((c) => c.platform === 'fanvue')
        ? h('button', { class: 'primary', onClick: () => openPlanner('fanvue') },
            '⚡ Fanvue planen')
        : null,
    ),
    h('div', { class: 'col', style: { gap: '6px' } }, chanRow, statRow),
    selBar,
    runBox,
    h('div', { class: 'calendar-scroll', tabindex: 0, role: 'region', 'aria-label': 'Monatskalender, seitlich scrollbar' }, grid),
    h('div', { class: 'row', style: { fontSize: '11px', color: 'var(--dim)', gap: '14px' } },
      h('span', {}, '▣ Bildpost'),
      h('span', {}, '¶ Textpost'),
      h('span', { style: { color: '#6ee7b7' } }, 'F Free-Post'),
      h('span', { style: { color: '#80570d' } }, 'S Sub-Post'),
      h('span', {}, 'Strg/Cmd + Klick wählt mehrere Posts aus'),
      h('span', {}, 'Posts lassen sich auf andere Tage ziehen'),
    ),
  )
  livePage.refresh = async () => { if (state.selection.size || attached) return false; await load() }
  return livePage

}
