(() => {
  const card = document.getElementById('upd-card');
  if (!card) return;
  const body = document.getElementById('upd-body');
  const current = document.getElementById('upd-current');
  const check = document.getElementById('upd-now');
  const install = document.getElementById('upd-install');
  const form = document.getElementById('upd-install-form');
  const preview = card.dataset.preview === 'true';
  let loading = false;
  let latest = '';
  function esc(value) {
    const el = document.createElement('span');
    el.textContent = value == null ? '' : value;
    return el.innerHTML;
  }
  function render(data) {
    current.textContent = '· installiert: ' + (data.current || 'unbekannt') +
      (data.dirty ? ' (lokal geändert)' : '');
    latest = data.update_available && !data.error ? data.latest || '' : '';
    install.disabled = preview || !latest;
    install.textContent = latest ? `Update auf ${latest} installieren` : 'Update installieren';
    let html = '';
    if (data.error) html = `<p class="guard">⚠ ${esc(data.error)}</p>`;
    else if (latest) {
      html = `<div class="upd-new"><strong>Version ${esc(latest)} ist verfügbar.</strong>`;
      if (data.changelog?.length) {
        html += '<ul class="upd-log">' + data.changelog.slice(0, 10).map(line => `<li>${esc(line)}</li>`).join('');
        if (data.changelog.length > 10) html += `<li class="muted">… und ${data.changelog.length - 10} weitere</li>`;
        html += '</ul>';
      }
      html += '</div>';
    } else html = `<p class="muted">${data.checked_at ? '✓ Die Version ist aktuell.' : 'Noch nicht nach Updates gesucht. Klicke auf „Nach Updates suchen“.'}</p>`;
    if (data.checked_at) html += `<p class="muted small">Zuletzt geprüft: ${esc(new Date(data.checked_at * 1000).toLocaleString())}</p>`;
    body.innerHTML = html;
  }
  async function load(force = false) {
    if (loading) return;
    loading = true;
    check.disabled = true;
    install.disabled = true;
    if (force) body.textContent = 'Suche nach Updates…';
    try {
      const response = await fetch('/api/update-check' + (force ? '?force=1' : ''));
      if (!response.ok) throw new Error('Update-Status nicht abrufbar');
      render(await response.json());
    } catch {
      latest = '';
      body.textContent = 'Update-Status nicht abrufbar. Bitte erneut nach Updates suchen.';
    } finally {
      loading = false;
      check.disabled = false;
    }
  }
  check.addEventListener('click', () => load(true));
  form.addEventListener('submit', event => {
    if (preview || loading || !latest || !confirm(`Update auf ${latest} einspielen? Die Datenbank wird vorher gesichert, der Dienst startet danach neu.`)) event.preventDefault();
  });
  load();
  setInterval(() => { if (!document.hidden) load(); }, 15000);
})();
