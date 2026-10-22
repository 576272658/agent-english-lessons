/* Click-to-look-up: tap any English word inside a "wordified" area to see its dictionary entry,
 * hear it, and add it to the word book.
 *   Words.init({ lesson: "EP001", context(el) -> {en, zh} | null, expression(el) -> {id, phrase} | null,
 *                onExpression(id) })
 *   Words.wordify(rootElement)   // wraps English words in <span class="w">; skips .gloss, buttons, links
 */
(function () {
  const { esc, progress } = App;
  const WORD = /[A-Za-z][A-Za-z'’-]*/g;
  const SKIP = ".gloss, button, a, input, .no-word, .wpop";
  let opts = {}, pop = null, current = null;
  const cache = new Map();

  function wordify(root) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => !n.nodeValue.trim() || n.parentElement.closest(SKIP) || n.parentElement.classList.contains("w")
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const text = node.nodeValue;
      WORD.lastIndex = 0;
      if (!WORD.test(text)) continue;
      WORD.lastIndex = 0;
      const frag = document.createDocumentFragment();
      let pos = 0, m;
      while ((m = WORD.exec(text))) {
        if (m.index > pos) frag.appendChild(document.createTextNode(text.slice(pos, m.index)));
        const span = document.createElement("span");
        span.className = "w"; span.textContent = m[0];
        frag.appendChild(span);
        pos = m.index + m[0].length;
      }
      if (pos < text.length) frag.appendChild(document.createTextNode(text.slice(pos)));
      node.parentNode.replaceChild(frag, node);
    }
  }

  async function lookup(word) {
    const key = word.toLowerCase().replace(/’/g, "'");
    if (!cache.has(key)) {
      cache.set(key, fetch(`api/word/${encodeURIComponent(key)}`)
        .then((r) => (r.ok || r.status === 404) ? r.json() : Promise.reject(r.status))
        .catch(async () => {
          // offline: use the word bundle downloaded with the lessons
          cache.delete(key);
          return (await Offline.lookupWord(key)) || { found: false, error: "离线时只能查已下载课程里的单词" };
        }));
    }
    return cache.get(key);
  }

  function ensurePop() {
    if (pop) return pop;
    pop = document.createElement("div");
    pop.className = "wpop"; pop.hidden = true;
    pop.setAttribute("role", "dialog");
    document.body.appendChild(pop);
    document.addEventListener("click", (e) => {
      if (!pop.hidden && !pop.contains(e.target) && !e.target.closest(".w")) close();
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    return pop;
  }

  function close() {
    if (!pop) return;
    pop.hidden = true;
    document.querySelectorAll(".w.sel").forEach((x) => x.classList.remove("sel"));
    current = null;
  }

  function place(el) {
    if (matchMedia("(max-width: 600px)").matches) { pop.classList.add("sheet"); pop.style.left = pop.style.top = ""; return; }
    pop.classList.remove("sheet");
    const r = el.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
    let left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), innerWidth - w - 8);
    let top = r.bottom + 8;
    if (top + h > innerHeight - 8 && r.top - h - 8 > 8) top = r.top - h - 8;
    pop.style.left = `${left + scrollX}px`; pop.style.top = `${top + scrollY}px`;
  }

  function render(el, e) {
    const expr = opts.expression?.(el);
    if (!e.found) {
      pop.innerHTML = `<div class="wpop-head"><span class="wpop-word">${esc(el.textContent)}</span>
        <button class="wpop-x" data-w="close" aria-label="关闭">✕</button></div>
        <p class="muted">${esc(e.error || "词典里没有找到这个词（可能是人名、地名或口语拼写）")}</p>
        ${expr ? `<button class="btn" data-w="expr">本课表达：${esc(expr.phrase)} →</button>` : ""}`;
    } else {
      const inBook = progress.hasWord(e.book_key);
      const note = e.inflection_of
        ? `<div class="wpop-note">${esc(e.surface)} 是 <b>${esc(e.word)}</b> 的${esc(e.forms.find((f) => f.form.toLowerCase() === e.surface)?.kind || "变形")}</div>
           ${e.surface_translation ? `<div class="wpop-own"><b>${esc(e.surface)}</b> ${e.surface_translation.map(esc).join("；")}</div>` : ""}`
        : e.also_form_of ? `<div class="wpop-note">也是 <b>${esc(e.also_form_of)}</b> 的变形</div>` : "";
      pop.innerHTML = `
        <div class="wpop-head">
          <span class="wpop-word">${esc(e.word)}</span><span class="wpop-ipa">${esc(e.phonetic)}</span>
          <button class="q-say" data-w="say" aria-label="播放发音">🔊</button>
          <button class="wpop-x" data-w="close" aria-label="关闭">✕</button>
        </div>
        ${note}
        ${expr ? `<button class="wpop-expr" data-w="expr">本课表达：<b>${esc(expr.phrase)}</b> →</button>` : ""}
        <ul class="wpop-tr">${e.translation.slice(0, 4).map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
        ${e.tags.length ? `<div class="wpop-tags">${e.tags.map((t) => `<span>${esc(t)}</span>`).join("")}</div>` : ""}
        ${e.definition.length ? `<details class="wpop-def"><summary>英文释义</summary>${e.definition.map((d) => `<div>${esc(d)}</div>`).join("")}</details>` : ""}
        <button class="btn ${inBook ? "on" : "primary"} wpop-add" data-w="book">${inBook ? "✓ 已在生词本（点击移除）" : "＋ 加入生词本"}</button>`;
    }
    pop.querySelectorAll("[data-w]").forEach((b) => b.onclick = async (ev) => {
      ev.stopPropagation();
      const act = b.dataset.w;
      if (act === "close") close();
      else if (act === "say") App.play(e.audio, e.surface || e.word);
      else if (act === "expr") { close(); opts.onExpression?.(expr.id); }
      else if (act === "book") {
        b.disabled = true;
        if (progress.hasWord(e.book_key)) await progress.removeWord(e.book_key);
        else await progress.addWord(e.book_key, { lesson: opts.lesson || null, ...(opts.context?.(el) || {}) });
        render(el, e);
      }
    });
  }

  async function open(el) {
    ensurePop();
    document.querySelectorAll(".w.sel").forEach((x) => x.classList.remove("sel"));
    el.classList.add("sel");
    current = el;
    pop.hidden = false;
    pop.innerHTML = `<div class="muted">查询中…</div>`;
    place(el);
    const e = await lookup(el.textContent);
    if (current !== el) return;
    render(el, e);
    place(el);
    if (e.found) App.play(e.audio, e.surface || e.word);
  }

  function init(o) {
    opts = o || {};
    document.addEventListener("click", (ev) => {
      const w = ev.target.closest(".w");
      if (!w) return;
      ev.preventDefault();
      if (current === w && !pop.hidden) { close(); return; }
      open(w);
    });
  }

  window.Words = { init, wordify, close };
})();
