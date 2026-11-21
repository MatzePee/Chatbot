import { h } from './ui.js?v=instagram-followers-20261121-1'

const NAMES = { x: 'X', fanvue: 'Fanvue', telegram: 'Telegram', instagram: 'Instagram' }
export function platformIcon(platform) {
  const label = NAMES[platform]
  return label ? h('img', { class: 'platform-icon', src: `/static/platform-icons/${platform}.${platform === 'instagram' ? 'svg' : 'jpg'}`,
    alt: label, title: label, width: 14, height: 14, draggable: false,
    style: { width: '14px', height: '14px', flex: '0 0 14px', objectFit: 'contain', borderRadius: '2px', verticalAlign: 'middle' },
  }) : null
}
export function platformLabel(platform, label) {
  return h('span', { class: 'platform-label', style: { display: 'inline-flex', alignItems: 'center', gap: '6px', maxWidth: '100%' } },
    platformIcon(platform), h('span', { style: { minWidth: '0', overflowWrap: 'anywhere' } }, label))
}
