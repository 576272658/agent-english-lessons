/* Service worker: makes the app work offline.
 *
 * - App shell (pages, JS, CSS, icons): network-first, cached copy when offline.
 * - API reads (api/...): network-first with a short timeout, cached copy when offline. Responses
 *   served from cache carry "x-offline: 1" so pages can show they are offline.
 * - Media (media/..., api/word-audio/...): cache-first. Lessons are downloaded into the media cache by
 *   offline.js; video Range requests are answered from the cached file (iOS needs 206 responses).
 * Writes (POST/PUT/DELETE) are never cached; offline.js queues them and syncs later.
 */
const SHELL = "shell-v1";
const API = "api-v1";
const MEDIA = "media-v1";
const SHELL_FILES = [
  "./", "index.html", "lesson.html", "expressions.html", "stats.html",
  "app.js", "quiz.js", "word.js", "offline.js", "style.css", "manifest.webmanifest",
  "icons/icon.svg", "icons/apple-touch-icon.png", "icons/icon-192.png", "icons/icon-512.png", "icons/favicon-32.png",
];
const API_TIMEOUT = 4000;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  const keep = new Set([SHELL, API, MEDIA]);
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => !keep.has(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

const scopePath = new URL(self.registration.scope).pathname;
const rel = (url) => url.pathname.startsWith(scopePath) ? url.pathname.slice(scopePath.length) : url.pathname;

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  const path = rel(url);

  if (path.startsWith("media/") || path.startsWith("api/word-audio/")) {
    event.respondWith(media(req));
  } else if (path.startsWith("api/word/")) {
    return;  // look-ups go to the network; word.js falls back to the downloaded word bundles
  } else if (path.startsWith("api/")) {
    event.respondWith(networkFirst(req, API, API_TIMEOUT));
  } else {
    event.respondWith(networkFirst(req, SHELL, 6000, true));
  }
});

async function networkFirst(req, cacheName, timeout, ignoreSearch = false) {
  const cache = await caches.open(cacheName);
  try {
    const res = await Promise.race([
      fetch(req),
      new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), timeout)),
    ]);
    if (res.ok) cache.put(ignoreSearch ? stripSearch(req) : req, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(req, { ignoreSearch });
    if (hit) return withHeader(hit, "x-offline", "1");
    return new Response(JSON.stringify({ offline: true, error: "离线，且这部分内容还没有下载到本机" }), {
      status: 503, headers: { "content-type": "application/json", "x-offline": "1" },
    });
  }
}

async function media(req) {
  const cache = await caches.open(MEDIA);
  const key = stripSearch(req);
  const hit = await cache.match(key);
  if (hit) return req.headers.get("range") ? rangeFrom(hit, req.headers.get("range")) : hit;
  try {
    const res = await fetch(req);
    // word audio is tiny: keep it so words heard once also play offline
    if (res.status === 200 && rel(new URL(req.url)).startsWith("api/word-audio/")) cache.put(key, res.clone());
    return res;
  } catch (err) {
    return new Response("", { status: 503, headers: { "x-offline": "1" } });
  }
}

async function rangeFrom(full, header) {
  const buf = await full.arrayBuffer();
  const size = buf.byteLength;
  const m = /bytes=(\d*)-(\d*)/.exec(header) || [];
  let start, end;
  if (m[1] === "" && m[2]) { start = Math.max(0, size - Number(m[2])); end = size - 1; }
  else { start = Number(m[1] || 0); end = m[2] ? Math.min(Number(m[2]), size - 1) : size - 1; }
  if (start >= size || start > end) {
    return new Response("", { status: 416, headers: { "Content-Range": `bytes */${size}` } });
  }
  return new Response(buf.slice(start, end + 1), {
    status: 206,
    headers: {
      "Content-Type": full.headers.get("Content-Type") || "application/octet-stream",
      "Content-Range": `bytes ${start}-${end}/${size}`,
      "Content-Length": String(end - start + 1),
      "Accept-Ranges": "bytes",
    },
  });
}

function stripSearch(req) {
  const u = new URL(req.url); u.search = "";
  return new Request(u.toString());
}

function withHeader(res, name, value) {
  const headers = new Headers(res.headers); headers.set(name, value);
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers });
}
