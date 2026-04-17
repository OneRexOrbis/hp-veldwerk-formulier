// Service Worker — HP Veldwerk Formulier
// App-shell cache is offline-fallback, maar index.html gebruikt
// network-first zodat updates direct doorkomen (voorkomt oude JS met
// nieuwe backend-contract mismatch).
const CACHE = 'hp-vw-v26';
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
      self.clients.matchAll({ type: 'window' }).then(clients =>
        clients.forEach(c => c.postMessage({ type: 'SW_UPDATED' }))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API-calls altijd via netwerk, nooit cachen
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(fetch(e.request));
    return;
  }

  // App-shell (/, /index.html): NETWORK-FIRST met cache-fallback
  // Dit zorgt dat nieuwe frontend direct wordt geserveerd (voorkomt
  // dat oude gecachte JS tegen nieuwe backend praat).
  const isAppShell = url.pathname === '/' || url.pathname === '/index.html';
  if (isAppShell) {
    e.respondWith(
      fetch(e.request)
        .then(resp => {
          // Sla verse kopie op in de cache voor offline gebruik
          const clone = resp.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone)).catch(() => {});
          return resp;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // Overige assets: cache-first
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
