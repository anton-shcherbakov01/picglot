// PicGlot service worker.
// Caches only the app shell and immutable static assets — never a job
// result, a share link or anything under /app or /admin.
const SHELL_CACHE = "picglot-shell-v1";
const STATIC_CACHE = "picglot-static-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.add("/")));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== SHELL_CACHE && key !== STATIC_CACHE)
            .map((key) => caches.delete(key)),
        ),
      ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never touch private areas, the API, or share links — network only.
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname.includes("/app/") ||
    url.pathname.startsWith("/share/") ||
    url.pathname.includes("/admin")
  ) {
    return;
  }

  // Immutable Next.js build assets: cache-first, forever.
  if (url.pathname.startsWith("/_next/static/")) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const hit = await cache.match(request);
        if (hit) return hit;
        const response = await fetch(request);
        if (response.ok) cache.put(request, response.clone());
        return response;
      }),
    );
    return;
  }

  // Everything else (pages): network-first, cached fallback for offline.
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(request, clone));
        }
        return response;
      })
      .catch(() =>
        caches.match(request).then((hit) => hit || caches.match("/")),
      ),
  );
});

self.addEventListener("message", (event) => {
  if (event.data === "SKIP_WAITING") self.skipWaiting();
});
