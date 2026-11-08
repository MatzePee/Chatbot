import { api } from '../api.js?v=top-posts-targets-20261101'
import { h, clear, empty, toast, attachPreview } from '../ui.js?v=creatorstudio-library-autosave-20261101'

const formatTime = value => new Date(value).toLocaleString('de-DE', {
  timeZone: 'Europe/Berlin', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
})

export default async function renderTopPostsX() {
  const list = h('div', { class: 'top-x-list', 'aria-label': 'X-Posts nach Views' })
  const status = h('div', { class: 'hint', role: 'status', 'aria-live': 'polite' })
  const busy = new Set()
  const page = h('div', { class: 'page stack top-x-page' },
    h('div', { class: 'page-head' }, h('h1', {}, 'Top Posts X')),
    h('p', { class: 'hint' }, 'Die 50 meistgesehenen, in AutoPost gespeicherten X-Posts über alle X-Kanäle. Sortiert nach Views, ohne Altersgrenze.'),
    h('div', { class: 'row top-x-legend', 'aria-label': 'Altersfarben' },
      h('span', { class: 'top-x-age red' }, '0–14 Tage'),
      h('span', { class: 'top-x-age yellow' }, '15–30 Tage'),
      h('span', { class: 'top-x-age green' }, 'Mehr als 30 Tage')),
    status,
    h('p', { class: 'hint' }, 'Die X-Kanäle stehen nebeneinander: letzter Post mit Views, Planungsstatus und Einplanen-Button je Kanal. Beim Versand wird das Originalbild mit dem aktuellen Wasserzeichen des Zielkanals verarbeitet.'),
    list,
  )
  async function load() {
    const scrolls = new Map([...list.querySelectorAll('.top-x-post')].map(node => [node.dataset.postId, node.querySelector('.top-x-channel-table')?.scrollLeft || 0]))
    const data = await api.topPostsX()
    const collection = data.collection || {}
    clear(status).appendChild(h('span', {}, 'Aktualisierung täglich um 01:00 Uhr (Europe/Berlin). ' +
      (collection.completed_at ? 'Letzter Lauf: ' + formatTime(collection.completed_at) + '. ' : 'Erste vollständige Messung steht noch aus. ') +
      (collection.status === 'running' ? 'Messung läuft … ' : '') +
      data.measured_posts + ' von ' + data.total_posts + ' Posts mit View-Daten.'))
    if (data.missing_views) status.appendChild(h('div', {}, data.missing_views + ' Posts ohne verfügbare View-Daten werden nicht als 0 Views gewertet.'))
    if (collection.status === 'partial' || (collection.errors || []).length) {
      status.appendChild(h('div', { class: 'warnbox' }, 'Nicht alle Konten konnten aktualisiert werden. ' + (collection.errors || []).join(' · ')))
    }
    clear(list)
    if (!data.items.length) { list.appendChild(empty('Noch keine X-Posts mit verfügbaren View-Daten. Die nächtliche Messung lädt auch ältere Posts nach.')); return }
    for (const item of data.items) {
      const pictures = h('div', { class: 'top-x-pictures' })
      for (const asset of item.media) {
        const picture = h('a', { href: asset.url, target: '_blank', rel: 'noreferrer', 'aria-label': 'Bild öffnen: ' + asset.filename },
          h('img', { src: asset.thumb_url, alt: asset.filename, loading: 'lazy' }))
        attachPreview(picture, asset.mime === 'video/mp4' ? asset.thumb_url : asset.url, { size: 400 })
        pictures.appendChild(picture)
      }
      if (!item.media.length) pictures.appendChild(h('span', { class: 'hint' }, 'Ohne verfügbares Bild'))
      const targets = (item.targets || [{ channel_id: item.channel_id, name: item.channel_name, platform: 'x', can_reuse: item.can_reuse, reuse_label: item.reuse_label }]).filter(target => target.platform === 'x')
      const channelRows = targets.map(target => {
        const historyKnown = Array.isArray(target.publications)
        const publications = target.publications || []
        const latest = publications[0]
        const key = item.post_id + ':' + target.channel_id
        const label = target.can_reuse ? (latest ? 'Erneut einplanen' : 'Einplanen') : target.reuse_label
        const button = h('button', {
          class: 'primary', type: 'button', disabled: !target.can_reuse,
          dataset: { canReuse: String(!!target.can_reuse) },
          'aria-label': label + ' für ' + target.name,
          title: target.reuse_label,
          onClick: async () => {
            if (busy.size || !target.can_reuse) return
            busy.add(key)
            list.querySelectorAll('.top-x-channel-table button').forEach(node => { node.disabled = true })
            button.textContent = 'Wird freigegeben …'
            window.CreatorStudioLive?.begin()
            try {
              await api.reuseTopPostX(item.post_id, target.channel_id)
              await load()
              toast.ok('Bilder für ' + target.name + ' erneut zur Planung freigegeben')
            } catch (error) {
              toast.error(error.message || 'Freigabe fehlgeschlagen')
              try { await load() } catch { /* Retain the last known table. */ }
            } finally {
              busy.delete(key)
              window.CreatorStudioLive?.end()
              list.querySelectorAll('.top-x-channel-table button').forEach(node => { node.disabled = node.dataset.canReuse !== 'true' })
              if (button.isConnected) button.textContent = label
            }
          },
        }, label)
        const publicationNode = publication => {
          const filenames = item.media.filter(asset => (publication.asset_ids || []).includes(asset.id)).map(asset => asset.filename)
          return h('div', { class: 'top-x-publication ' + publication.age_band },
            h('time', { datetime: publication.published_at }, formatTime(publication.published_at)),
            h('span', { class: 'top-x-publication-views' }, Number.isFinite(publication.views) ? publication.views.toLocaleString('de-DE') + ' Views' : 'Views nicht verfügbar'),
            publication.external_url ? h('a', { href: publication.external_url, target: '_blank', rel: 'noreferrer' }, 'Post ↗') : null,
            item.media.length > 1 ? h('span', { class: 'hint' }, filenames.join(', ')) : null)
        }
        return h('td', { 'data-channel-id': target.channel_id },
          h('div', { class: 'top-x-channel-box top-x-date ' + (latest?.age_band || 'neutral') },
            h('strong', { class: 'top-x-channel-name' }, target.name),
            h('span', { class: 'top-x-channel-status' }, target.can_reuse ? 'Zur Planung verfügbar' : target.reuse_label),
            latest ? publicationNode(latest) : h('span', { class: 'hint' }, historyKnown ? 'Noch nie veröffentlicht' : 'Verlauf wird geladen …'),
            publications.length > 1 ? h('details', { class: 'top-x-history' },
              h('summary', {}, (publications.length - 1) + ' frühere Veröffentlichung' + (publications.length > 2 ? 'en' : '')),
              ...publications.slice(1).map(publicationNode)) : null,
            button),
        )
      })
      const channelTable = h('div', { class: 'top-x-channel-table table-scroll', tabindex: 0, role: 'region', 'aria-label': 'X-Kanäle und Veröffentlichungen für Post ' + item.rank },
        h('table', { style: { minWidth: Math.max(1, targets.length) * 210 + 'px' } },
          h('tbody', {}, h('tr', {}, ...channelRows))))
      list.appendChild(h('article', { class: 'card top-x-post', 'data-post-id': item.post_id },
        h('div', { class: 'top-x-rank num' }, '#' + item.rank),
        h('div', { class: 'top-x-content stack' },
          h('div', { class: 'row' }, h('strong', {}, item.channel_name), h('span', { class: 'hint' }, item.channel_handle)),
          pictures,
          item.text ? h('p', { class: 'top-x-text' }, item.text) : null,
          h('a', { href: item.external_url, target: '_blank', rel: 'noreferrer' }, 'Post auf X ansehen ↗')),
        h('div', { class: 'top-x-performance' },
          h('strong', { class: 'top-x-views num' }, item.views.toLocaleString('de-DE')),
          h('span', { class: 'hint' }, 'Views'),
          h('time', { class: 'hint', datetime: item.published_at }, formatTime(item.published_at))),
        channelTable,
      ))
    }
    list.querySelectorAll('.top-x-post').forEach(node => { node.querySelector('.top-x-channel-table').scrollLeft = scrolls.get(node.dataset.postId) || 0 })
  }
  await load()
  page.refresh = async () => { if (busy.size) return false; await load(); return true }
  return page
}
