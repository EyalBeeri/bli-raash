// Offline layer for the installed app: the page and the last feed stay readable without a connection.
// The network is tried first so a good connection always shows the newest version; on a weak one we
// fall back to the saved copy after a few seconds instead of leaving a blank screen.
const CACHE = "bli-raash-v5";
const SHELL = ["./", "feed.json", "manifest.webmanifest", "icons/icon-192.png", "icons/apple-touch-icon.png"];
const NETWORK_WAIT_MS = 4000;

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
  const key = url.origin + url.pathname;  // one saved copy per file, whatever the query string
  const saved = () => caches.match(key).then(hit => hit || (req.mode === "navigate" ? caches.match(new URL("./", location).href) : undefined));

  const network = fetch(req);
  // Save a copy of every good response; registered up front so the browser keeps the worker alive for it.
  event.waitUntil(network.then(res => {
    if (res.ok) {
      const copy = res.clone();
      return caches.open(CACHE).then(c => c.put(key, copy));
    }
  }).catch(() => {}));
  const timeout = new Promise(resolve => setTimeout(resolve, NETWORK_WAIT_MS));

  event.respondWith((async () => {
    // Whichever comes first: the network, or the wait running out with a saved copy available.
    const first = await Promise.race([network.catch(() => null), timeout.then(() => "slow")]);
    if (first && first !== "slow") return first;
    const copy = await saved();
    if (copy) return copy;
    return network;  // nothing saved yet: keep waiting for the network (or its error)
  })());
});
