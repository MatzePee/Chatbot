import { api } from './api.js?v=creatorstudio-mobile-system-20260909'
import { h, clear, empty, fmtDateTime, guard } from './ui.js?v=creatorstudio-mobile-system-20260909'
import { openPostEditor } from './posteditor.js?v=creatorstudio-translation-20260910'

let preview, owner, timer
const observer = new MutationObserver(() => { if (owner && !owner.isConnected) hide() })
function hide() {
  clearTimeout(timer)
  owner?.removeAttribute('aria-describedby')
  owner = null
  preview?.remove()
  preview = null
  observer.disconnect()
}
function laterHide() { clearTimeout(timer); timer = setTimeout(hide, 150) }
window.addEventListener('hashchange', hide)
window.addEventListener('resize', hide)
document.addEventListener('scroll', event => { if (!preview?.contains(event.target)) hide() }, true)
document.addEventListener('keydown', event => { if (event.key === 'Escape') hide() })

function show(row, post, channel) {
  hide()
  owner = row
  preview = h('div', { class: 'upcoming-preview', id: 'upcoming-preview', role: 'tooltip' },
    h('strong', {}, channel?.display_name || 'Post'),
    h('span', { class: 'hint' }, fmtDateTime(post.scheduled_at)),
    ...(post.media_asset_ids || []).map(id => h('img', {
      src: `/api/v1/media/${encodeURIComponent(id)}/file`, alt: 'Bild des geplanten Posts', loading: 'lazy',
    })),
    h('div', { class: 'upcoming-preview-text' }, post.body_text || '(kein Text)'),
    (post.hashtags || []).length ? h('div', { class: 'upcoming-preview-text hint' },
      post.hashtags.map(tag => '#' + tag.replace(/^#/, '')).join(' ')) : null,
  )
  preview.addEventListener('mouseenter', () => clearTimeout(timer))
  preview.addEventListener('mouseleave', laterHide)
  document.body.appendChild(preview)
  row.setAttribute('aria-describedby', preview.id)
  const rect = row.getBoundingClientRect()
  const width = preview.offsetWidth, height = preview.offsetHeight
  let left = rect.right + 10
  if (left + width > innerWidth - 8) left = rect.left - width - 10
  preview.style.left = Math.max(8, Math.min(left, innerWidth - width - 8)) + 'px'
  preview.style.top = Math.max(8, Math.min(rect.top, innerHeight - height - 8)) + 'px'
  observer.observe(document.body, { childList: true, subtree: true })
}

export function renderUpcoming(list, posts, channels) {
  hide()
  clear(list)
  if (!posts.length) list.appendChild(empty('Nichts eingeplant.'))
  const reload = async () => {
    const fresh = await api.dashboard()
    renderUpcoming(list, fresh.upcoming_posts, fresh.channel_health)
  }
  for (const post of posts) {
    const channel = channels.find(item => item.id === post.channel_id)
    const ids = post.media_asset_ids || []
    const thumb = ids.length ? h('img', {
      src: `/api/v1/media/${encodeURIComponent(ids[0])}/thumb?size=256`,
      alt: 'Bildminiatur', loading: 'lazy',
    }) : h('span', { class: 'upcoming-text-icon', 'aria-label': 'Textpost' }, '¶')
    thumb.addEventListener('error', () => {
      thumb.replaceWith(h('span', { class: 'upcoming-text-icon', 'aria-label': 'Bild nicht verfügbar' }, '▧'))
    }, { once: true })
    const row = h('button', { type: 'button', class: 'upcoming-post',
      'aria-label': `Post bearbeiten · ${channel?.display_name || ''} · ${fmtDateTime(post.scheduled_at)} · ${post.body_text || 'Bildpost'}`,
    },
      h('span', { class: 'upcoming-thumb' }, thumb,
        ids.length > 1 ? h('span', { class: 'upcoming-count' }, '+' + (ids.length - 1)) : null),
      h('span', { class: 'upcoming-copy' },
        h('span', { class: 'upcoming-meta hint' },
          h('i', { class: 'dot', style: { background: channel?.color || '#666' } }),
          h('span', { class: 'num' }, fmtDateTime(post.scheduled_at)),
          h('span', { class: 'upcoming-status' }, ({ scheduled: 'Geplant', needs_review: 'Zur Freigabe' })[post.status] || post.status)),
        h('span', { class: 'upcoming-text' }, post.body_text || '(kein Text)')),
    )
    row.addEventListener('mouseenter', () => {
      if (!matchMedia('(hover: hover)').matches) return
      clearTimeout(timer)
      timer = setTimeout(() => { if (row.isConnected) show(row, post, channel) }, 250)
    })
    row.addEventListener('mouseleave', laterHide)
    row.addEventListener('focus', () => { if (row.matches(':focus-visible')) show(row, post, channel) })
    row.addEventListener('blur', hide)
    row.addEventListener('click', guard(async () => {
      hide()
      row.disabled = true
      try {
        // Read the current version before opening; the scheduler may have changed it.
        const fresh = await api.post(post.id)
        if (list.isConnected) openPostEditor(fresh, channels, { onChanged: reload })
      } finally { row.disabled = false }
    }))
    list.appendChild(row)
  }
}
