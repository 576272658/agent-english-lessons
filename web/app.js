/* Shared helpers for every page: API calls, progress (server + offline outbox), nav, audio.
 * All URLs are relative so the app works under a sub-path (e.g. https://host/english/). */
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  let servedFromCache = false;  // some data on this page came from the offline cache

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: opts.body ? { "content-type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (res.headers.get("x-offline")) { servedFromCache = true; updateNet(); }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.offline ? body.error : `${opts.method || "GET"} ${path} → ${res.status}`);
    }
    return res.json();
  }

  // Progress lives in data/progress.db on the server. Every write is queued (offline.js) and synced,
  // so it works offline too; local state is updated immediately.
  const progress = {
    checkins: {},
    favorites: new Set(),
    words: new Set(),
    async load() {
      this.apply(await api("api/progress"));
    },
    // server snapshot + writes still waiting in the outbox
    async apply(snapshot) {
      this.checkins = { ...snapshot.checkins }; this.favorites = new Set(snapshot.favorites); this.words = new Set(snapshot.words);
      for (const op of await Offline.pendingOps()) this.applyOp(op);
    },
    applyOp(op) {
      if (op.type === "checkin") { if (op.done) this.checkins[op.lesson] = op.at; else delete this.checkins[op.lesson]; }
      else if (op.type === "favorite") op.on ? this.favorites.add(op.expr) : this.favorites.delete(op.expr);
      else if (op.type === "word_add") this.words.add(op.word);
      else if (op.type === "word_remove") this.words.delete(op.word);
    },
    write(op) { this.applyOp({ ...op, at: new Date().toISOString() }); return Offline.queue(op); },
    isDone(id) { return id in this.checkins; },
    setDone(id, done) { return this.write({ type: "checkin", lesson: id, done }); },
    isFav(id) { return this.favorites.has(id); },
    async toggleFav(id) {
      const on = !this.favorites.has(id);
      await this.write({ type: "favorite", expr: id, on });
      return on;
    },
    hasWord(key) { return this.words.has(key); },
    async addWord(key, body = {}) {
      await this.write({ type: "word_add", word: key, lesson: body.lesson || null, context_en: body.context_en || null, context_zh: body.context_zh || null });
      toast(navigator.onLine ? "已加入生词本" : "已加入生词本（联网后同步）");
    },
    removeWord(key) { return this.write({ type: "word_remove", word: key }); },
    recordAttempt(a) { this.write({ type: "attempt", ...a }); },
  };
  window.addEventListener("progress-synced", (e) => progress.apply(e.detail));

  // network / sync indicator in the nav bar
  async function updateNet() {
    const el = document.getElementById("net");
    if (!el || !window.Offline) return;
    const st = await Offline.status();
    const offline = !navigator.onLine || servedFromCache;
    const parts = [];
    if (offline) parts.push("离线模式");
    if (st.pending) parts.push(`${st.pending} 条记录待同步`);
    if (st.downloading) parts.push(`正在下载 ${st.downloading}…`);
    el.textContent = parts.join(" · ");
    el.className = "net" + (offline ? " off" : "") + (parts.length ? "" : " empty");
  }
  window.addEventListener("offline-change", updateNet);
  window.addEventListener("online", () => { servedFromCache = false; updateNet(); });
  window.addEventListener("offline", updateNet);

  function nav(active) {
    const items = [["./", "courses", "课程"], ["expressions.html", "expressions", "表达库"], ["stats.html", "stats", "学习统计"]];
    setTimeout(updateNet);
    return `<nav class="nav">${items.map(([href, key, label]) =>
      `<a href="${href}" class="${key === active ? "on" : ""}">${label}</a>`).join("")}<span id="net" class="net empty"></span></nav>`;
  }

  let audio = null;
  function play(src, fallbackText, rate = 1) {
    if (audio) audio.pause();
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    if (!src) return speak(fallbackText, rate);
    audio = new Audio(src); audio.playbackRate = rate;
    audio.play().catch(() => speak(fallbackText, rate));
    return audio;
  }
  function speak(text, rate = 1) {
    if (!text || !("speechSynthesis" in window)) return;
    const u = new SpeechSynthesisUtterance(text); u.lang = "en-US"; u.rate = rate; speechSynthesis.speak(u);
  }

  function toast(msg) {
    let el = document.getElementById("toast");
    if (!el) { el = document.createElement("div"); el.id = "toast"; el.className = "toast"; document.body.appendChild(el); }
    el.textContent = msg; el.classList.add("show");
    clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("show"), 3000);
  }

  function fail(root, err) {
    root.innerHTML = `<div class="empty">加载失败：${esc(err.message)}<br><button class="btn" onclick="location.reload()">重试</button></div>`;
  }

  // Local per-device preferences only (quiz mode, playback speed) -- never learning data.
  const pref = {
    get(k, d) { try { return localStorage.getItem("pref:" + k) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem("pref:" + k, v); } catch {} },
  };

  // A word-book row (api/words) as a quiz item
  const wordQuizItem = (w) => ({
    id: w.word, kind: "word", lesson: w.lesson_id, phrase: w.display, answer: w.display, phonetic: w.phonetic,
    pos: "单词", meaning_zh: w.meaning, tts: w.audio,
    examples: w.context_en ? [{ en: w.context_en, zh: w.context_zh || "" }] : [],
  });

  // Difficulty badges: fit for this learner (text + color), vocabulary level, speed
  function difficultyBadges(d, { long = false } = {}) {
    if (!d) return "";
    const fitIcon = { easy: "○", good: "✓", hard: "△", too_hard: "✕" }[d.fit] || "";
    const sp = d.speed ? `<span class="dchip">语速${esc(d.speed.speed_name)}${long ? `（${d.speed.wpm} 词/分）` : ""}</span>` : "";
    return `<span class="fit fit-${esc(d.fit)}" title="你大约认识其中 ${Math.round(d.coverage * 100)}% 的词">${fitIcon} ${long ? "对你" : ""}${esc(d.fit_name)}</span>`
      + `<span class="dchip">词汇·${esc(d.vocab_level_name)}</span>${sp}`;
  }

  // offline = no network at all, or the server is unreachable (e.g. Tailscale down) so data came from cache
  const isOffline = () => !navigator.onLine || servedFromCache;

  window.App = { esc, api, progress, nav, play, speak, toast, fail, pref, wordQuizItem, difficultyBadges, isOffline };

  // offline support: service worker + background download of the newest lessons
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
  window.addEventListener("load", () => { Offline.flush(); setTimeout(() => Offline.autoSync(), 1500); });
})();
