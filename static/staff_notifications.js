(() => {
  'use strict';
  const bell = document.getElementById('staff-alert-bell'), page = document.getElementById('staff-notification-settings');
  if (!bell && !page) return;
  let state, csrf = '', registration, installPrompt, audio, loading = false;
  const $ = id => document.getElementById(id);
  const say = text => { if ($('notification-diagnostics')) $('notification-diagnostics').textContent = text; };
  async function api(path = '', data) {
    const response = await fetch('/crm/notifications/api' + path, {credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      ...(data === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(data)})});
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Notification request failed');
    return result;
  }
  function diagnostics() {
    if (!window.isSecureContext) return 'HTTPS is required for device notifications.';
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return 'Web Push is unsupported in this browser. Use current Android Chrome.';
    if (!state?.push_configured) return 'Server Web Push is not configured. Notification history is available.';
    if (Notification.permission === 'denied') return 'Notifications are blocked. Chrome → Site settings → Notifications → Allow, then return here.';
    if (Notification.permission !== 'granted') return 'Tap Enable this device and allow notifications.';
    const device = state.devices.find(d => d.current && d.active);
    return device ? 'This device is registered. Test a notification to verify Android delivery.' : 'This device is unsubscribed. Tap Enable this device.';
  }
  function renderNotices(target, notices) {
    if (!target) return;
    target.replaceChildren();
    if (!notices.length) { target.textContent = 'No recent notifications.'; return; }
    notices.forEach(item => {
      const row = document.createElement('div'); row.className = 'staff-alert-item' + (item.read ? '' : ' staff-alert-unread');
      const link = document.createElement('a');
      // The server constructs these destinations; also enforce a local allowlist.
      link.href = /^\/crm\/whatsapp\?conversation=\d+$/.test(item.url) ? item.url : '/crm/notifications';
      const title = document.createElement('strong'); title.textContent = item.title.replace(/^PixiePinks CRM — /, '');
      const preview = document.createElement('span'); preview.textContent = item.body;
      link.append(title, preview);
      link.addEventListener('click', event => { event.preventDefault(); api(`/${item.id}/read`, {}).catch(() => {}).finally(() => location.assign(link.href)); });
      const time = document.createElement('small'); time.textContent = new Date(item.created_at).toLocaleString('en-LK', {timeZone: 'Asia/Colombo'}) + ' · ' + (item.read ? 'Read' : 'Unread');
      const read = document.createElement('button'); read.type = 'button'; read.textContent = 'Mark read'; read.hidden = item.read;
      read.addEventListener('click', () => api(`/${item.id}/read`, {}).then(refresh).catch(e => say(e.message)));
      row.append(link, time, read); target.append(row);
    });
  }
  function chime() {
    if (!audio || !state.preferences.sound || document.hidden || !document.hasFocus()) return;
    const oscillator = audio.createOscillator(), gain = audio.createGain();
    oscillator.connect(gain); gain.connect(audio.destination); oscillator.frequency.value = 740;
    gain.gain.setValueAtTime(.08, audio.currentTime); gain.gain.exponentialRampToValueAtTime(.001, audio.currentTime + .22);
    oscillator.start(); oscillator.stop(audio.currentTime + .24);
  }
  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const result = await api(); csrf = result.csrf_token; state = result;
      const key = `staff-alert-last-${state.user_id}`, stored = sessionStorage.getItem(key), last = Number(stored || 0);
      const newest = Math.max(0, ...state.notices.map(n => n.id));
      if (stored && state.notices.some(n => n.id > last && !n.read && n.type !== 'TEST')) chime();
      sessionStorage.setItem(key, String(Math.max(last, newest)));
      if ($('staff-alert-count')) { $('staff-alert-count').textContent = String(state.unread); $('staff-alert-count').hidden = !state.unread; }
      renderNotices($('staff-alert-list'), state.notices); renderNotices($('notification-history'), state.notices);
      if (page) {
        const form = $('notification-preferences');
        if (!form.contains(document.activeElement)) Object.entries(state.preferences).forEach(([key, value]) => form.elements[key].checked = value);
        $('notification-devices').replaceChildren();
        state.devices.forEach(d => {
          const row = document.createElement('div'); row.className = 'notification-device';
          const label = document.createElement('span'); label.textContent = `${d.label}${d.current ? ' (this browser)' : ''} · ${d.active ? 'Registered' : 'Inactive'} · ${d.status || ''}`;
          const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove device'; remove.disabled = !d.active;
          remove.addEventListener('click', () => api(`/devices/${d.id}/remove`, {}).then(async () => { if (d.current) await unsubscribe(); await refresh(); }).catch(e => say(e.message)));
          row.append(label, remove); $('notification-devices').append(row);
        });
        say(diagnostics());
      }
    } catch (_) { say('Notifications unavailable. Check your connection and sign-in.'); }
    finally { loading = false; }
  }
  function bytes(value) { const raw = atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4)); return Uint8Array.from(raw, c => c.charCodeAt(0)); }
  async function worker() {
    if (!registration) registration = await navigator.serviceWorker.register('/crm/staff-push-sw.js', {scope: '/crm/'});
    await navigator.serviceWorker.ready;
    return registration;
  }
  async function unsubscribe() {
    if (!('serviceWorker' in navigator)) return;
    const reg = await navigator.serviceWorker.getRegistration('/crm/');
    const sub = await reg?.pushManager.getSubscription(); if (sub) await sub.unsubscribe();
    (await reg?.getNotifications() || []).forEach(n => n.close());
  }
  async function enable(askPermission = true) {
    if (!state?.push_configured) throw new Error('Server Web Push is not configured.');
    if (!('Notification' in window) || !('PushManager' in window)) throw new Error('Use current Android Chrome over HTTPS.');
    const permission = askPermission ? await Notification.requestPermission() : Notification.permission;
    if (permission !== 'granted') throw new Error('Allow notifications in Chrome site settings first.');
    const reg = await worker();
    let subscription = await reg.pushManager.getSubscription();
    const expected = bytes(state.public_key);
    if (subscription?.options.applicationServerKey && !new Uint8Array(subscription.options.applicationServerKey).every((b, i) => b === expected[i])) {
      await subscription.unsubscribe(); subscription = null;
    }
    if (!subscription) subscription = await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: expected});
    await api('/devices', {subscription: subscription.toJSON(), label: /Android/i.test(navigator.userAgent) ? 'Android Chrome' : 'Browser device'});
    await refresh();
  }
  function unlockSound() {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return;
    audio ||= new Audio(); audio.resume().catch(() => {});
  }
  $('staff-alert-sound')?.addEventListener('click', () => { unlockSound(); chime(); });
  document.addEventListener('pointerdown', () => { if (state?.preferences.sound) unlockSound(); });
  bell?.addEventListener('click', () => { const panel = $('staff-alert-panel'); panel.hidden = !panel.hidden; bell.setAttribute('aria-expanded', String(!panel.hidden)); if (!panel.hidden) refresh(); });
  document.addEventListener('click', e => { if ($('staff-alerts') && !$('staff-alerts').contains(e.target)) { $('staff-alert-panel').hidden = true; bell?.setAttribute('aria-expanded', 'false'); } });
  document.querySelectorAll('a[href="/logout"]').forEach(link => link.addEventListener('click', () => {
    navigator.serviceWorker?.controller?.postMessage({type: 'STAFF_LOGOUT'});
    navigator.serviceWorker?.getRegistration('/crm/').then(reg => reg?.active?.postMessage({type: 'STAFF_LOGOUT'}));
  }));
  navigator.serviceWorker?.addEventListener('message', event => {
    if (event.data?.type === 'STAFF_NOTIFICATION') refresh();
    if (event.data?.type === 'STAFF_SUBSCRIPTION_CHANGED') enable(false).catch(() => say('Device subscription changed. Open settings and enable this device again.'));
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.addEventListener('online', refresh);
  window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); installPrompt = event; if ($('push-install')) $('push-install').hidden = false; });
  if (page) {
    $('push-enable').addEventListener('click', () => enable().catch(e => say(e.message)));
    $('push-disable').addEventListener('click', async () => {
      try { for (const d of state.devices.filter(d => d.current && d.active)) await api(`/devices/${d.id}/remove`, {}); await unsubscribe(); await refresh(); } catch (e) { say(e.message); }
    });
    $('push-test').addEventListener('click', () => api('/test', {}).then(() => say('Test queued. Check the Android notification shade; provider acceptance is not proof of display.')).catch(e => say(e.message)));
    $('sound-enable').addEventListener('click', async () => { const Audio = window.AudioContext || window.webkitAudioContext; if (!Audio) return say('Audio is unsupported.'); audio ||= new Audio(); await audio.resume(); chime(); say('Sound unlocked for this open CRM. Also enable the sound preference.'); });
    $('notification-preferences').addEventListener('submit', event => { event.preventDefault(); const form = event.currentTarget;
      api('/preferences', Object.fromEntries(['messages', 'assignments', 'reminders', 'sound'].map(key => [key, form.elements[key].checked]))).then(refresh).catch(e => say(e.message)); });
    $('push-install').addEventListener('click', async () => { if (installPrompt) { await installPrompt.prompt(); installPrompt = null; $('push-install').hidden = true; } });
    if (page.dataset.manager === 'true') {
      api('/control').then(data => {
        const form = $('notification-controls');
        ['shared_users', 'escalation_users'].forEach(key => data.staff.filter(u => key !== 'escalation_users' || u.manager).forEach(u => {
          const option = document.createElement('option'); option.value = u.id; option.textContent = u.name; option.selected = data.control[key].includes(u.id); form.elements[key].append(option);
        }));
        ['reminders_enabled', 'escalations_enabled'].forEach(key => form.elements[key].checked = data.control[key]);
        ['reminder_minutes', 'escalation_minutes'].forEach(key => form.elements[key].value = data.control[key]);
      }).catch(e => say(e.message));
      $('notification-controls').addEventListener('submit', event => { event.preventDefault(); const form = event.currentTarget, data = {};
        ['shared_users', 'escalation_users'].forEach(key => data[key] = [...form.elements[key].selectedOptions].map(o => Number(o.value)));
        ['reminders_enabled', 'escalations_enabled'].forEach(key => data[key] = form.elements[key].checked);
        ['reminder_minutes', 'escalation_minutes'].forEach(key => data[key] = Number(form.elements[key].value));
        api('/control', data).then(() => say('Team settings saved. Existing conversation permissions still apply.')).catch(e => say(e.message));
      });
    }
  }
  refresh().then(async () => {
    // Renew only a device that this account still explicitly enables; never undo revocation.
    if (state?.push_configured && state.devices.some(d => d.current && d.active) && 'Notification' in window && Notification.permission === 'granted') {
      await enable(false).catch(() => say('Device renewal failed. Open settings and enable this device again.'));
    }
  });
  setInterval(() => { if (!document.hidden) refresh(); }, 15000);
})();
