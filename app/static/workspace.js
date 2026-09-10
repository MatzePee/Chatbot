// Delegated because AutoPost creates the shared shell after checking its session.
(() => {
  function updateSettingsNavigation() {
    const links = [...document.querySelectorAll('[data-settings-nav] a')];
    const samePage = links.filter(link => link.pathname === location.pathname);
    const selected = samePage.find(link => link.hash === location.hash) || samePage[0];
    links.forEach(link => {
      link.classList.toggle('active', link === selected);
      if (link === selected) link.setAttribute('aria-current', link.hash ? 'location' : 'page');
      else link.removeAttribute('aria-current');
    });
  }
  updateSettingsNavigation();
  window.addEventListener('hashchange', updateSettingsNavigation);
  document.addEventListener('creatorpilot:refresh', updateSettingsNavigation);

  function setMenu(frame, open) {
    if (!frame) return;
    frame.classList.toggle('workspace-menu-open', open);
    frame.querySelector('.workspace-menu-button')?.setAttribute('aria-expanded', String(open));
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('.workspace-menu-button');
    if (button) {
      const frame = button.closest('.workspace-frame');
      setMenu(frame, !frame.classList.contains('workspace-menu-open'));
    } else if (event.target.closest('.workspace-backdrop, .workspace-nav a, .workspace-switcher a')) {
      setMenu(event.target.closest('.workspace-frame'), false);
    }
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const frame = document.querySelector('.workspace-menu-open');
    if (frame) { setMenu(frame, false); frame.querySelector('.workspace-menu-button')?.focus(); }
  });
})();
