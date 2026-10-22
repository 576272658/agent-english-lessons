/* Shared helpers for every page: API calls, progress (stored on the server), nav, audio.
 * All URLs are relative so the app works under a sub-path (e.g. https://host/english/). */
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: opts.body ? { "content-type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!res.ok) throw new Error(`${opts.method || "GET"} ${path} → ${res.status}`);
    return res.json();
  }

  // Progress lives in data/progress.db on the server. Writes are fire-and-forget with a toast on failure.
  const progress = {
    checkins: {},
    favorites: new Set(),
    words: new Set(),
    async load() {
      const p = await api("api/progress");
      this.checkins = p.checkins; this.favorites = new Set(p.favorites); this.words = new Set(p.words);
    },
    isDone(id) { return id in this.checkins; },
    async setDone(id, done) {
      if (done) this.checkins[id] = new Date().toISOString(); else delete this.checkins[id];
      try { this.checkins = await api(`api/checkins/${id}`, { method: done ? "PUT" : "DELETE" }); } catch (e) { toast("打卡没有保存成功，请检查网络"); }
    },
    isFav(id) { return this.favorites.has(id); },
    async toggleFav(id) {
      const fav = !this.favorites.has(id);
      fav ? this.favorites.add(id) : this.favorites.delete(id);
      try { this.favorites = new Set(await api(`api/favorites/${id}`, { method: fav ? "PUT" : "DELETE" })); } catch (e) { toast("收藏没有保存成功，请检查网络"); }
      return fav;
    },
    hasWord(key) { return this.words.has(key); },
    async addWord(key, body) {
      try { this.words = new Set(await api(`api/words/${encodeURIComponent(key)}`, { method: "PUT", body })); toast("已加入生词本"); }
      catch (e) { toast("加入生词本失败，请检查网络"); }
    },
    async removeWord(key) {
      try { this.words = new Set(await api(`api/words/${encodeURIComponent(key)}`, { method: "DELETE" })); }
      catch (e) { toast("移除失败，请检查网络"); }
    },
    recordAttempt(a) {
      api("api/attempts", { method: "POST", body: a }).catch(() => toast("测验记录没有保存成功"));
    },
  };

  function nav(active) {
    const items = [["./", "courses", "课程"], ["expressions.html", "expressions", "表达库"], ["stats.html", "stats", "学习统计"]];
    return `<nav class="nav">${items.map(([href, key, label]) =>
      `<a href="${href}" class="${key === active ? "on" : ""}">${label}</a>`).join("")}</nav>`;
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

  window.App = { esc, api, progress, nav, play, speak, toast, fail, pref, wordQuizItem };
})();
