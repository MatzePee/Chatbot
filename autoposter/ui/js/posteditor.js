import { translationField } from './translation-preview.js?v=instagram-followers-20261121-1'
// Post-Editor als eigenes Modul – wird vom Kalender und vom Tagesdetail genutzt.
import { api } from './api.js?v=instagram-followers-20261121-1'
import {
  h, append, clear, empty, field, modal, toast, guard, spinner, openMediaEditor,
  toLocalInput, attachPreview, confirmDialog, mediaPreview,
} from './ui.js?v=instagram-followers-20261121-1'

export function openPostEditor(post, channels, { onChanged = () => {} } = {}) {
  const channel = channels.find((c) => c.id === post.channel_id)
  const isFanvue = channel && channel.platform === 'fanvue'
  const maxChars = channel?.platform === 'telegram' ? (post.media_asset_ids?.length ? 1024 : 4096) : channel?.platform === 'x' ? 280 : channel?.platform === 'instagram' ? 2200 : 5000

  const bufferDelivery = post.generation_meta?.buffer_delivery
  const uploadPost = (bufferDelivery?.provider || channel?.instagram_provider) === 'upload_post'
  const providerName = uploadPost ? 'Upload-Post' : 'Buffer'
  const bufferLocked = ['preparing','uploading','pending','queued','uncertain','remote_error'].includes(bufferDelivery?.state)
  modal(`Post — ${channel ? channel.display_name : ''}`, (close) => {
    const text = h('textarea', { style: { height: '130px' } })
    text.value = post.body_text || ''
    const translatedText = translationField(text, post.body_text_variants)
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
    if (channel?.fanvue_audience) {
      audience.value = channel.fanvue_audience
      audience.disabled = true
    }
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
        src: `/api/v1/media/${assetId}/thumb?size=256&variant=${channel?.platform==='instagram'?'instagram':'all'}`,
        class: 'daythumb', alt: '',
      })
      const open = h('button', { type: 'button', class: 'ghost', style: { padding: '0' }, 'aria-label': 'Medium öffnen',
        onClick: guard(async () => {
          const asset = await api.mediaDetail(assetId)
          if(asset.mime==='video/mp4')modal('Medienvorschau',()=>mediaPreview(asset,{style:{width:'100%',maxHeight:'65vh',objectFit:'contain'},playsInline:true}),{wide:true})
          else await openMediaEditor(asset,{platform:channel?.platform,onSaved:async()=>{await onChanged();showIssues((await api.preflight(post.id)).issues)}})
        }),
      }, img)
      attachPreview(open, `/api/v1/media/${assetId}/thumb?size=512&variant=${channel?.platform==='instagram'?'instagram':'all'}`, { size: 360 })
      open.dataset.cropAsset=assetId;open.dataset.instagramAssigned=String(channel?.platform==='instagram')
      if(channel?.platform==='instagram')api.mediaDetail(assetId).then(asset=>{if(asset.mime.startsWith('image/')&&asset.image_crops?.instagram?.valid===false){open.style.outline='3px solid #dc2626';open.title='⚠ Instagram-Bildformat passt nicht – Bild bearbeiten';open.appendChild(h('span',{class:'image-crop-warning',role:'img','aria-label':'Achtung: Instagram-Bildformat passt nicht'},'⚠'))}}).catch(()=>{})
      mediaBox.appendChild(open)
    }
    if (!(post.media_asset_ids || []).length) {
      mediaBox.appendChild(h('span', { class: 'hint' }, 'Reiner Textpost'))
    }

    const updateCounter = () => {
      translatedText.invalidate()
      const tags = hashtags.value.split(',').map(t=>t.trim()).filter(Boolean).map(t=>'#'+t.replace(/^#+/,'')).join(' ')
      const len = channel?.platform === 'telegram' ? text.value.length + (tags ? tags.length + (text.value ? 2 : 0) : 0) : text.value.length + hashtags.value.split(',').filter(Boolean).join(' ').length
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

        translatedText.seed(result.variants)
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

    if(bufferLocked) genBtn.disabled=true
    return h('div', { class: 'grid c2' },
      h('div', {},
        mediaBox,
        bufferDelivery ? h('div',{class:bufferLocked?'warnbox':'hint',style:{display:'flex',flexDirection:'column',alignItems:'stretch',gap:'10px'}},
          `Instagram über ${providerName} · ` + ({preparing:'Versand vorbereitet',uploading:'Medien werden übertragen',pending:'Übermittlung läuft/unklar – Status prüfen',queued:'Übergeben – Bestätigung steht aus',uncertain:'Versand unklar – Status prüfen, nicht erneut senden',remote_error:'Anbieter hat einen Fehler gemeldet',confirmed:'Auf Instagram veröffentlicht',rejected:'Übermittlung abgelehnt'}[bufferDelivery.state] || bufferDelivery.state),
          h('div',{class:'row'},
            h('button',{style:{whiteSpace:'nowrap',flex:'0 0 auto'},onClick:guard(async()=>{const r=await api.bufferStatus(post.id);toast.info(r.message);const fresh=await api.post(post.id);close();await onChanged();openPostEditor(fresh,channels,{onChanged})})},'Status prüfen'),
            h('a',{class:'btn',style:{whiteSpace:'nowrap',flex:'0 0 auto'},href:uploadPost?'https://app.upload-post.com/':'https://publish.buffer.com',target:'_blank',rel:'noreferrer'},providerName + ' öffnen')),
          post.error_message && ['remote_error','rejected','uncertain'].includes(bufferDelivery.state) ? h('p',{},post.error_message) : null,
          bufferLocked ? h('p',{},'Vor weiteren Aktionen den Versandstatus beim Anbieter prüfen. Es wird kein zweiter Beitrag gesendet.') : null) : null,
        channel?.telegram_mode === 'paid' ? h('div', {class:'card pad col'},
          h('b', {}, `Telegram Pay · ${post.media_set_id ? 'Set' : 'Einzelbild'} · ⭐ ${post.price_stars || 'Preis fehlt'}`),
          h('div',{class:'hint'},'Gespeicherter Gesamtpreis. Änderungen an Preis oder Set erfordern eine erneute Freigabe.'),
          h('button', {onClick:guard(async()=>{
            await api.updatePost(post.id,{body_text:text.value,hashtags:hashtags.value.split(',').map(t=>t.trim()).filter(Boolean)})
            const updated = await api.refreshPaid(post.id)
            toast.ok('Set und Preis übernommen. Bitte erneut freigeben.')
            close(); await onChanged(); openPostEditor(updated, channels, {onChanged})
          })}, 'Set und Preis übernehmen'),
        ) : null,
        channel?.platform === 'telegram' ? h('div',{class:'hint'}, `Telegram · ${maxChars} Zeichen · Plaintext · keine automatische Aufteilung`, post.generation_meta?.telegram_delivery ? ' · Versand: ' + ({uncertain:'unklar – Zielkanal manuell prüfen, nicht erneut senden',pending:'unklar/läuft – nicht erneut senden',confirmed:'bestätigt',rejected:'abgelehnt'}[post.generation_meta.telegram_delivery.state] || post.generation_meta.telegram_delivery.state) : '') : null,
        post.generation_meta?.x_image_comment ? h('div', { class: 'hint', style: { marginTop: '12px', whiteSpace: 'pre-wrap' } },
          'X Auto-Kommentar · ' + ({ pending: 'Wartet', sending: 'Versand begonnen – bei Abbruch auf X prüfen', sent: 'Veröffentlicht', failed: 'Fehlgeschlagen – auf X prüfen', unknown: 'Status unklar – auf X prüfen', cancelled: 'Deaktiviert' }[post.generation_meta.x_image_comment.state] || post.generation_meta.x_image_comment.state),
          '\n' + post.generation_meta.x_image_comment.text,
          post.generation_meta.x_image_comment.error ? '\n' + post.generation_meta.x_image_comment.error : '',
        ) : null,
        h('div', { style: { height: '10px' } }),
        translatedText.node,
        counter,
        field('Hashtags (kommagetrennt)', hashtags),
        field('Geplant für', when),
        isFanvue ? field('Sichtbarkeit', audience,
          channel.fanvue_audience ? 'Die Zielgruppe ist durch den gewählten Kanal festgelegt.'
            : 'Free-Posts erreichen alle Follower, Sub-Posts nur zahlende Abonnenten.') : null,
        isFanvue ? field('Preis in Cent (optional)', price,
          'Mindestens 300 Cent, nur mit Bild möglich.') : null,
        isFanvue ? h('label', { class: 'inline', style: { marginBottom: '12px' } }, pin,
          'Nach dem Veröffentlichen anpinnen') : null,
        h('div', { class: 'row' },
          h('button', { class: 'primary', disabled:bufferLocked, onClick: save }, 'Speichern'),
          h('button', { disabled:bufferLocked, onClick: guard(async () => {
            await api.approve(post.id); toast.ok('Freigegeben'); close(); await onChanged()
          }) }, 'Freigeben'),
          h('button', { disabled:bufferLocked, onClick: guard(async () => {
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
          h('button', { class: 'small danger', disabled:bufferLocked || !!bufferDelivery?.files?.length, onClick: guard(async () => {
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
