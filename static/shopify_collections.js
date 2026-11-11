(() => {
  "use strict";
  const root = document.getElementById("inbox"), dialog = document.getElementById("collection-dialog"),
    query = document.getElementById("collection-query"), select = document.getElementById("collection-select"),
    results = document.getElementById("collection-items"), status = document.getElementById("collection-status"),
    send = document.getElementById("collection-send"), refresh = document.getElementById("collection-preview"),
    stop = document.getElementById("collection-stop"), retry = document.getElementById("collection-retry"),
    check = document.getElementById("collection-check"), reset = document.getElementById("collection-new");
  let target, csrf, preview, batch, generation = 0, controller, working = false,
    prepareAttempt = null, retryAttempt = null, recipient;
  const attempts = new Map();
  const report = value => { status.textContent = value; };
  const path = suffix => `/conversations/${target}/collection-batches${suffix || ""}`;
  async function api(url, options = {}) {
    const response = await fetch(`/crm/whatsapp/api${url}`, {credentials: "same-origin", ...options});
    let data;
    try { data = await response.json(); } catch (_) { throw new Error("Response unavailable. Check the existing batch before starting another."); }
    if (!response.ok) { const error = new Error(data.detail || "Collection request failed."); error.status = response.status; throw error; }
    return data;
  }
  const post = (url, body, key) => api(url, {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf,
    ...(key ? {"Idempotency-Key": key} : {})}, body: JSON.stringify(body)});
  function controls() {
    const count = results.querySelectorAll(".collection-card input:checked").length;
    const job = batch?.background?.status;
    const active = ["QUEUED", "RUNNING", "PAUSING"].includes(job);
    const blocked = batch && (["PROCESSING", "BLOCKED", "UPDATING"].includes(batch.status) || ["BLOCKED", "CANCELED"].includes(job));
    query.disabled = working || Boolean(batch) || Boolean(prepareAttempt);
    select.disabled = query.disabled; refresh.disabled = query.disabled || !select.value;
    send.textContent = prepareAttempt ? "Check prepared batch" : batch ? "Resume remaining products" : `Send selected products (${count})`;
    send.disabled = working || active || Boolean(retryAttempt) || Boolean(blocked) || (batch ? !batch.counts.QUEUED : !prepareAttempt && (!preview || count < 1 || count > 20));
    stop.hidden = !active; stop.disabled = working || job === "PAUSING";
    retry.hidden = !batch || (!batch.counts.FAILED && !retryAttempt); retry.disabled = working || active || Boolean(blocked);
    retry.textContent = retryAttempt ? "Check failed reset" : "Reset failed products for retry";
    check.hidden = !batch; check.disabled = working;
    reset.disabled = working || Boolean(prepareAttempt) || Boolean(retryAttempt);
    for (const input of results.querySelectorAll("input,select")) input.disabled = working || Boolean(prepareAttempt);
  }
  function renderBatch() {
    if (!batch) return;
    results.replaceChildren();
    for (const item of batch.items) {
      const row = document.createElement("div"); row.className = "collection-result";
      row.textContent = `${item.title} · ${item.variant_title} — ${item.status}${item.reason ? `: ${item.reason}` : ""}`;
      results.append(row);
    }
    const c = batch.counts;
    report(`To ${recipient}: ${c.SENT}/${batch.items.length} sent · ${c.QUEUED} remaining · ${c.FAILED} failed · ${c.SKIPPED} skipped · ${c.UNKNOWN} unconfirmed.${["PROCESSING", "BLOCKED"].includes(batch.status) ? " A product is active or unconfirmed. Check progress before retrying." : ""}`);
    if (batch.background) report(status.textContent + ` Background: ${batch.background.status}. ${batch.background.reason || "You can close this window; sending continues on the server."}`);
    controls();
  }
  async function search() {
    if (working || batch || prepareAttempt || !dialog.open) return;
    const current = ++generation; controller?.abort(); controller = new AbortController();
    preview = null; results.replaceChildren(); select.replaceChildren(); controls(); report("Loading Shopify collections…");
    try {
      const data = await api(`/collections?q=${encodeURIComponent(query.value.trim())}`, {signal: controller.signal});
      if (current !== generation || !dialog.open) return;
      csrf = data.csrf_token;
      for (const item of data.collections) { const option = document.createElement("option"); option.value = item.id; option.textContent = item.title; select.append(option); }
      report(data.collections.length ? "Choose a collection, then preview available products. First 100 matching collections shown." : "No matching collections.");
    } catch (error) { if (current === generation && error.name !== "AbortError") report(error.message); }
    finally { if (current === generation) controls(); }
  }
  async function loadPreview() {
    if (working || batch || !select.value) return;
    const current = ++generation; controller?.abort(); controller = new AbortController();
    preview = null; results.replaceChildren(); working = true; controls(); report("Checking Shopify collection and availability…");
    try {
      const data = await api(`/conversations/${target}/collection-preview?collection_id=${encodeURIComponent(select.value)}`, {signal: controller.signal});
      if (current !== generation || !dialog.open) return;
      preview = data; csrf = data.csrf_token;
      for (const product of data.products) {
        const card = document.createElement("article"), label = document.createElement("label"), checkbox = document.createElement("input"),
          title = document.createElement("span"), image = new Image(), choices = document.createElement("select");
        card.className = "collection-card"; card.dataset.productId = product.id;
        checkbox.type = "checkbox"; checkbox.checked = data.products.length <= data.max_products;
        checkbox.onchange = controls; title.textContent = product.title; label.append(checkbox, title);
        image.alt = product.title; image.src = product.variants[0].image; image.loading = "lazy"; image.onerror = () => image.hidden = true;
        choices.setAttribute("aria-label", `Available variant for ${product.title}`);
        for (const variant of product.variants) { const option = document.createElement("option"); option.value = variant.variant_id;
          option.textContent = `${variant.variant_title === "Default Title" ? "Standard" : variant.variant_title} · LKR ${variant.price}`; choices.append(option); }
        choices.onchange = () => { image.hidden = false; image.src = product.variants.find(v => v.variant_id === choices.value).image; };
        card.append(image, label, choices); results.append(card);
      }
      report(`${data.products.length} available products. ${data.excluded_products} unavailable/unpublished products excluded. ${data.products.length > data.max_products ? "Choose up to 20; this collection will not be silently truncated." : "Review one available variant per selected product. Nothing has been sent."}`);
    } catch (error) { if (current === generation && error.name !== "AbortError") report(error.message); }
    finally { working = false; controls(); }
  }
  async function refreshBatch() {
    const cid = target, id = batch?.batch_id, current = generation;
    if (!id) return;
    const value = await api(`/conversations/${cid}/collection-batches/${id}`);
    if (current === generation && target === cid && dialog.open) { batch = value; renderBatch(); }
  }
  async function run() {
    if (working || !batch) return;
    const cid = target, id = batch.batch_id, current = generation;
    working = true; controls();
    try {
      const value = await post(`/conversations/${cid}/collection-batches/${id}/start`, {});
      if (current === generation && dialog.open) { batch = value; renderBatch(); }
      await activity();
    } catch (error) { if (current === generation) report(error.message); }
    finally { if (current === generation) { working = false; controls(); } }
  }
  async function openFor(cid, name, batchId) {
    target = cid; recipient = name; prepareAttempt = attempts.get(cid) || null; retryAttempt = null;
    document.getElementById("product-modal").hidden = true;
    if (!dialog.open) dialog.showModal();
    document.getElementById("collection-recipient").textContent = `Customer: ${recipient}`;
    const current = ++generation; batch = null; preview = null; results.replaceChildren(); working = true; controls();
    try {
      const data = await api(`/conversations/${cid}/collection-batches/recent`);
      if (current !== generation || !dialog.open) return;
      csrf = data.csrf_token; batch = batchId ? await api(`/conversations/${cid}/collection-batches/${batchId}`) : data.batch;
      if (current !== generation || !dialog.open) return;
      if (batch?.background && prepareAttempt) { attempts.delete(cid); prepareAttempt = null; }
      if (batch && !prepareAttempt) renderBatch();
    } catch (error) { if (current === generation) report(error.message); }
    finally { if (current === generation) { working = false; controls(); } }
    if (current === generation && dialog.open && !batch && !prepareAttempt) { query.value = ""; await search(); query.focus(); }
  }
  document.getElementById("collection-open").onclick = () => {
    const cid = Number(root.dataset.activeConversation); if (!cid) return;
    openFor(cid, document.getElementById("chat-name").textContent);
  };
  document.getElementById("collection-close").onclick = () => dialog.close();
  dialog.addEventListener("close", () => { generation++; controller?.abort(); working = false; document.getElementById("catalog-button").focus(); });
  document.getElementById("collection-minimize").onclick = () => dialog.close();
  root.addEventListener("inbox-conversation", () => { if (dialog.open) dialog.close(); });
  query.oninput = () => { clearTimeout(query._timer); query._timer = setTimeout(search, 300); };
  select.onchange = () => { generation++; controller?.abort(); preview = null; results.replaceChildren(); controls(); report("Preview this collection before sending."); };
  refresh.onclick = loadPreview;
  stop.onclick = async () => {
    if (working || !batch) return;
    const current = generation, cid = target, id = batch.batch_id;
    working = true; controls();
    try { const value = await post(`/conversations/${cid}/collection-batches/${id}/pause`, {});
      if (current === generation && dialog.open) { batch = value; renderBatch(); } await activity();
    } catch (error) { if (current === generation) report(error.message); }
    finally { if (current === generation) { working = false; controls(); } }
  };
  reset.onclick = () => { if (!reset.disabled) { batch = null; preview = null; results.replaceChildren(); query.value = ""; search(); } };
  check.onclick = async () => { if (working) return; working = true; controls(); try { await refreshBatch(); } catch (error) { report(error.message); } finally { working = false; controls(); } };
  send.onclick = async () => {
    if (working || send.disabled) return;
    if (batch && !prepareAttempt) { await run(); return; }
    const cid = target, current = generation;
    if (!prepareAttempt) prepareAttempt = {key: crypto.randomUUID(), body: {background: true, collection_id: preview.collection.id,
      preview_token: preview.preview_token, items: [...results.querySelectorAll(".collection-card")]
        .filter(card => card.querySelector("input").checked).map(card => ({product_id: card.dataset.productId, variant_id: card.querySelector("select").value}))}};
    const attempt = prepareAttempt; attempts.set(cid, attempt);
    working = true; controls(); report("Queuing your reviewed selection for background sending…");
    try {
      const created = await post(`/conversations/${cid}/collection-batches`, attempt.body, attempt.key);
      // Selection and send intent commit atomically on the server, even if this page closes.
      const value = created;
      attempts.delete(cid);
      if (current === generation && dialog.open) { batch = value; prepareAttempt = null; renderBatch(); }
      await activity();
    } catch (error) {
      if (error.status && error.status < 500) attempts.delete(cid);
      if (current === generation) {
        prepareAttempt = attempts.get(cid) || null;
        report(`${error.message} Check the saved batch before retrying.`);
      }
    } finally { if (current === generation) { working = false; controls(); } }
  };
  retry.onclick = async () => {
    if (working || retry.disabled) return;
    const current = generation, cid = target, id = batch.batch_id;
    retryAttempt ||= crypto.randomUUID(); working = true; controls();
    try { const value = await post(`/conversations/${cid}/collection-batches/${id}/retry-failed`, {}, retryAttempt); if (current === generation) { batch = value; retryAttempt = null; renderBatch(); } }
    catch (error) { report(`${error.message} Retry this same action; sent products will not be reset.`); }
    finally { if (current === generation) { working = false; controls(); } }
    // Explicit Resume starts sending; resetting failed items alone sends nothing.
  };
  async function activity() {
    try {
      const data = await api('/collection-batches/active');
      const box = document.getElementById('collection-activity'), list = document.getElementById('collection-active-list');
      csrf = data.csrf_token; box.hidden = !data.jobs.length; list.replaceChildren();
      document.getElementById('collection-activity-label').textContent = `Product sends (${data.jobs.length})`;
      for (const item of data.jobs) {
        const button = document.createElement('button'); button.type = 'button';
        button.textContent = `${item.customer} — ${item.counts.SENT}/${item.items.length} sent · ${item.background.status}`;
        button.onclick = () => openFor(item.conversation_id, item.customer, item.batch_id); list.append(button);
      }
    } catch (_) { /* Existing queued sends continue even while this browser is offline. */ }
  }
  setInterval(() => { if (dialog.open && batch && !working && !prepareAttempt) refreshBatch().catch(error => report(error.message)); }, 2000);
  setInterval(activity, 4000); activity();
})();
