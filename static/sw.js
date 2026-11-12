const CACHE_NAME = "hau-services-v2";
const CORE_ASSETS = [
    "/static/style.css",
    "/static/favicon.svg",
    "/static/offline.html",
    "/static/vendor/bootstrap/bootstrap.min.css",
    "/static/vendor/bootstrap/bootstrap.bundle.min.js",
    "/static/vendor/bootstrap-icons/bootstrap-icons.min.css",
    "/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2",
    "/static/vendor/chart.js/chart.umd.min.js",
    "/static/icons/icon-192.png",
    "/static/icons/icon-512.png"
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(CORE_ASSETS)).then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET") {
        return;
    }
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) {
        return;
    }

    if (url.pathname.startsWith("/static/")) {
        event.respondWith(
            caches.match(request).then((cached) => cached || fetch(request).then((response) => {
                const copy = response.clone();
                caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
                return response;
            }))
        );
        return;
    }

    if (request.mode === "navigate") {
        event.respondWith(
            fetch(request).catch(() => caches.match("/static/offline.html"))
        );
    }
});
