/* Shared polling coordinator. Keeps DOM nodes, form edits, focus and scroll. */
(() => {
  const touched = new WeakSet();
  let interacting = 0, pending = 0;
  const controls = 'input,textarea,select,[contenteditable="true"]';
  for (const event of ['input', 'change']) document.addEventListener(event, e => {
    if (e.target.matches(controls)) touched.add(e.target);
    interacting = Date.now();
  }, true);
  for (const event of ['pointerdown', 'keydown', 'dragstart', 'drop'])
    document.addEventListener(event, () => { interacting = Date.now(); }, true);
  function dirty(node) {
    return (node.matches?.(controls) && touched.has(node)) || [...(node.querySelectorAll?.(controls) || [])].some(n => touched.has(n));
  }
  function busy() {
    return document.hidden || pending > 0 || Date.now() - interacting < 2000 ||
      document.activeElement?.matches(controls) || document.querySelector('.modal-bg,dialog[open],.pk-overlay.open') ||
      !!getSelection()?.toString();
  }
  const status = document.createElement('div');
  status.className = 'live-status'; status.textContent = 'Automatische Aktualisierung · alle 15 Sekunden';
  function start(refresh, host) {
    host?.prepend(status);
    let running = false;
    async function tick() {
      if (running || busy()) return;
      running = true;
      try {
        const updated = await refresh();
        status.textContent = updated === false ? 'Aktualisierung pausiert · Eingaben bleiben erhalten' :
          'Automatisch aktualisiert · ' + new Date().toLocaleTimeString('de-DE');
      } catch { status.textContent = 'Verbindung unterbrochen · nächster Versuch automatisch'; }
      finally { running = false; }
    }
    setInterval(tick, 15000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });
  }
  window.CreatorStudioLive = { start, busy, dirty, begin: () => { pending++; }, end: () => { pending = Math.max(0, pending - 1); },
    saved: node => { node?.querySelectorAll(controls).forEach(n => touched.delete(n)); } };

  if (document.getElementById('root')) return; // AutoPost supplies its own refresh callbacks.
  const main = document.querySelector('main');
  if (!main || !/^\/(?:$|queue$|chats(?:\/|$)|ppv(?:\/|$)|reports$|test$|settings(?:\/shared)?$|logs$|upload$|doku(?:\/|$))/.test(location.pathname)) return;
  const protectedSelector = 'script,style,canvas,[data-live-preserve],#si-body,#si-age,#si-scope,#upd-card,#ppv-picker,#pk-zoom,#backfill-status,.mem-live,.ppv-thumbs,.live-status';
  function key(n) {
    if (n.nodeType !== 1) return '';
    if (n.id) return 'id:' + n.id;
    if (n.dataset.uuid) return 'uuid:' + n.dataset.uuid;
    if (n.tagName === 'FORM') return 'form:' + n.getAttribute('action');
    if (n.classList.contains('ppv-card')) return 'ppv:' + n.dataset.name;
    return '';
  }
  function same(a, b) { return a.nodeType === b.nodeType && a.nodeName === b.nodeName && key(a) === key(b); }
  function morph(old, fresh) {
    if (old.nodeType === 3) { if (old.data !== fresh.data) old.data = fresh.data; return; }
    if (old.nodeType !== 1 || old.matches(protectedSelector)) return;
    if (old.matches('form') && dirty(old)) return;
    if (old.matches(controls)) {
      if (dirty(old)) return;
      if (old.tagName !== 'SELECT') { old.value = fresh.value; old.checked = fresh.checked; }
    }
    for (const a of [...old.attributes]) {
      if (a.name === 'open' && old.tagName === 'DETAILS') continue;
      if (!fresh.hasAttribute(a.name)) old.removeAttribute(a.name);
    }
    for (const a of fresh.attributes) {
      if (a.name === 'open' && old.tagName === 'DETAILS') continue;
      if (old.getAttribute(a.name) !== a.value) old.setAttribute(a.name, a.value);
    }
    const pool = [...old.childNodes];
    const used = new Set();
    let cursor = old.firstChild;
    for (const child of fresh.childNodes) {
      if (child.nodeType === 1 && child.matches('script,style')) continue;
      let match = pool.find(n => !used.has(n) && same(n, child));
      if (match) { used.add(match); morph(match, child); }
      else { match = child.cloneNode(true); match.querySelectorAll?.('script').forEach(n => n.remove()); }
      if (match !== cursor) old.insertBefore(match, cursor);
      cursor = match.nextSibling;
    }
    for (const child of pool) {
      if (used.has(child) || child.matches?.(protectedSelector) || dirty(child)) continue;
      child.remove();
    }
    if (old.tagName === 'SELECT' && !dirty(old)) old.value = fresh.value;
  }
  start(async () => {
    const response = await fetch(location.pathname + location.search, {
      cache: 'no-store', headers: { 'X-CreatorStudio-Refresh': '1' }, signal: AbortSignal.timeout(20000),
    });
    if (!response.ok || response.redirected) throw new Error('Refresh failed');
    const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
    if (!doc.querySelector('main') || busy()) return false;
    const x = scrollX, y = scrollY;
    const scroller = document.querySelector('.workspace-scroll');
    const contentX = scroller?.scrollLeft, contentY = scroller?.scrollTop;
    // Never remove a draft while its unsaved reply is being edited.
    for (const card of main.querySelectorAll('.draft')) {
      if (dirty(card) && !doc.getElementById(card.id)) return false;
    }
    morph(main, doc.querySelector('main'));
    const oldStatus = document.querySelector('#workspace-status'), newStatus = doc.querySelector('#workspace-status');
    if (oldStatus && newStatus) morph(oldStatus, newStatus);
    const queueLink = document.querySelector('.workspace-nav a[href="/queue"]');
    if (queueLink) { const a = doc.querySelector('.workspace-nav a[href="/queue"]'); if (a) queueLink.innerHTML = a.innerHTML; }
    const notices = document.querySelector('.workspace-notices'), freshNotices = doc.querySelector('.workspace-notices');
    if (notices && freshNotices) morph(notices, freshNotices);
    document.dispatchEvent(new Event('creatorpilot:refresh'));
    scrollTo(x, y);
    if (scroller) scroller.scrollTo(contentX, contentY);
    return true;
  }, document.querySelector('footer'));
})();
