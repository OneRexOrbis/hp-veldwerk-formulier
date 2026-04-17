// Service Worker — HP Veldwerk Formulier
// Cache de app-shell zodat hij ook start zonder netwerk (pincode-scherm)
const CACHE = 'hp-vw-v25';
const APP_SHELL = ['/', '/index.html'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => {
      // Stuur update-signaal naar alle open tabs
      self.clients.matchAll({ type: 'window' }).then(clients =>
        clients.forEach(c => c.postMessage({ type: 'SW_UPDATED' }))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API-calls altijd via netwerk
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(fetch(e.request));
    return;
  }

  // App-shell: cache-first, fallback naar netwerk
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
