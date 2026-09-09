// Post-Editor als eigenes Modul – wird vom Kalender und vom Tagesdetail genutzt.
import { api } from './api.js?v=creatorstudio-mobile-system-20260909'
import {
  h, append, clear, empty, field, modal, toast, guard, spinner,
  toLocalInput, attachPreview, confirmDialog,
} from './ui.js?v=creatorstudio-mobile-system-20260909'

export function openPostEditor(post, channels, { onChanged = () => {} } = {}) {
  const channel = channels.find((c) => c.id === post.channel_id)
  const isFanvue = channel && channel.platform === 'fanvue'
  const maxChars = channel && channel.platform === 'x' ? 280 : 5000

  modal(`Post — ${channel ? channel.display_name : ''}`, (close) => {
    const text = h('textarea', { style: { height: '130px' } })
    text.value = post.body_text || ''
    const hashtags = h('input', { value: (post.hashtags || []).join(', ') })
    const when = h('input', { type: 'datetime-local', value: toLocalInput(post.scheduled_at) })

    const audience = h('select', {},
      h('option', {
        value: 'followers-and-subscribers',
        selected: post.audience === 'followers-and-subscribers',
      }, 'Free-Post — für alle Follower sichtbar'),
      h('option', {
        value: 'subscribers',
        selected: post.audience !== 'followers-and-subscribers',
      }, 'Sub-Post — nur für Abonnenten'))
    const price = h('input', {
      type: 'number', min: '300', step: '50',
      value: post.price_cents || '', placeholder: 'kein Preis',
    })
    const pin = h('input', { type: 'checkbox', checked: !!post.pin_after_publish })

    const counter = h('div', { class: 'hint' })
    const issuesBox = h('div', { class: 'col' })
    const instruction = h('input', { placeholder: 'Zusätzliche Anweisung (optional)' })
    const undoBox = h('div', { style: { marginTop: '8px' } })

    // ---- Bilder des Posts ----
    const mediaBox = h('div', { class: 'row' })
    for (const assetId of post.media_asset_ids || []) {
      const img = h('img', {
        src: `/api/v1/media/${assetId}/thumb?size=256`,
        class: 'daythumb', alt: '',
      })
      attachPreview(img, `/api/v1/media/${assetId}/file`, { size: 360 })
      mediaBox.appendChild(img)
    }
    if (!(post.media_asset_ids || []).length) {
      mediaBox.appendChild(h('span', { class: 'hint' }, 'Reiner Textpost'))
    }

    const updateCounter = () => {
      const len = text.value.length + hashtags.value.split(',').filter(Boolean).join(' ').length
      counter.textContent = `${len} / ${maxChars} Zeichen`
      counter.style.color = len > maxChars ? 'var(--err)' : 'var(--dim)'
    }
    text.addEventListener('input', updateCounter)
    hashtags.addEventListener('input', updateCounter)
    updateCounter()

    const showIssues = (issues) => {
      clear(issuesBox)
      if (!issues.length) {
        issuesBox.appendChild(h('div', { class: 'okbox' }, 'Keine Beanstandungen.'))
        return
      }
      for (const issue of issues) {
        issuesBox.appendChild(
          h('div', { class: issue.level === 'error' ? 'errbox' : 'warnbox' }, issue.message))
      }
    }
    api.preflight(post.id).then((r) => showIssues(r.issues)).catch(() => {})

    const save = guard(async () => {
      const payload = {
        body_text: text.value,
        hashtags: hashtags.value.split(',').map((t) => t.trim()).filter(Boolean),
        scheduled_at: when.value ? new Date(when.value).toISOString() : null,
      }
      if (isFanvue) {
        payload.audience = audience.value
        payload.price_cents = price.value ? Number(price.value) : null
        payload.pin_after_publish = pin.checked
      }
      await api.updatePost(post.id, payload)
      toast.ok('Gespeichert')
      showIssues((await api.preflight(post.id)).issues)
      await onChanged()
    })

    // Direkt ersetzen statt Varianten anbieten: Wer neu generiert, will einen
    // neuen Text – nicht drei Vorschläge, aus denen er wählen soll.
    const genBtn = h('button', { class: 'primary', style: { width: '100%', justifyContent: 'center' } },
      'Text neu generieren')
    genBtn.addEventListener('click', guard(async () => {
      const before = text.value
      clear(genBtn).appendChild(spinner())
      append(genBtn, [' erzeuge …'])
      genBtn.disabled = true
      try {
        const result = await api.generate({
          post_id: post.id, instruction: instruction.value, variants: 1,
        })
        const fresh = (result.variants || [])[0]
        if (!fresh || !fresh.text) { toast.error('Das Modell lieferte keinen Text'); return }

        text.value = fresh.text
        hashtags.value = (fresh.hashtags || []).join(', ')
        updateCounter()
        if (fresh.issues && fresh.issues.length) {
          toast.info(`Erzeugt mit Hinweis: ${fresh.issues.join(', ')}`)
        } else {
          toast.ok('Neuer Text von ' + result.model)
        }
        // Der alte Text ist einen Klick weit weg, falls der neue nicht taugt.
        clear(undoBox).appendChild(h('button', {
          class: 'small',
          onClick: () => {
            text.value = before
            updateCounter()
            clear(undoBox)
          },
        }, 'Vorherigen Text zurückholen'))
      } finally {
        clear(genBtn).appendChild(document.createTextNode('Text neu generieren'))
        genBtn.disabled = false
      }
    }))

    return h('div', { class: 'grid c2' },
      h('div', {},
        mediaBox,
        post.generation_meta?.x_image_comment ? h('div', { class: 'hint', style: { marginTop: '12px', whiteSpace: 'pre-wrap' } },
          'X Auto-Kommentar · ' + ({ pending: 'Wartet', sending: 'Versand begonnen – bei Abbruch auf X prüfen', sent: 'Veröffentlicht', failed: 'Fehlgeschlagen – auf X prüfen', unknown: 'Status unklar – auf X prüfen', cancelled: 'Deaktiviert' }[post.generation_meta.x_image_comment.state] || post.generation_meta.x_image_comment.state),
          '\n' + post.generation_meta.x_image_comment.text,
          post.generation_meta.x_image_comment.error ? '\n' + post.generation_meta.x_image_comment.error : '',
        ) : null,
        h('div', { style: { height: '10px' } }),
        field('Text', text),
        counter,
        field('Hashtags (kommagetrennt)', hashtags),
        field('Geplant für', when),
        isFanvue ? field('Sichtbarkeit', audience,
          'Free-Posts erreichen alle Follower, Sub-Posts nur zahlende Abonnenten.') : null,
        isFanvue ? field('Preis in Cent (optional)', price,
          'Mindestens 300 Cent, nur mit Bild möglich.') : null,
        isFanvue ? h('label', { class: 'inline', style: { marginBottom: '12px' } }, pin,
          'Nach dem Veröffentlichen anpinnen') : null,
        h('div', { class: 'row' },
          h('button', { class: 'primary', onClick: save }, 'Speichern'),
          h('button', { onClick: guard(async () => {
            await api.approve(post.id); toast.ok('Freigegeben'); close(); await onChanged()
          }) }, 'Freigeben'),
          h('button', { onClick: guard(async () => {
            const result = await api.publish(post.id)
            result.ok ? toast.ok(result.message) : toast.error(result.message)
            close(); await onChanged()
          }) }, 'Jetzt veröffentlichen'),
        ),
        h('div', { class: 'row', style: { marginTop: '8px' } },
          h('button', { class: 'small', onClick: guard(async () => {
            await api.duplicatePost(post.id, { copies: 1, shift_days: 1 })
            toast.ok('Auf den Folgetag kopiert'); close(); await onChanged()
          }) }, 'Duplizieren'),
          h('div', { class: 'spacer' }),
          h('button', { class: 'small danger', onClick: guard(async () => {
            if (!(await confirmDialog('Post löschen', 'Diesen Post wirklich löschen?',
              { okLabel: 'Löschen', danger: true }))) return
            await api.deletePost(post.id); toast.ok('Gelöscht'); close(); await onChanged()
          }) }, 'Löschen'),
        ),
      ),
      h('div', { class: 'stack' },
        h('div', {}, h('label', { class: 'lbl' }, 'Preflight'), issuesBox),
        h('div', {},
          h('label', { class: 'lbl' }, 'Text neu generieren'),
          instruction,
          h('div', { class: 'hint', style: { margin: '4px 0 8px' } },
            'Ersetzt den Text links. Bildbeschreibung, Tagesrhythmus und '
            + 'Beispielposts fließen automatisch ein.'),
          genBtn,
          undoBox,
        ),
        post.external_url
          ? h('a', { href: post.external_url, target: '_blank', rel: 'noreferrer' },
              'Veröffentlichten Post ansehen →')
          : null,
      ),
    )
  }, { wide: true })
}
