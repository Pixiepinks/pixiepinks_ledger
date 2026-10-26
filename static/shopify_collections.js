(() => {
  "use strict";
  const root = document.getElementById("inbox"), dialog = document.getElementById("collection-dialog"),
    query = document.getElementById("collection-query"), select = document.getElementById("collection-select"),
    results = document.getElementById("collection-items"), status = document.getElementById("collection-status"),
    send = document.getElementById("collection-send"), refresh = document.getElementById("collection-preview"),
    stop = document.getElementById("collection-stop"), retry = document.getElementById("collection-retry"),
    check = document.getElementById("collection-check"), reset = document.getElementById("collection-new");
  let target, csrf, preview, batch, generation = 0, controller, working = false, stopRequested = false,
    prepareAttempt = null, retryAttempt = null, recipient;
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
    const blocked = batch && ["PROCESSING", "BLOCKED", "UPDATING"].includes(batch.status);
    query.disabled = working || Boolean(batch) || Boolean(prepareAttempt);
    select.disabled = query.disabled;
    refresh.disabled = query.disabled || !select.value;
    send.textContent = prepareAttempt ? "Check prepared batch" : batch ? "Resume remaining products" : `Send selected products (${count})`;
    send.disabled = working || Boolean(retryAttempt) || Boolean(blocked) || (batch ? !batch.counts.QUEUED : !prepareAttempt && (!preview || count < 1 || count > 20));
    stop.hidden = !working; stop.disabled = stopRequested;
    retry.hidden = !batch || (!batch.counts.FAILED && !retryAttempt); retry.disabled = working || Boolean(blocked);
    retry.textContent = retryAttempt ? "Check failed reset" : "Reset failed products for retry";
    check.hidden = !batch; check.disabled = working;
    reset.disabled = working || Boolean(prepareAttempt) || Boolean(retryAttempt) || Boolean(batch && (blocked || batch.counts.QUEUED));
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
    report(`To ${recipient}: ${c.SENT}/${batch.items.length} sent · ${c.QUEUED} remaining · ${c.FAILED} failed · ${c.SKIPPED} skipped · ${c.UNKNOWN} unconfirmed.${["PROCESSING", "BLOCKED"].includes(batch.status) ? " Work is active or unconfirmed. Check progress; do not create another batch." : ""}`);
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
    if (batch) { batch = await api(path(`/${batch.batch_id}`)); renderBatch(); }
  }
  async function run() {
    if (working || !batch) return;
    working = true; stopRequested = false; controls();
    const cid = target, batchId = batch.batch_id;
    try {
      batch = await api(`/conversations/${cid}/collection-batches/${batchId}`); renderBatch();
      while (!stopRequested && batch.status === "READY") {
        const next = batch.items.find(item => item.status === "QUEUED"); if (!next) break;
        report(`Sending to ${recipient}: ${next.title}… ${batch.counts.SENT}/${batch.items.length} sent. Stop pauses after the current product.`);
        batch = await post(`/conversations/${cid}/collection-batches/${batchId}/send-item`, {product_id: next.product_id});
        renderBatch();
        if (batch.status !== "READY" || stopRequested) break;
        await new Promise(resolve => setTimeout(resolve, 1100));
      }
      renderBatch();
      if (stopRequested && batch.counts.QUEUED) report(`Paused. ${batch.counts.SENT} sent; ${batch.counts.QUEUED} remaining. Resume will not repeat sent products.`);
    } catch (error) { report(`${error.message} Use Check progress before resuming; confirmed products are not re-sent.`); }
    finally { working = false; controls(); }
  }
  document.getElementById("collection-open").onclick = async () => {
    if (working) return;
    const cid = Number(root.dataset.activeConversation); if (!cid) return;
    if ((prepareAttempt || retryAttempt) && target !== cid) { document.getElementById("send-error").textContent = "Check the previous collection action before starting another."; return; }
    target = cid; recipient = document.getElementById("chat-name").textContent;
    document.getElementById("product-modal").hidden = true; dialog.showModal();
    document.getElementById("collection-recipient").textContent = `Customer: ${recipient}`;
    if (prepareAttempt) { controls(); return; }
    const current = ++generation; batch = null; preview = null; results.replaceChildren(); working = true; controls();
    try {
      const data = await api(path("/recent"));
      if (current !== generation || !dialog.open) return;
      csrf = data.csrf_token; batch = data.batch;
      if (batch) renderBatch();
    } catch (error) { report(error.message); }
    finally { working = false; controls(); }
    if (current === generation && dialog.open && !batch) { query.value = ""; await search(); query.focus(); }
  };
  document.getElementById("collection-close").onclick = () => dialog.close();
  dialog.addEventListener("close", () => { stopRequested = true; generation++; controller?.abort(); document.getElementById("catalog-button").focus(); });
  root.addEventListener("inbox-conversation", () => { if (dialog.open) dialog.close(); });
  query.oninput = () => { clearTimeout(query._timer); query._timer = setTimeout(search, 300); };
  select.onchange = () => { generation++; controller?.abort(); preview = null; results.replaceChildren(); controls(); report("Preview this collection before sending."); };
  refresh.onclick = loadPreview;
  stop.onclick = () => { stopRequested = true; controls(); };
  reset.onclick = () => { if (!reset.disabled) { batch = null; preview = null; results.replaceChildren(); query.value = ""; search(); } };
  check.onclick = async () => { if (working) return; working = true; controls(); try { await refreshBatch(); } catch (error) { report(error.message); } finally { working = false; controls(); } };
  send.onclick = async () => {
    if (working || send.disabled) return;
    if (batch) { await run(); return; }
    stopRequested = false;
    const checking = Boolean(prepareAttempt);
    if (!prepareAttempt) prepareAttempt = {key: crypto.randomUUID(), body: {collection_id: preview.collection.id,
      preview_token: preview.preview_token, items: [...results.querySelectorAll(".collection-card")]
        .filter(card => card.querySelector("input").checked).map(card => ({product_id: card.dataset.productId, variant_id: card.querySelector("select").value}))}};
    working = true; controls(); report("Preparing the reviewed selection. No products have been sent yet…");
    try {
      batch = await post(path(), prepareAttempt.body, prepareAttempt.key); prepareAttempt = null; renderBatch();
    } catch (error) {
      if (error.status && error.status < 500) { prepareAttempt = null; preview = null; results.replaceChildren(); }
      report(`${error.message}${prepareAttempt ? " Use Check prepared batch with this same request." : " Refresh the preview before sending."}`);
    } finally { working = false; controls(); }
    if (batch && !checking && !stopRequested && dialog.open) await run();
  };
  retry.onclick = async () => {
    if (working || retry.disabled) return;
    retryAttempt ||= crypto.randomUUID(); working = true; controls();
    try { batch = await post(path(`/${batch.batch_id}/retry-failed`), {}, retryAttempt); retryAttempt = null; renderBatch(); }
    catch (error) { report(`${error.message} Retry this same action; sent products will not be reset.`); }
    finally { working = false; controls(); }
    // Explicit Resume starts sending; resetting failed items alone sends nothing.
  };
})();
