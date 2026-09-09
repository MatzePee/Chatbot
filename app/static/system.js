
(function () {
  var body = document.getElementById('si-body'), age = document.getElementById('si-age');
  if (!body) return;

  function cls(p) { return p == null ? 'ok' : p >= 90 ? 'crit' : p >= 70 ? 'warn' : 'ok'; }
  function row(label, pct, text) {
    if (pct == null && !text) return '';
    var w = pct == null ? 0 : Math.max(0, Math.min(100, pct));
    return '<div class="si-row"><span class="si-label">' + label + '</span>' +
      '<span class="si-bar"><span class="si-fill ' + cls(pct) + '" style="width:' + w + '%"></span></span>' +
      '<span class="si-val">' + (text || (pct + ' %')) + '</span></div>';
  }

  function render(d) {
    if (!d.ok) {
      body.innerHTML = '<p class="muted">Auslastung nicht ermittelbar: ' +
        (d.error || 'unbekannt') + '</p>';
      return;
    }
    var html = '';
    html += row('CPU', d.cpu_percent,
      d.cpu_percent == null ? null : d.cpu_percent + ' %' +
      (d.cpu_cores ? ' von ' + d.cpu_cores + ' Kern' + (d.cpu_cores > 1 ? 'en' : '') : ''));
    html += row('Arbeitsspeicher', d.mem_percent,
      d.mem_total ? d.mem_used_h + ' / ' + d.mem_total_h : null);
    html += row('Festplatte', d.disk_percent,
      d.disk_total ? d.disk_used_h + ' / ' + d.disk_total_h : null);
    // Load ins Verhältnis zu den Kernen: 100 % = ein Kern dauerhaft voll belegt
    if (d.load) {
      html += row('Systemlast', d.load_ratio == null ? null : Math.min(100, d.load_ratio * 100),
        d.load.map(function (v) { return v.toFixed(2); }).join(' · '));
    }
    var meta = [];
    if (d.uptime) meta.push('⏱ Laufzeit: ' + d.uptime_h);
    if (d.proc_rss) meta.push('🤖 Bot-Prozess: ' + d.proc_rss_h);
    if (d.db_bytes) meta.push('🗄 Datenbank: ' + d.db_bytes_h);
    if (meta.length) html += '<div class="si-meta">' + meta.join('<span>·</span>') + '</div>';
    body.innerHTML = html || '<p class="muted">Keine Werte verfügbar.</p>';
    age.textContent = '· ' + new Date(d.ts * 1000).toLocaleTimeString();
    // Beschriftung ehrlich halten: im Container gelten die Limits des Containers,
    // direkt auf einem Server die Werte der ganzen Maschine.
    var scope = document.getElementById('si-scope');
    if (scope && d.env_label) {
      scope.textContent = (d.env_kind === 'container'
        ? 'Werte des ' + d.env_label + 's – nicht die des Hosts.'
        : 'Werte des gesamten Servers.') + ' Aktualisiert sich alle 10 Sekunden.';
    }
  }

  function load() {
    fetch('/api/sysinfo').then(function (r) { return r.json(); }).then(render)
      .catch(function (e) { age.textContent = '· nicht erreichbar'; });
  }
  load();
  // Nur aktualisieren, wenn der Tab sichtbar ist – kein Leerlauf im Hintergrund
  setInterval(function () { if (!document.hidden) load(); }, 10000);
})();

(function () {
  var body = document.getElementById('upd-body'), cur = document.getElementById('upd-current');
  if (!body) return;
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }

  function render(d) {
    cur.textContent = '· installiert: ' + (d.current || 'unbekannt') +
      (d.dirty ? ' (lokal geändert)' : '');
    var h = '';
    if (d.error) {
      h += '<p class="guard">⚠ ' + esc(d.error) + '</p>';
    } else if (d.update_available) {
      h += '<div class="upd-new"><strong>Version ' + esc(d.latest) + ' ist verfügbar.</strong>';
      if (d.changelog && d.changelog.length) {
        h += '<ul class="upd-log">';
        d.changelog.slice(0, 10).forEach(function (c) { h += '<li>' + esc(c) + '</li>'; });
        if (d.changelog.length > 10) h += '<li class="muted">… und ' + (d.changelog.length - 10) + ' weitere</li>';
        h += '</ul>';
      }
      h += '</div>';
      h += '<form method="post" action="/system/update" class="draft-actions" ' +
           'onsubmit="return confirm(\'Update auf ' + esc(d.latest) +
           ' einspielen? Die Datenbank wird vorher gesichert, der Dienst startet danach neu.\');">' +
           '<button class="btn-primary">⬇ Update auf ' + esc(d.latest) + ' installieren</button></form>';
    } else {
      h += '<p class="muted">✓ Die Version ist aktuell.</p>';
    }
    h += '<p class="muted small" style="margin-top:8px">' +
         (d.checked_at ? 'Zuletzt geprüft: ' + new Date(d.checked_at * 1000).toLocaleString() : 'Noch nicht geprüft') +
         ' · <a href="#" id="upd-now">jetzt nachsehen</a></p>';
    body.innerHTML = h;
    window.protectPreviewForms?.();
    var link = document.getElementById('upd-now');
    if (link) link.addEventListener('click', function (e) {
      e.preventDefault(); body.innerHTML = '<p class="muted">Frage Repository ab…</p>'; load(true);
    });
  }
  function load(force) {
    fetch('/api/update-check' + (force ? '?force=1' : ''))
      .then(function (r) { return r.json(); }).then(render)
      .catch(function () { body.innerHTML = '<p class="muted">Update-Status nicht abrufbar.</p>'; });
  }
  load(false);
  setInterval(function () { if (!document.hidden) load(false); }, 15000);
})();

