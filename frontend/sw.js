/* TabForge service worker: instant-start shell cache.
 *
 * The cache name carries the app version — bump VERSION together with
 * pyproject/CHANGELOG on release. A new deploy ships a byte-different
 * sw.js, the browser installs it, and `activate` below deletes every
 * older cache, so a stale shell can never stick after an update.
 */
"use strict";

const VERSION = "0.2.0";
const CACHE = `tabforge-shell-${VERSION}`;
const SHELL = [
  "/",
  "/style.css",
  "/app.js",
  "/manifest.json",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // API is always live: job state, uploads, downloads must never be cached
  if (url.pathname.startsWith("/api/")) return;
  // cross-origin (CDN alphaTab, fonts): let the browser handle it
  if (url.origin !== location.origin) return;
  if (e.request.method !== "GET") return;

  e.respondWith(
    caches.match(e.request, { ignoreSearch: url.pathname === "/" })
      .then((hit) => hit || fetch(e.request).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
        return res;
      })));
});
