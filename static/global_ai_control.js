(() => {
  const banner = document.querySelector('[data-global-ai-status]');
  if (!banner) return;
  const label = banner.querySelector('[data-global-ai-label]');
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
      banner.dataset.available = 'true';
      banner.dataset.enabled = String(state.enabled);
      label.textContent = state.enabled ? 'AI ONLINE' : 'AI OFFLINE — HUMAN STAFF ONLY';
      banner.style.borderColor = state.enabled ? '#258348' : '#ab2634';
      banner.style.background = state.enabled ? '#edf9f0' : '#fff1f2';
    } catch (_) {
      banner.dataset.available = 'false';
      label.textContent = 'AI STATE UNAVAILABLE — RELOAD TO CHECK';
      banner.style.borderColor = '#ab2634';
      banner.style.background = '#fff1f2';
    } finally { running = false; }
  }
  setInterval(refresh, 10000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
