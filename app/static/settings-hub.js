// Navigation is read-only; warn before leaving independently edited forms/cards.
(() => {
  const hub = document.querySelector('.settings-hub')
  if (!hub) return
  let dirty = false, submitting = false
  hub.addEventListener('input', () => { dirty = true })
  hub.addEventListener('change', () => { dirty = true })
  hub.addEventListener('submit', () => { submitting = true })
  window.addEventListener('beforeunload', event => {
    if (!dirty || submitting) return
    // The common live tracker clears dirty state after confirmed AutoPost saves.
    if (window.CreatorStudioLive && !window.CreatorStudioLive.dirty(hub)) return
    event.preventDefault()
    event.returnValue = ''
  })
})()
