import { h } from './ui.js?v=creatorstudio-mobile-system-20260909'
import { api } from './api.js?v=creatorstudio-translation-20260910'

let nextId = 0

export function translationField(text, variants = []) {
  const cache = new Map()
  const seed = (items) => {
    for (const item of items || []) {
      if (item.text?.trim() && item.translation_de?.trim()) cache.set(item.text.trim(), Promise.resolve(item.translation_de))
    }
  }
  seed(variants)
  let timer, opened = false
  const output = h('div', { class: 'translation-output', role: 'tooltip', hidden: true })
  const button = h('button', { type: 'button', class: 'translation-button',
    'aria-label': 'Deutsche Übersetzung anzeigen', 'aria-expanded': 'false',
  }, h('span', { class: 'german-flag', 'aria-hidden': 'true' }))
  const group = h('div', { class: 'translation-heading' },
    h('label', { class: 'lbl' }, 'Text'), button, output)
  const close = () => {
    clearTimeout(timer)
    opened = false
    output.hidden = true
    button.setAttribute('aria-expanded', 'false')
    button.removeAttribute('aria-describedby')
  }
  output.id = 'translation-' + (++nextId)
  const show = async () => {
    clearTimeout(timer)
    opened = true
    output.hidden = false
    button.setAttribute('aria-expanded', 'true')
    button.setAttribute('aria-describedby', output.id)
    const source = text.value.trim()
    if (!source) { output.textContent = 'Bitte zuerst einen Text eingeben.'; return }
    output.textContent = 'Wird ins Deutsche übersetzt …'
    if (!cache.has(source)) {
      if (cache.size >= 20) cache.delete(cache.keys().next().value)
      const request = api.translateText(source).then(result => result.translation)
      cache.set(source, request)
      request.catch(() => { if (cache.get(source) === request) cache.delete(source) })
    }
    try {
      const translated = await cache.get(source)
      if (opened && text.value.trim() === source) output.textContent = translated
    } catch (error) {
      if (opened && text.value.trim() === source) output.textContent = 'Übersetzung nicht verfügbar: ' + error.message
    }
  }
  button.addEventListener('mouseenter', () => {
    if (matchMedia('(hover: hover)').matches) timer = setTimeout(show, 250)
  })
  group.addEventListener('mouseleave', close)
  button.addEventListener('focus', show)
  button.addEventListener('click', show)
  button.addEventListener('blur', close)
  group.addEventListener('keydown', event => {
    if (event.key === 'Escape' && opened) { event.stopPropagation(); close() }
  })
  text.addEventListener('input', close)
  return { node: h('div', { class: 'field' }, group, text), invalidate: close, seed }
}
