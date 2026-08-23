const SHELL_CACHE = "orbit-alpha-shell-20260823-decision-workspace-v6";
const APP_SHELL = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./favicon.svg",
  "./styles.css?v=20260823-decision-workspace-v6",
  "./app-default-settings.js?v=20260821-stable-share-v1",
  "./app.js?v=20260823-decision-workspace-v6",
  "./vendor/lightweight-charts.standalone.production.js",
  "./icons/house.svg",
  "./icons/chart-no-axes-combined.svg",
  "./icons/brain-circuit.svg",
  "./icons/bell.svg",
  "./icons/calendar-days.svg",
  "./icons/settings.svg",
  "./icons/download.svg",
  "./icons/refresh-cw.svg",
  "./icons/search.svg",
  "./icons/orbit-alpha-192.png",
  "./icons/orbit-alpha-512.png"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(SHELL_CACHE).then(function (cache) {
      return cache.addAll(APP_SHELL);
    })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (key) {
        return key.indexOf("orbit-alpha-shell-") === 0 && key !== SHELL_CACHE;
      }).map(function (key) {
        return caches.delete(key);
      }));
    }).then(function () {
      return self.clients.claim();
    })
  );
});

self.addEventListener("message", function (event) {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;
  var url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.indexOf("/api/") >= 0 || url.pathname.endsWith("/ws")) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).then(function (response) {
        if (response && response.ok) {
          var copy = response.clone();
          caches.open(SHELL_CACHE).then(function (cache) { cache.put("./index.html", copy); });
        }
        return response;
      }).catch(function () {
        return caches.match("./index.html").then(function (response) {
          return response || caches.match("./");
        });
      })
    );
    return;
  }

  const isMutableAppAsset = ["/app.js", "/app-default-settings.js", "/styles.css"].some(function (pathname) {
    return url.pathname.endsWith(pathname);
  });
  if (isMutableAppAsset) {
    event.respondWith(
      fetch(request).then(function (response) {
        if (response && response.ok) {
          var copy = response.clone();
          caches.open(SHELL_CACHE).then(function (cache) { cache.put(request, copy); });
        }
        return response;
      }).catch(function () {
        return caches.match(request);
      })
    );
    return;
  }

  event.respondWith(
    caches.match(request).then(function (cached) {
      var network = fetch(request).then(function (response) {
        if (response && response.ok) {
          var copy = response.clone();
          caches.open(SHELL_CACHE).then(function (cache) { cache.put(request, copy); });
        }
        return response;
      });
      return cached || network;
    })
  );
});
