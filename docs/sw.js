// 주식 레이더 서비스 워커: 앱 화면 캐시(오프라인 열기) + 푸시 알림
const CACHE = "radar-v1";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png", "apple-touch-icon.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin) return;
  if (/\.(enc|json)$/.test(u.pathname)) return;               // 데이터는 항상 새로
  e.respondWith(fetch(e.request).then(r => { const c = r.clone(); caches.open(CACHE).then(x => x.put(e.request, c)); return r; })
    .catch(() => caches.match(e.request).then(r => r || caches.match("index.html"))));
});
self.addEventListener("push", e => {
  let d = {}; try { d = e.data.json(); } catch { d = { title: "주식 레이더", body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "주식 레이더", {
    body: d.body || "", icon: "icon-192.png", badge: "icon-192.png", data: { url: d.url || "./" }, tag: "radar", renotify: true }));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(cs => {
    for (const c of cs) if ("focus" in c) { c.postMessage("refresh"); return c.focus(); }
    return self.clients.openWindow(e.notification.data?.url || "./");
  }));
});
