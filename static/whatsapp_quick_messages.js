(() => {
  "use strict";
  const root = document.getElementById("inbox"), input = document.getElementById("message-input"), button = document.getElementById("quick-messages-button"), panel = document.getElementById("quick-message-panel"), search = document.getElementById("quick-message-search"), category = document.getElementById("quick-message-category"), language = document.getElementById("quick-message-language"), results = document.getElementById("quick-message-results"), status = document.getElementById("quick-message-status"), more = document.getElementById("quick-message-more");
  let rows = [], highlighted = 0, generation = 0, timer, loading = false, total = 0;
  const command = () => /^\/[a-z][a-z0-9_-]{0,39}$/i.test(input.value);
  const usable = () => !input.disabled && !document.getElementById("text-composer").hidden && !window.inboxMediaBusy;
  function close() { panel.hidden = true; button.setAttribute("aria-expanded", "false"); generation++; loading = false; }
  function select(row) {
    if (!usable()) return;
    input.value = row.body; input.dataset.quickMessageId = String(row.id);
    close(); input.focus(); input.setSelectionRange(input.value.length, input.value.length);
    input.dispatchEvent(new Event("input", {bubbles: true}));
  }
  function highlight(index) {
    highlighted = Math.max(0, Math.min(index, rows.length - 1));
    [...results.querySelectorAll("button")].forEach((node, i) => { node.classList.toggle("highlighted", i === highlighted); node.setAttribute("aria-selected", String(i === highlighted)); });
  }
  function render() {
    results.replaceChildren(); let group;
    rows.forEach(row => {
      if (row.category !== group) { const heading = document.createElement("h4"); heading.textContent = row.category; results.append(heading); group = row.category; }
      const choice = document.createElement("button"), title = document.createElement("strong"), detail = document.createElement("small"), preview = document.createElement("span");
      choice.type = "button"; choice.setAttribute("role", "option"); title.textContent = row.title; detail.textContent = `/${row.shortcut} · ${row.language === "si" ? "Sinhala" : "English"}`; preview.textContent = row.body;
      choice.append(title, detail, preview); choice.onclick = () => select(row); results.append(choice);
    });
    const exact = command() ? rows.findIndex(row => row.shortcut.toLowerCase() === input.value.slice(1).toLowerCase()) : -1;
    highlight(exact >= 0 ? exact : 0); more.hidden = rows.length >= total;
  }
  async function load(append = false) {
    const ticket = ++generation; loading = true; status.textContent = "Loading replies…"; more.disabled = true;
    try {
      const query = new URLSearchParams({q: search.value.replace(/^\//, ""), category: category.value, language: language.value, offset: String(append ? rows.length : 0)});
      const response = await fetch(`/crm/whatsapp/api/quick-messages?${query}`, {credentials: "same-origin", cache: "no-store"});
      if (!response.ok) throw new Error("Quick Messages are unavailable."); const data = await response.json();
      if (ticket !== generation) return;
      root.dataset.quickMessageCsrf = data.csrf_token; rows = append ? [...rows, ...data.messages] : data.messages; total = data.total;
      const selectedCategory = category.value; category.replaceChildren(new Option("All categories", ""), ...data.categories.map(value => new Option(value, value))); category.value = selectedCategory;
      render(); status.textContent = rows.length ? "Choose a reply. Nothing is sent until you press Send." : "No matching active replies.";
    } catch (error) { if (ticket === generation) { results.replaceChildren(); rows = []; status.textContent = error.message; } }
    finally { if (ticket === generation) { loading = false; more.disabled = false; } }
  }
  function open(focus = true) {
    if (!usable()) return;
    panel.hidden = false; button.setAttribute("aria-expanded", "true"); load(); if (focus) search.focus();
  }
  button.onclick = () => { if (panel.hidden) { search.value = ""; open(); } else close(); };
  document.getElementById("quick-message-close").onclick = () => { close(); input.focus(); };
  search.oninput = () => { clearTimeout(timer); timer = setTimeout(() => load(), 180); };
  category.onchange = language.onchange = () => load(); more.onclick = () => load(true);
  input.addEventListener("input", () => {
    if (!input.value) delete input.dataset.quickMessageId;
    clearTimeout(timer);
    if (!command()) { close(); return; }
    if (!usable()) return;
    delete input.dataset.quickMessageId; search.value = input.value.slice(1); category.value = ""; language.value = "";
    panel.hidden = false; button.setAttribute("aria-expanded", "true"); loading = true; rows = []; results.replaceChildren(); generation++;
    timer = setTimeout(() => load(), 120);
  });
  // Capture before the existing Enter-to-send handler. An autocomplete selection
  // only edits the textarea; a separate subsequent Send/Enter is required.
  input.addEventListener("keydown", event => {
    if (event.isComposing || event.shiftKey || panel.hidden || !command()) return;
    if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); close(); return; }
    if (["ArrowDown", "ArrowUp"].includes(event.key) && rows.length) {
      event.preventDefault(); event.stopImmediatePropagation(); highlight(highlighted + (event.key === "ArrowDown" ? 1 : -1)); return;
    }
    if (["Enter", "Tab"].includes(event.key) && (rows.length || loading)) {
      event.preventDefault(); event.stopImmediatePropagation(); if (rows.length && !loading) select(rows[highlighted]);
    }
  }, true);
  panel.addEventListener("keydown", event => {
    if (event.key === "Escape") { close(); input.focus(); }
    if (event.key === "Enter" && event.target.tagName !== "BUTTON") { event.preventDefault(); event.stopPropagation(); }
  });
  root.addEventListener("inbox-conversation", () => { clearTimeout(timer); close(); if (input.dataset.quickMessageId) { input.value = ""; delete input.dataset.quickMessageId; } });
  // A media selection or recording keeps ownership of its composer controls.
  const observer = new MutationObserver(() => { button.disabled = !usable(); if (!usable()) close(); });
  observer.observe(document.getElementById("text-composer"), {attributes: true, attributeFilter: ["hidden"]});
  observer.observe(input, {attributes: true, attributeFilter: ["disabled"]});
})();
