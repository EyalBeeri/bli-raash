// Offline layer for the installed app: the page and the last feed stay readable without a connection.
// Everything is fetched from the network first, so a working connection always shows the newest version.
const CACHE = "bli-raash-v1";
const SHELL = ["./", "manifest.webmanifest", "icons/icon-192.png", "icons/apple-touch-icon.png"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;  // fonts and outlets: browser default
  // Store under the address without its cache-busting query, so the last copy can stand in when offline.
  const key = url.origin + url.pathname;
  event.respondWith(
    fetch(req)
      .then(res => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(key, copy));
        }
        return res;
      })
      .catch(() => caches.match(key).then(hit => hit || caches.match("./")))
  );
});
