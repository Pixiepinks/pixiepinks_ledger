/* No fetch interception or customer-data cache. This worker only handles staff push. */
'use strict';
async function reserveAlert(id) {
  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open('pixiepinks-staff-alerts', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('seen', {keyPath: 'id'});
    request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
  });
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction('seen', 'readwrite'), store = tx.objectStore('seen');
      let fresh = false;
      const request = store.get(id);
      request.onsuccess = () => { if (!request.result) { store.put({id, at: Date.now()}); fresh = true; } };
      const cursor = store.openCursor(); cursor.onsuccess = () => { const entry = cursor.result; if (!entry) return; if (entry.value.at < Date.now() - 86400000) entry.delete(); entry.continue(); };
      tx.oncomplete = () => resolve(fresh); tx.onerror = () => reject(tx.error);
    });
  } finally { db.close(); }
}
self.addEventListener('push', event => {
  event.waitUntil((async () => {
    let id;
    try { id = event.data.json().id; } catch (_) { return; }
    if (!Number.isSafeInteger(id) || id < 1) return;
    // Fetch with the current login/device cookie. Queued pushes cannot reveal old-user data.
    const response = await fetch(`/crm/notifications/api/push/${id}`, {credentials: 'same-origin', cache: 'no-store', redirect: 'error'}).catch(() => null);
    if (!response?.ok) return;
    const item = await response.json();
    const existing = await self.registration.getNotifications({tag: `staff-alert-${id}`});
    if (existing.length || !await reserveAlert(id)) return;
    const windows = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    windows.forEach(client => client.postMessage({type: 'STAFF_NOTIFICATION', id}));
    await self.registration.showNotification(item.title, {body: item.body, tag: `staff-alert-${id}`,
      icon: '/static/pwa-icon-192.png?v=pixiepinks-logo-2', badge: '/static/pwa-icon-192.png',
      silent: !item.sound || windows.some(c => c.focused), ...(item.sound && !windows.some(c => c.focused) ? {vibrate: [180, 80, 180]} : {}),
      data: {id}, renotify: false});
  })());
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const id = event.notification.data?.id;
    // Recheck permissions at tap time, including a different account on the same device.
    const response = Number.isSafeInteger(id) ? await fetch(`/crm/notifications/api/push/${id}`, {
      credentials: 'same-origin', cache: 'no-store', redirect: 'error'}).catch(() => null) : null;
    const item = response?.ok ? await response.json() : null;
    if (item?.csrf_token) await fetch(`/crm/notifications/api/${id}/read`, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': item.csrf_token}, body: '{}'
    }).catch(() => null);
    const path = item && /^\/crm\/whatsapp\?conversation=\d+$/.test(item.url) ? item.url : '/crm/notifications';
    const target = new URL(path, self.location.origin).href;
    const windows = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    const client = windows.find(c => new URL(c.url).origin === self.location.origin && new URL(c.url).pathname.startsWith('/crm/'));
    if (client) { await client.navigate(target); await client.focus(); }
    else await self.clients.openWindow(target);
  })());
});
self.addEventListener('message', event => {
  if (event.data?.type === 'STAFF_LOGOUT') event.waitUntil((async () => {
    (await self.registration.getNotifications()).forEach(n => n.close());
    const subscription = await self.registration.pushManager.getSubscription();
    if (subscription) await subscription.unsubscribe();
  })());
});
// Renew only an authenticated account that still explicitly enables this browser.
self.addEventListener('pushsubscriptionchange', event => {
  event.waitUntil((async () => {
    try {
      const response = await fetch('/crm/notifications/api', {credentials: 'same-origin', cache: 'no-store', redirect: 'error'});
      if (!response.ok) return;
      const state = await response.json();
      if (!state.push_configured || !state.devices.some(d => d.current && d.active)) return;
      let subscription = event.newSubscription || await self.registration.pushManager.getSubscription();
      if (!subscription) {
        const key = state.public_key, raw = atob(key.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - key.length % 4) % 4));
        subscription = await self.registration.pushManager.subscribe({userVisibleOnly: true,
          applicationServerKey: Uint8Array.from(raw, c => c.charCodeAt(0))});
      }
      const saved = await fetch('/crm/notifications/api/devices', {method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf_token},
        body: JSON.stringify({subscription: subscription.toJSON(), label: 'Renewed browser device'})});
      if (saved.ok) return;
    } catch (_) { /* A page can explain renewal failure without exposing device keys. */ }
    (await self.clients.matchAll({type: 'window'})).forEach(c => c.postMessage({type: 'STAFF_SUBSCRIPTION_CHANGED'}));
  })());
});
