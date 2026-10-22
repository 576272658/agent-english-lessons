/* Expression quiz (typing + flashcard), shared by lesson pages and the expression library.
 * Generated pages load assets/quiz.js. Usage:
 *   Quiz.mount(el, items, { base: "", onFinish(results) {} })
 * items: [{ id, phrase, answer, phonetic, pos, meaning_zh, examples, tts }]
 * base:  prefix prepended to item.tts (path of the page relative to where tts paths start)
 */
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} },
  };
  const norm = (w) => w.toLowerCase().replace(/[’‘`]/g, "'").trim();
  const MAX_TRIES = 3;
  const isFav = (id) => !!store.get("fav:" + id);
  const toggleFav = (id) => store.set("fav:" + id, isFav(id) ? null : "1");

  let audio = null;
  function say(item, base) {
    if (audio) audio.pause();
    if (item.tts) { audio = new Audio(base + item.tts); audio.play().catch(() => speak(item.answer)); }
    else speak(item.answer);
  }
  function speak(text) {
    if (!("speechSynthesis" in window)) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text); u.lang = "en-US"; speechSynthesis.speak(u);
  }

  function mount(root, allItems, opts = {}) {
    const base = opts.base || "";
    const st = { mode: store.get("quiz:mode") || "typing", items: allItems.slice(), i: 0, results: {}, tries: MAX_TRIES, revealed: false, flipped: false, finished: false };
    let advanceTimer = 0;

    function reset(items) {
      clearTimeout(advanceTimer);
      Object.assign(st, { items, i: 0, results: {}, tries: MAX_TRIES, revealed: false, flipped: false, finished: false });
      draw();
    }
    function next() {
      clearTimeout(advanceTimer);
      st.i += 1; st.tries = MAX_TRIES; st.revealed = false; st.flipped = false;
      if (st.i >= st.items.length) {
        st.finished = true;
        if (opts.onFinish) opts.onFinish(st.results);
      }
      draw();
    }
    function mark(result) {
      const item = st.items[st.i];
      if (!(item.id in st.results)) st.results[item.id] = result;
    }

    function answerPanel(item) {
      const ex = item.examples?.[0];
      return `<div class="q-answer">
        <div class="q-phrase">${esc(item.phrase)}</div>
        <div class="q-ipa">${esc(item.phonetic)}</div>
        ${ex ? `<div class="q-ex">${esc(ex.en)}<span>${esc(ex.zh)}</span></div>` : ""}
      </div>`;
    }

    function draw() {
      if (!st.items.length) { root.innerHTML = `<div class="empty">没有可以测验的表达</div>`; return; }
      const tabs = `<div class="q-tabs">
        <button data-mode="flash" class="${st.mode === "flash" ? "on" : ""}">🗂 Flashcard</button>
        <button data-mode="typing" class="${st.mode === "typing" ? "on" : ""}">⌨️ Typing</button></div>`;

      if (st.finished) {
        const unknown = st.items.filter((x) => st.results[x.id] !== "known");
        const known = st.items.length - unknown.length;
        root.innerHTML = `${tabs}<div class="quiz-card q-done">
          <div class="q-score">${known} / ${st.items.length}</div>
          <div class="tip">${unknown.length ? "下面这些还要再练练：" : "全部掌握，太棒了！🎉"}</div>
          ${unknown.length ? `<ul class="q-list">${unknown.map((x) => `<li><b>${esc(x.phrase)}</b> ${esc(x.meaning_zh)}</li>`).join("")}</ul>` : ""}
          <div class="btn-row" style="justify-content:center">
            ${unknown.length ? `<button class="btn primary" data-act="retry-unknown">只练不认识的（${unknown.length}）</button>` : ""}
            <button class="btn" data-act="restart">再来一遍</button>
          </div></div>`;
        wire();
        return;
      }

      const item = st.items[st.i];
      const words = item.answer.split(" ");
      const head = `<div class="q-progress">${st.i + 1} / ${st.items.length}</div>
        <button class="q-star ${isFav(item.id) ? "on" : ""}" data-act="fav" title="收藏">${isFav(item.id) ? "★" : "☆"}</button>
        <div class="q-zh">${esc(item.meaning_zh)} <button class="q-say" data-act="say" title="播放发音">🔊</button></div>
        ${item.pos ? `<div><span class="q-pos">${esc(item.pos)}</span></div>` : ""}`;

      let body, foot;
      if (st.mode === "typing") {
        body = `<div class="q-inputs">${words.map((w, k) => `<input class="q-in" data-k="${k}" style="width:${Math.max(3, w.length) + 1.5}ch"
            autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" ${st.revealed ? "disabled" : ""}>`).join("")}</div>
          <div class="tip">${words.length > 1 ? `请输入 ${words.length} 个单词的短语` : "请输入单词"}，回车提交</div>
          <div class="q-tries">${st.revealed ? "" : `💪 剩余尝试次数：${st.tries} 次`}</div>
          <div class="q-feedback" data-fb></div>
          ${st.revealed ? answerPanel(item) : ""}`;
      } else {
        body = st.flipped ? answerPanel(item)
          : `<button class="btn" data-act="flip" style="margin:28px auto;display:flex">点击翻面查看答案</button>`;
      }
      if (st.revealed) foot = `<button class="q-btn next" data-act="next">下一题 →</button>`;
      else foot = `<button class="q-btn no" data-act="unknown">✕ 不认识</button>
        ${st.mode === "typing" ? `<button class="q-btn peek" data-act="peek">👁 查看答案</button>` : ""}
        <button class="q-btn yes" data-act="known">✓ 认识</button>`;

      root.innerHTML = `${tabs}<div class="quiz-card"><div class="q-body">${head}${body}</div><div class="q-foot">${foot}</div></div>`;
      wire();
      if (st.mode === "typing" && !st.revealed) root.querySelector(".q-in")?.focus({ preventScroll: true });
    }

    function check() {
      const item = st.items[st.i];
      const inputs = [...root.querySelectorAll(".q-in")];
      const want = item.answer.split(" ");
      const got = inputs.map((x) => x.value);
      if (got.every((g) => !g.trim())) return;
      const ok = want.map((w, k) => norm(w) === norm(got[k] || ""));
      inputs.forEach((x, k) => { x.classList.toggle("ok", ok[k]); x.classList.toggle("bad", !ok[k]); });
      const fb = root.querySelector("[data-fb]");
      if (ok.every(Boolean)) {
        mark("known");
        say(item, base);
        fb.innerHTML = `<span class="good">✓ 正确！</span>`;
        inputs.forEach((x) => x.disabled = true);
        advanceTimer = setTimeout(next, 1300);
        return;
      }
      st.tries -= 1;
      if (st.tries <= 0) { mark("unknown"); st.revealed = true; draw(); say(item, base); return; }
      root.querySelector(".q-tries").textContent = `💪 剩余尝试次数：${st.tries} 次`;
      fb.innerHTML = `<span class="badc">再想想，标红的单词不对</span>`;
      inputs.find((x, k) => !ok[k])?.select();
    }

    function wire() {
      root.querySelectorAll("[data-mode]").forEach((b) => b.onclick = () => {
        st.mode = b.dataset.mode; store.set("quiz:mode", st.mode); st.revealed = false; st.flipped = false; draw();
      });
      root.querySelectorAll("[data-act]").forEach((b) => b.onclick = () => {
        const item = st.items[st.i];
        switch (b.dataset.act) {
          case "say": say(item, base); break;
          case "fav": toggleFav(item.id); b.classList.toggle("on", isFav(item.id)); b.textContent = isFav(item.id) ? "★" : "☆"; break;
          case "flip": st.flipped = true; draw(); say(item, base); break;
          case "peek": mark("unknown"); st.revealed = true; draw(); say(item, base); break;
          case "unknown":
            mark("unknown");
            if (st.mode === "typing" || !st.flipped) { st.revealed = true; st.flipped = true; draw(); say(item, base); } else next();
            break;
          case "known": mark("known"); next(); break;
          case "next": next(); break;
          case "retry-unknown": reset(st.items.filter((x) => st.results[x.id] !== "known")); break;
          case "restart": reset(st.items.slice()); break;
        }
      });
      const inputs = [...root.querySelectorAll(".q-in")];
      inputs.forEach((inp, k) => {
        inp.addEventListener("keydown", (e) => {
          if (e.key === "Enter") { e.preventDefault(); check(); }
          else if (e.key === " " && k < inputs.length - 1) { e.preventDefault(); inputs[k + 1].focus(); }
          else if (e.key === "Backspace" && !inp.value && k > 0) { e.preventDefault(); inputs[k - 1].focus(); }
        });
        inp.addEventListener("input", () => {
          inp.classList.remove("ok", "bad");
          if (!inp.value.includes(" ")) return;  // mobile keyboards: spread typed spaces across boxes
          const parts = inp.value.split(/\s+/);
          inp.value = parts.shift();
          let j = k + 1;
          for (const p of parts) { if (j >= inputs.length || !p) break; inputs[j].value = p; j++; }
          inputs[Math.min(j, inputs.length - 1)].focus();
        });
      });
    }

    draw();
    return { reset };
  }

  window.Quiz = { mount, isFav, toggleFav };
})();
