// Connection controls shared by the workspace; notification choices stay in their modules.
(() => {
  const form = document.getElementById('telegram-form');
  if (!form) return;
  const out = document.getElementById('tg-out');
  const chatButton = document.getElementById('tg-chatid');
  const testButton = document.getElementById('tg-test');
  const token = form.elements.namedItem('telegram_bot_token');
  const chatId = form.elements.namedItem('telegram_chat_id');

  async function request(path, extra = {}) {
    const previous = [chatButton.disabled, testButton.disabled];
    chatButton.disabled = testButton.disabled = true;
    window.CreatorStudioLive?.begin();
    out.textContent = 'Verbinde mit Telegram…';
    try {
      const response = await fetch(path, {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams({token: token.value, ...extra}),
      });
      const data = await response.json();
      if (!response.ok) return {ok: false, msg: data.detail || data.error || 'Anfrage fehlgeschlagen.'};
      return data;
    } catch {
      return {ok: false, msg: 'Verbindung fehlgeschlagen. Bitte erneut versuchen.'};
    } finally {
      [chatButton.disabled, testButton.disabled] = previous;
      window.CreatorStudioLive?.end();
    }
  }

  chatButton.addEventListener('click', async () => {
    const data = await request('/settings/shared/telegram-chatid');
    if (data.ok && data.chat_id) {
      chatId.value = data.chat_id;
      // Treat discovered values like typed input so polling cannot discard them.
      chatId.dispatchEvent(new Event('input', {bubbles: true}));
      out.textContent = '✅ ' + data.msg + ' – Chat-ID eingetragen. Bitte die Verbindung speichern.';
    } else out.textContent = '⚠ ' + data.msg;
  });
  testButton.addEventListener('click', async () => {
    const data = await request('/settings/shared/telegram-test', {chat_id: chatId.value});
    out.textContent = (data.ok ? '✅ ' : '⚠ ') + data.msg;
  });
})();
