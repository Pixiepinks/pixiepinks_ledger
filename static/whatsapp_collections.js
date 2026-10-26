(() => {
  "use strict";
  const root = document.getElementById("inbox"), dialog = document.getElementById("collection-dialog");
  const query = document.getElementById("collection-query"), select = document.getElementById("collection-select"),
    results = document.getElementById("collection-items"), status = document.getElementById("collection-status"),
    send = document.getElementById("collection-send"), refresh = document.getElementById("collection-preview");
  let target, recipient, csrf, preview, generation = 0, controller, loading = false, sending = false, attempt = null;
  const report = message => { status.textContent = message; };
  async function api(path, options = {}) {
    const response = await fetch(`/crm/whatsapp/api${path}`, {credentials: "same-origin", ...options});
    let data;
    try { data = await response.json(); } catch (_) { throw new Error("Response unavailable. Check the send result before trying again."); }
    if (!response.ok) { const error = new Error(data.detail || "Collection request failed."); error.status = response.status; error.state = data.status; throw error; }
    return data;
  }
  function count() {
    const number = results.querySelectorAll("input:checked").length;
    send.textContent = attempt ? "Check send result" : `Send collection (${number})`;
    send.disabled = sending || loading || (!attempt && (!preview || number < 1 || number > 30));
    return number;
  }
  function clear() { preview = null; results.replaceChildren(); count(); }
  async function search() {
    if (sending || attempt || !dialog.open) return;
    const current = ++generation; controller?.abort(); controller = new AbortController();
    clear(); select.replaceChildren(); loading = true; refresh.disabled = true; report("Loading Shopify collections…");
    try {
      const data = await api(`/collections?q=${encodeURIComponent(query.value.trim())}`, {signal: controller.signal});
      if (current !== generation || !dialog.open) return;
      csrf = data.csrf_token;
      for (const collection of data.collections) {
        const option = document.createElement("option"); option.value = collection.id; option.textContent = collection.title; select.append(option);
      }
      report(data.configured ? (data.collections.length ? "Choose a collection, then preview available products. First 100 matches shown." : "No matching collections.") : data.detail);
      refresh.disabled = !data.collections.length;
    } catch (error) { if (current === generation && error.name !== "AbortError") report(error.message); }
    finally { if (current === generation) { loading = false; count(); } }
  }
  async function loadPreview() {
    if (sending || attempt || !select.value) return;
    const current = ++generation; controller?.abort(); controller = new AbortController();
    clear(); loading = true; refresh.disabled = true; count(); report("Verifying collection, Shopify stock and linked Meta catalog…");
    const cid = target;
    try {
      const data = await api(`/conversations/${cid}/collection-preview?collection_id=${encodeURIComponent(select.value)}`, {signal: controller.signal});
      if (current !== generation || target !== cid || !dialog.open) return;
      preview = data; csrf = data.csrf_token;
      for (const item of data.items) {
        const label = document.createElement("label"), checkbox = document.createElement("input"), text = document.createElement("span");
        label.className = "collection-item"; checkbox.type = "checkbox"; checkbox.value = item.variant_id;
        checkbox.checked = data.items.length <= data.max_items;
        checkbox.addEventListener("change", count);
        text.textContent = `${item.title} · ${item.variant_title} · Shopify price: LKR ${item.price}`;
        label.append(checkbox);
        if (item.image) { const image = new Image(); image.src = item.image; image.alt = ""; image.loading = "lazy"; image.onerror = () => image.remove(); label.append(image); }
        label.append(text); results.append(label);
      }
      const excluded = data.excluded;
      report(`${data.items.length} available mapped variants. Excluded: ${excluded.unavailable} unavailable, ${excluded.unmapped} unmapped, ${excluded.meta_unavailable} unavailable in Meta. ${data.items.length > 30 ? "Select up to 30 variants; WhatsApp cannot include this whole collection in one message." : "Review the selection, then Send. Nothing has been sent."}`);
    } catch (error) { if (current === generation && error.name !== "AbortError") report(error.message); }
    finally { if (current === generation) { loading = false; refresh.disabled = false; count(); } }
  }
  document.getElementById("collection-open").onclick = () => {
    if (sending) return;
    const cid = Number(root.dataset.activeConversation);
    if (!cid) return;
    document.getElementById("product-modal").hidden = true;
    dialog.showModal();
    if (attempt) { report(`An earlier collection send to ${recipient} is unconfirmed. Check that result before starting another collection send.`); count(); return; }
    target = cid; recipient = document.getElementById("chat-name").textContent;
    document.getElementById("collection-recipient").textContent = `To: ${recipient}`;
    query.value = ""; search(); query.focus();
  };
  document.getElementById("collection-close").onclick = () => dialog.close();
  dialog.addEventListener("close", () => { generation++; controller?.abort(); loading = false; document.getElementById("catalog-button").focus(); });
  root.addEventListener("inbox-conversation", () => { if (dialog.open) dialog.close(); });
  query.addEventListener("input", () => { clearTimeout(query._timer); query._timer = setTimeout(search, 300); });
  select.onchange = () => { generation++; controller?.abort(); loading = false; clear(); refresh.disabled = !select.value; report("Preview this collection before sending."); };
  refresh.onclick = loadPreview;
  send.onclick = async () => {
    if (sending || send.disabled) return;
    if (!attempt) attempt = {cid: target, key: crypto.randomUUID(), body: {collection_id: preview.collection.id,
      variant_ids: [...results.querySelectorAll("input:checked")].map(input => input.value), preview_token: preview.preview_token}};
    const current = attempt; sending = true; count(); refresh.disabled = true; query.disabled = true; select.disabled = true;
    for (const checkbox of results.querySelectorAll("input")) checkbox.disabled = true;
    report(`Sending one product collection to ${recipient}…`);
    try {
      const data = await api(`/conversations/${current.cid}/send-collection`, {method: "POST",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf, "Idempotency-Key": current.key}, body: JSON.stringify(current.body)});
      attempt = null; clear(); report(`Collection sent to ${recipient}. WhatsApp controls its browsable layout.`);
    } catch (error) {
      if (error.state === "FAILED" || (error.status && error.status < 500 && !error.state)) {
        attempt = null; clear(); report(`${error.message} Refresh the preview before another attempt.`);
      } else report(`${error.message} Use “Check send result” with the same request; do not start another send.`);
    } finally {
      sending = false; refresh.disabled = Boolean(attempt); query.disabled = Boolean(attempt); select.disabled = Boolean(attempt); count();
    }
  };
})();
