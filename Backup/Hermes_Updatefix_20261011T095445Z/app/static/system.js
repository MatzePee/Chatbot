
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

