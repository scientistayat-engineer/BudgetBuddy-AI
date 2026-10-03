const V = "budgetbuddy-v4";
const SHELL = ["./", "index.html", "style.css", "app.js", "firebase-config.js", "api-config.js", "manifest.webmanifest", "images/icon.svg"];
const OK = ["www.gstatic.com", "fonts.googleapis.com", "fonts.gstatic.com"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(V).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((k) => Promise.all(k.filter((x) => x !== V).map((x) => caches.delete(x)))).then(() => self.clients.claim()));
});
// Stale-while-revalidate for the app shell, Firebase SDK modules and fonts.
// Firestore, Auth and our /api/ calls are not intercepted; Firestore handles its own offline cache.
self.addEventListener("fetch", (e) => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.pathname.startsWith("/api/") || (u.origin !== location.origin && !OK.includes(u.hostname))) return;
  e.respondWith(caches.open(V).then(async (c) => {
    const hit = await c.match(r);
    const net = fetch(r).then((res) => { if (res.ok || res.type === "opaque") c.put(r, res.clone()); return res; }).catch(() => hit);
    return hit || net;
  }));
});
