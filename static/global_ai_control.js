(() => {
  const banner = document.querySelector('[data-global-ai-status]');
  if (!banner) return;
  const label = banner.querySelector('[data-global-ai-label]');
  const indicator = banner.hasAttribute('data-ai-indicator');
  const button = banner.querySelector('[data-ai-status-button]');
  const description = banner.querySelector('#inbox-ai-description');
  function paint(available, enabled) {
    banner.dataset.available = String(available);
    banner.dataset.enabled = String(enabled);
    if (indicator) {
      const message = !available ? 'Global AI auto replies unavailable' : enabled ? 'Global AI auto replies on' : 'Global AI auto replies off';
      label.textContent = message;
      button.setAttribute('aria-label', message); button.title = message; description.textContent = message;
    } else {
      label.textContent = !available ? 'AI STATE UNAVAILABLE — RELOAD TO CHECK' : enabled ? 'AI ONLINE' : 'AI OFFLINE — HUMAN STAFF ONLY';
      banner.style.borderColor = available && enabled ? '#258348' : '#ab2634';
      banner.style.background = available && enabled ? '#edf9f0' : '#fff1f2';
    }
  }
  if (indicator) {
    const close = () => { description.hidden = true; button.setAttribute('aria-expanded','false'); };
    button.addEventListener('click', () => { description.hidden = !description.hidden; button.setAttribute('aria-expanded',String(!description.hidden)); });
    document.addEventListener('click', event => { if (!banner.contains(event.target)) close(); });
    button.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
  }
  let running = false;
  async function refresh() {
    if (running || document.hidden) return;
    running = true;
    try {
      const response = await fetch('/crm/whatsapp/api/global-ai-state', {
        credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(8000)
      });
      if (!response.ok || response.redirected) throw new Error('State unavailable');
      const state = await response.json();
      if (typeof state.enabled !== 'boolean' || state.available !== true) throw new Error('Invalid state');
      paint(true, state.enabled);
    } catch (_) {
      paint(false, false);
    } finally { running = false; }
  }
  setInterval(refresh, 10000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
