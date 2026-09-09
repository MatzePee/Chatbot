import { api } from '../api.js?v=creatorstudio-layout-20260909'
import { h, field, spinner, clear } from '../ui.js?v=creatorstudio-layout-20260909'

export default function renderLogin({ onLogin }) {
  const email = h('input', { type: 'email', required: true, autofocus: true, autocomplete: 'username' })
  const password = h('input', { type: 'password', required: true, autocomplete: 'current-password' })
  const totp = h('input', { inputmode: 'numeric', placeholder: 'optional' })
  const error = h('div', { class: 'errbox', style: { display: 'none' } })
  const submit = h('button', { class: 'primary', type: 'submit', style: { width: '100%', justifyContent: 'center' } }, 'Anmelden')

  const form = h('form', {
    class: 'card pad login',
    onSubmit: async (e) => {
      e.preventDefault()
      error.style.display = 'none'
      clear(submit).appendChild(spinner())
      submit.disabled = true
      try {
        onLogin(await api.login(email.value, password.value, totp.value || undefined))
      } catch (err) {
        error.textContent = err.message
        error.style.display = 'block'
        clear(submit).appendChild(document.createTextNode('Anmelden'))
        submit.disabled = false
      }
    },
  },
    h('h1', { style: { margin: '0 0 2px', fontSize: '19px' } }, 'AutoPost'),
    h('p', { class: 'hint', style: { marginTop: 0, marginBottom: '16px' } }, 'Anmeldung erforderlich'),
    field('E-Mail', email),
    field('Passwort', password),
    field('2FA-Code (falls aktiviert)', totp),
    error,
    submit,
  )

  return h('div', { class: 'login-wrap' }, form)
}
