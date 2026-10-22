/* Offline support (page side). Works with sw.js.
 *
 * Downloads: a downloaded lesson = its API JSON (api cache, via sw.js), every media file it uses
 * (media cache) and a dictionary bundle for its words (IndexedDB). Auto mode keeps the newest
 * N lessons that are not checked in downloaded, and drops auto-downloaded ones that fall out.
 *
 * Writes: every progress write (check-in, favorite, quiz answer, word book) goes into an IndexedDB
 * outbox with an id and the time it happened, then POST api/sync. Offline, it stays queued and is
 * sent on the next chance (back online, page load, app brought to front). The server applies each
 * id once, so re-sending is safe.
 */
(function () {
  const DB_NAME = "agent-english-lessons";
  const MEDIA = "media-v1";
  const AUTO_COUNT = 5;
  let dbp = null;

  function db() {
    if (!dbp) {
      dbp = new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = () => {
          req.result.createObjectStore("lessons", { keyPath: "id" });
          req.result.createObjectStore("outbox", { keyPath: "id" });
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
    }
    return dbp;
  }
  async function tx(store, mode, fn) {
    const d = await db();
    return new Promise((resolve, reject) => {
      const t = d.transaction(store, mode);
      const out = fn(t.objectStore(store));
      t.oncomplete = () => resolve(out && "result" in out ? out.result : out);
      t.onerror = () => reject(t.error);
    });
  }
  const all = (store) => tx(store, "readonly", (s) => s.getAll());
  const put = (store, v) => tx(store, "readwrite", (s) => s.put(v));
  const del = (store, k) => tx(store, "readwrite", (s) => s.delete(k));

  const emit = () => window.dispatchEvent(new CustomEvent("offline-change"));
  const randomId = () => "op-" + (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));
  let seq = 0;

  // ---------- outbox ----------

  async function pendingOps() {
    try { return (await all("outbox")).sort((a, b) => a.seq - b.seq); } catch { return []; }
  }

  async function queue(op) {
    const full = { ...op, id: randomId(), at: new Date().toISOString(), seq: Date.now() * 1000 + (seq++ % 1000) };
    await put("outbox", full);
    emit();
    flush();
  }

  let flushing = null;
  function flush() {
    if (flushing) return flushing;
    flushing = (async () => {
      try {
        const ops = await pendingOps();
        if (!ops.length) return null;
        const res = await fetch("api/sync", {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ ops: ops.map(({ seq, ...o }) => o) }),
        });
        if (!res.ok) return null;
        const body = await res.json();
        for (const id of [...body.applied, ...body.rejected]) await del("outbox", id);
        window.dispatchEvent(new CustomEvent("progress-synced", { detail: body.progress }));
        return body.progress;
      } catch { return null; } finally { flushing = null; emit(); }
    })();
    return flushing;
  }

  // ---------- downloads ----------

  async function records() { try { return await all("lessons"); } catch { return []; } }
  async function isDownloaded(id) { return (await records()).some((r) => r.id === id); }

  let current = null;  // lesson id being downloaded
  async function download(id, source = "manual") {
    current = id; emit();
    try {
      const res = await fetch(`api/lessons/${id}`, { cache: "no-store" });
      if (!res.ok || res.headers.get("x-offline")) throw new Error("需要联网才能下载");
      const L = await res.json();
      const urls = [...new Set([L.media, L.audio,
        ...L.sentences.map((s) => s.tts), ...L.expressions.map((e) => e.tts),
        ...(L.prestudy || []).map((w) => w.audio)].filter(Boolean))];
      const cache = await caches.open(MEDIA);
      let bytes = 0;
      for (const url of urls) {
        const hit = await cache.match(url);
        if (hit) { bytes += Number(hit.headers.get("content-length")) || 0; continue; }
        const r = await fetch(url);
        if (!r.ok) continue;  // e.g. word audio TTS unavailable: page falls back to speech synthesis
        const blob = await r.clone().blob();
        bytes += blob.size;
        await cache.put(url, r);
      }
      const words = await fetch(`api/lessons/${id}/words`).then((r) => r.ok ? r.json() : {}).catch(() => ({}));
      const old = (await records()).find((r) => r.id === id);
      await put("lessons", { id, title: L.title, source: old?.source === "manual" ? "manual" : source, urls, words, bytes,
        at: new Date().toISOString() });
      if (navigator.storage?.persist) navigator.storage.persist().catch(() => {});
      return true;
    } finally { current = null; emit(); }
  }

  async function remove(id) {
    const rec = (await records()).find((r) => r.id === id);
    if (!rec) return;
    const cache = await caches.open(MEDIA);
    const others = new Set((await records()).filter((r) => r.id !== id).flatMap((r) => r.urls));
    for (const url of rec.urls) if (!others.has(url)) await cache.delete(url);
    await del("lessons", id);
    emit();
  }

  async function lookupWord(key) {
    for (const r of await records()) if (r.words?.[key]) return r.words[key];
    return null;
  }

  // ---------- auto mode ----------

  const autoOn = () => App.pref.get("offline-auto", "on") !== "off";
  let syncing = null;
  function autoSync() {
    if (syncing || !navigator.onLine || !("caches" in window)) return syncing;
    syncing = (async () => {
      try {
        await flush();
        const res = await fetch("api/lessons", { cache: "no-store" });
        if (!res.ok || res.headers.get("x-offline")) return;
        const lessons = await res.json();
        const prog = await fetch("api/progress").then((r) => r.json());
        // keep the other pages' data fresh in the cache too
        ["api/expressions", "api/words", "api/stats", "api/settings"].forEach((u) => fetch(u).catch(() => {}));
        if (!autoOn()) return;
        const target = lessons.filter((l) => !(l.id in prog.checkins)).slice(-AUTO_COUNT).map((l) => l.id);
        const have = await records();
        for (const id of target) {
          if (!have.some((r) => r.id === id)) await download(id, "auto").catch(() => {});
        }
        for (const r of have) if (r.source === "auto" && !target.includes(r.id)) await remove(r.id);
      } catch { /* offline or server down: try again next time */ } finally { syncing = null; emit(); }
    })();
    return syncing;
  }

  async function status() {
    const recs = await records();
    return {
      lessons: recs.map((r) => ({ id: r.id, title: r.title, source: r.source, bytes: r.bytes })),
      bytes: recs.reduce((n, r) => n + (r.bytes || 0), 0),
      downloading: current,
      pending: (await pendingOps()).length,
      auto: autoOn(),
      autoCount: AUTO_COUNT,
    };
  }

  function setAuto(on) { App.pref.set("offline-auto", on ? "on" : "off"); if (on) autoSync(); emit(); }

  window.addEventListener("online", () => { flush(); autoSync(); });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { flush(); autoSync(); } });

  window.Offline = { queue, flush, pendingOps, download, remove, isDownloaded, records, lookupWord, autoSync, status, setAuto };
})();
