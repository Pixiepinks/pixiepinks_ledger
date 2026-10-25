(() => {
  "use strict";
  const root = document.getElementById("quick-message-management"), form = document.getElementById("quick-editor"), feedback = document.getElementById("quick-admin-feedback"), rows = document.getElementById("quick-admin-rows"), error = document.getElementById("quick-editor-error");
  const search = document.getElementById("quick-admin-search"), category = document.getElementById("quick-admin-category"), language = document.getElementById("quick-admin-language"), status = document.getElementById("quick-admin-status");
  let offset = 0, total = 0, generation = 0, timer;
  const field = name => form.elements.namedItem(name);
  async function api(url, options = {}) {
    const response = await fetch(url, {credentials: "same-origin", cache: "no-store", ...options});
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || "Quick Message request failed."); return data;
  }
  function edit(row = null) {
    form.reset(); form.hidden = false; error.textContent = ""; document.getElementById("quick-id").value = row?.id || "";
    document.getElementById("quick-editor-title").textContent = row ? "Edit message" : "Create message";
    if (row) ["title", "shortcut", "category", "language", "body", "display_order"].forEach(name => { field(name).value = row[name]; });
    field("active").checked = row ? row.active : true; form.scrollIntoView({block: "nearest"}); field("title").focus();
  }
  async function save(row, id) {
    return api(`/crm/quick-messages/api${id ? `/${id}` : ""}`, {method: id ? "PUT" : "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": root.dataset.csrf}, body: JSON.stringify(row)});
  }
  async function load() {
    const ticket = ++generation; feedback.textContent = "Loading messages…";
    try {
      const data = await api(`/crm/quick-messages/api?${new URLSearchParams({q: search.value, category: category.value, language: language.value, status: status.value, offset: String(offset)})}`);
      if (ticket !== generation) return;
      total = data.total; rows.replaceChildren(); root.dataset.csrf = data.csrf_token;
      const selectedCategory = category.value; category.replaceChildren(new Option("All categories", ""), ...data.categories.map(value => new Option(value, value))); category.value = selectedCategory;
      document.getElementById("quick-categories").replaceChildren(...data.categories.map(value => new Option(value, value)));
      data.messages.forEach(row => {
        const tr = document.createElement("tr"), title = document.createElement("td"), heading = document.createElement("strong"), shortcut = document.createElement("small");
        heading.textContent = row.title; shortcut.textContent = `/${row.shortcut}`; title.append(heading, shortcut); tr.append(title);
        [row.category, row.language === "si" ? "Sinhala" : "English", row.active ? "Active" : "Inactive", row.display_order, row.usage_count].forEach(value => { const cell = document.createElement("td"); cell.textContent = String(value); tr.append(cell); });
        const actions = document.createElement("td"), change = document.createElement("button"), toggle = document.createElement("button"); change.type = toggle.type = "button"; change.className = toggle.className = "btn btn-secondary";
        change.textContent = "Edit"; change.onclick = () => edit(row); toggle.textContent = row.active ? "Deactivate" : "Activate";
        toggle.onclick = async () => { toggle.disabled = true; try { const body = Object.fromEntries(["title", "shortcut", "category", "language", "body", "display_order"].map(key => [key, row[key]])); await save({...body, active: !row.active}, row.id); await load(); } catch (failure) { feedback.textContent = failure.message; toggle.disabled = false; } };
        actions.append(change, toggle); tr.append(actions); rows.append(tr);
      });
      feedback.textContent = data.messages.length ? "" : "No matching messages.";
      document.getElementById("quick-admin-total").textContent = `${total} messages · ${data.messages.length ? offset + 1 : 0}–${Math.min(offset + data.messages.length, total)}`;
      document.getElementById("quick-admin-prev").disabled = offset === 0; document.getElementById("quick-admin-next").disabled = offset + 100 >= total;
    } catch (failure) { if (ticket === generation) feedback.textContent = failure.message; }
  }
  document.getElementById("quick-new").onclick = () => edit(); document.getElementById("quick-cancel").onclick = () => { form.hidden = true; };
  form.onsubmit = async event => {
    event.preventDefault(); const button = document.getElementById("quick-save"); button.disabled = true; error.textContent = "";
    const body = Object.fromEntries(["title", "shortcut", "category", "language", "body"].map(name => [name, field(name).value])); body.active = field("active").checked; body.display_order = Number(field("display_order").value);
    try { await save(body, document.getElementById("quick-id").value); form.hidden = true; await load(); } catch (failure) { error.textContent = failure.message; } finally { button.disabled = false; }
  };
  search.oninput = () => { clearTimeout(timer); timer = setTimeout(() => { offset = 0; load(); }, 180); };
  category.onchange = language.onchange = status.onchange = () => { offset = 0; load(); };
  document.getElementById("quick-admin-prev").onclick = () => { offset = Math.max(0, offset - 100); load(); }; document.getElementById("quick-admin-next").onclick = () => { offset += 100; load(); };
  load();
})();
