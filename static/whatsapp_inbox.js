(() => {
  "use strict";
  const root = document.getElementById("inbox"), list = document.getElementById("conversation-list"), messages = document.getElementById("messages"), errorBox = document.getElementById("send-error"), statusBox = document.getElementById("send-status");
  let selected = Number(root.dataset.initial) || null, filter = new URLSearchParams(location.search).get("filter") || "all";
  let recorder, stream, timer, blob, blobUrl, started, duration, voiceKey, chunks = [], discard = false;
  let conversationController, conversationLoading = false, conversationGeneration = 0, listLoading = false, detailsSignature = "", currentConversationData = null;
  const text = (el, value) => { el.textContent = value == null ? "—" : String(value); };
  const clock = seconds => `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
  const badge = (value, kind = "neutral") => { const el = document.createElement("span"); el.className = `badge ${kind}`; text(el, value); return el; };
  const money = value => new Intl.NumberFormat("en-LK", {style: "currency", currency: "LKR", maximumFractionDigits: 2}).format(value || 0);
  async function api(url, options) {
    const response = await fetch(url, {credentials: "same-origin", ...options}); let data = {};
    try { data = await response.json(); } catch (_) { /* generic error below */ }
    if (!response.ok) { const error = new Error(data.detail || data.error || "Request failed"); error.status = response.status; throw error; }
    return data;
  }
  function conversationRow(c) {
    const button = document.createElement("button"); button.className = "conversation-row" + (c.id === selected ? " selected" : "");
    const line = document.createElement("div"); line.className = "conversation-line";
    const name = document.createElement("strong"); text(name, c.customer_name);
    const time = document.createElement("time"); text(time, c.last_message_at ? new Date(c.last_message_at).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}) : ""); line.append(name, time);
    const preview = document.createElement("p"); text(preview, c.latest_message || c.phone_number);
    const meta = document.createElement("div"); meta.className = "conversation-meta"; meta.append(badge(c.mode, c.mode.toLowerCase()));
    if (c.payment_required) meta.append(badge("PAYMENT REQUIRED", "payment"));
    if (c.unread_count) { const unread = document.createElement("span"); unread.className = "unread"; text(unread, c.unread_count); meta.append(unread); }
    button.append(line, preview, meta); button.onclick = () => openConversation(c.id, true); return button;
  }
  async function loadList() {
    if (listLoading || document.hidden) return;
    listLoading = true;
    try {
      const search = document.getElementById("search").value;
      const data = await api(`/crm/whatsapp/api/conversations?filter=${encodeURIComponent(filter)}&search=${encodeURIComponent(search)}`);
      list.replaceChildren(...data.conversations.map(conversationRow)); text(document.getElementById("list-status"), `${data.conversations.length} shown`);
      if (!data.conversations.length) { const empty = document.createElement("div"); empty.className = "empty"; text(empty, search ? "No search results." : "No WhatsApp conversations."); list.replaceChildren(empty); }
    } catch (_) { list.innerHTML = '<div class="empty">Could not load conversations.</div>'; }
    finally { listLoading = false; }
  }
  function detailRow(label, value) { const row = document.createElement("div"), name = document.createElement("span"), content = document.createElement("span"); row.className = "detail-row"; text(name, label); text(content, value); row.append(name, content); return row; }
  function detailLink(label, url, primary = false) { const link = document.createElement("a"); link.className = `btn ${primary ? "btn-primary" : "btn-secondary"}`; link.href = url; text(link, label); return link; }
  function renderDetails(data) {
    const profile = document.getElementById("profile"); profile.replaceChildren(); const heading = document.createElement("h3"); text(heading, "Customer profile");
    profile.append(heading, detailRow("Name", data.conversation.customer_name), detailRow("WhatsApp", data.conversation.phone_number), detailRow("Mode", data.conversation.mode));
    const order = document.getElementById("order"); order.replaceChildren();
    if (data.sales_order) {
      const value = data.sales_order, heading = document.createElement("h3"), actions = document.createElement("div"); heading.className = "detail-title"; text(heading, "Latest sales order"); actions.className = "detail-actions";
      order.append(heading, detailRow("Order", value.internal_order_number), detailRow("Order status", value.order_status.replaceAll("_", " ")), detailRow("Payment", value.payment_status.replaceAll("_", " ")), detailRow("Products", value.product_count), detailRow("Grand total", money(value.grand_total)));
      actions.append(detailLink("View Order", value.view_url)); if (value.edit_url) actions.append(detailLink("Edit Draft", value.edit_url, true)); order.append(actions);
      if (value.conversation_order_count > 1) { const note = document.createElement("small"); note.className = "detail-note"; text(note, `${value.conversation_order_count} sales orders are linked to this conversation. Showing the latest.`); order.append(note); }
    }
    if (!data.order) { if (!data.sales_order) { const empty = document.createElement("div"); empty.className = "empty"; text(empty, "No active order."); order.append(empty); } return; }
    const value = data.order, orderHeading = document.createElement("h3"); text(orderHeading, "WhatsApp sales intent"); order.append(orderHeading);
    if (value.product_image_url?.startsWith("https://")) { const image = new Image(); image.className = "product-thumb"; image.alt = value.product_title; image.src = value.product_image_url; order.append(image); }
    order.append(detailRow("Product", value.product_title), detailRow("Variant", value.variant_title || "—"));
    if (value.bicycle_size) order.append(detailRow("Bicycle", `${value.bicycle_gender || ""} · age ${value.bicycle_age || "—"} · ${value.bicycle_size}\"`));
    order.append(detailRow("Product", money(value.product_price)), detailRow("Initial service", money(value.service_charge)), detailRow("Delivery", value.delivery_charge ? money(value.delivery_charge) : "FREE"), detailRow("TOTAL", money(value.total)), detailRow("Status", value.payment_required ? "PAYMENT DETAILS REQUIRED" : value.status.replaceAll("_", " ")));
    if (value.contact_person_name) order.append(detailRow("Contact", value.contact_person_name), detailRow("Primary phone", value.primary_phone), detailRow("Alternative phone", value.alternative_phone), detailRow("Delivery address", value.delivery_address));
    if (value.product_url?.startsWith("https://")) { const link = document.createElement("a"); link.href = value.product_url; link.target = "_blank"; link.rel = "noopener noreferrer"; text(link, "Open in Shopify ↗"); order.append(link); }
  }
  function renderMessage(message) {
    const wrapper = document.createElement("div"), bubble = document.createElement("div"), metadata = document.createElement("small"); wrapper.className = `message ${message.direction}`; bubble.className = "bubble";
    if (message.product) {
      bubble.classList.add("product-bubble");
      if (message.product.image?.startsWith("https://")) { const image = new Image(); image.src = message.product.image; image.alt = message.product.title; bubble.append(image); }
      const copy = document.createElement("div"), title = document.createElement("strong"), variant = document.createElement("span"), price = document.createElement("span"), link = document.createElement("a"); copy.className = "product-copy"; text(title, message.product.title); text(variant, message.product.variant && message.product.variant !== "Default Title" ? message.product.variant : ""); text(price, money(message.product.price));
      if (message.product.url?.startsWith("https://")) { link.href = message.product.url; link.target = "_blank"; link.rel = "noopener noreferrer"; text(link, "View product ↗"); }
      copy.append(title); if (variant.textContent) copy.append(variant); copy.append(price); if (link.href) copy.append(link); bubble.append(copy);
    } else if (message.message_type === "audio" && message.media_url) { bubble.classList.add("audio-bubble"); const label = document.createElement("span"), audio = document.createElement("audio"); text(label, `Voice message${message.duration_seconds ? ` · ${clock(message.duration_seconds)}` : ""}`); audio.controls = true; audio.preload = "metadata"; audio.src = message.media_url; bubble.append(label, audio); } else text(bubble, message.text);
    wrapper.dataset.messageId = String(message.id); wrapper.dataset.contentKey = messageContentKey(message); metadata.className = "message-meta"; wrapper.dataset.status = message.status;
    text(metadata, messageMetadata(message)); wrapper.append(bubble, metadata); return wrapper;
  }
  const messageContentKey = message => JSON.stringify([message.direction, message.text, message.source, message.timestamp, message.message_type, message.duration_seconds, message.media_url, message.product]);
  const messageMetadata = message => `${message.direction === "inbound" ? "Customer" : message.source.startsWith("staff") ? "Staff" : "AI"} · ${new Date(message.timestamp).toLocaleString()} · ${message.status}`;
  function reconcileMessages(incoming) {
    const wasNearBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
    const containerTop = messages.getBoundingClientRect().top;
    const anchor = [...messages.children].find(node => node.getBoundingClientRect().bottom >= containerTop);
    const anchorId = anchor?.dataset.messageId, anchorOffset = anchor ? anchor.getBoundingClientRect().top - containerTop : 0;
    const existing = new Map([...messages.querySelectorAll("[data-message-id]")].map(node => [node.dataset.messageId, node]));
    const wanted = new Set(incoming.map(message => String(message.id)));
    [...existing].forEach(([id, node]) => { if (!wanted.has(id)) { node.remove(); existing.delete(id); } });
    incoming.forEach((message, index) => {
      const id = String(message.id), contentKey = messageContentKey(message); let node = existing.get(id);
      if (!node || node.dataset.contentKey !== contentKey) { const replacement = renderMessage(message); if (node) node.replaceWith(replacement); node = replacement; existing.set(id, node); }
      else if (node.dataset.status !== message.status) { node.dataset.status = message.status; text(node.querySelector(".message-meta"), messageMetadata(message)); }
      const current = messages.children[index]; if (current !== node) messages.insertBefore(node, current || null);
    });
    if (wasNearBottom) messages.scrollTop = messages.scrollHeight;
    else if (anchorId) { const retained = messages.querySelector(`[data-message-id="${CSS.escape(anchorId)}"]`); if (retained) messages.scrollTop += retained.getBoundingClientRect().top - containerTop - anchorOffset; }
  }
  async function openConversation(id, markRead = false) {
    const switching = selected !== id; selected = id; root.classList.add("chat-open"); document.getElementById("chat-empty").hidden = true; document.getElementById("chat").hidden = false;
    if (switching) { conversationGeneration += 1; conversationController?.abort(); conversationLoading = false; detailsSignature = ""; messages.replaceChildren(); }
    if (conversationLoading) return;
    const generation = conversationGeneration, controller = new AbortController(); conversationController = controller; conversationLoading = true;
    try {
      if (markRead) await api(`/crm/whatsapp/api/conversations/${id}/read`, {method: "POST", signal: controller.signal}); const data = await api(`/crm/whatsapp/api/conversations/${id}`, {signal: controller.signal});
      if (generation !== conversationGeneration || selected !== id) return;
      currentConversationData = data;
      text(document.getElementById("chat-name"), data.conversation.customer_name); text(document.getElementById("chat-phone"), data.conversation.phone_number); document.getElementById("mode-badge").replaceChildren(badge(data.conversation.mode, data.conversation.mode.toLowerCase()));
      const action = document.getElementById("mode-action"); text(action, data.conversation.mode === "AI" ? "Take Over" : "Return to AI"); action.dataset.mode = data.conversation.mode; document.getElementById("payment-banner").hidden = !data.conversation.payment_required;
      reconcileMessages(data.messages); const nextDetailsSignature = JSON.stringify([data.conversation, data.order, data.sales_order]); if (nextDetailsSignature !== detailsSignature) { renderDetails(data); detailsSignature = nextDetailsSignature; }
      if (markRead || switching) loadList();
    } catch (error) { if (error.name !== "AbortError" && !messages.children.length) messages.innerHTML = '<div class="empty">Conversation not found or could not be loaded.</div>'; }
    finally { if (conversationController === controller) { conversationLoading = false; conversationController = null; } }
  }
  const stopTracks = () => { stream?.getTracks().forEach(track => track.stop()); stream = null; };
  const recordingFilename = type => type.includes("ogg") ? "voice.ogg" : type.includes("mp4") ? "voice.m4a" : "voice.webm";
  function showComposerMessage(message = "", isError = false) {
    text(isError ? errorBox : statusBox, message);
    text(isError ? statusBox : errorBox, "");
  }
  function resetRecording() {
    clearInterval(timer); stopTracks(); recorder = null; chunks = []; duration = 0; voiceKey = null; if (blobUrl) URL.revokeObjectURL(blobUrl); blobUrl = null; blob = null;
    const preview = document.getElementById("voice-preview-audio"); preview.pause(); preview.removeAttribute("src"); preview.load();
    document.getElementById("recording-controls").hidden = true; document.getElementById("voice-preview").hidden = true; document.getElementById("text-composer").hidden = false; document.getElementById("voice-composer").hidden = false; document.getElementById("record-button").disabled = false; text(document.getElementById("recording-timer"), "00:00");
  }
  function prepareRecordingPreview() {
    duration = Math.max(0, (performance.now() - started) / 1000);
    blob = new Blob(chunks, {type: recorder.mimeType || chunks[0]?.type || "audio/webm"});
    if (!blob.size || duration < 0.25) { resetRecording(); showComposerMessage("No usable audio was recorded. Check your microphone and try again.", true); return; }
    blobUrl = URL.createObjectURL(blob); voiceKey = crypto.randomUUID();
    const preview = document.getElementById("voice-preview-audio");
    preview.onloadedmetadata = () => {
      if (Number.isFinite(preview.duration) && preview.duration > 0) text(document.getElementById("voice-duration"), clock(preview.duration));
      else if (preview.duration === Infinity) { preview.currentTime = 1e101; preview.ontimeupdate = () => { preview.ontimeupdate = null; preview.currentTime = 0; }; }
    };
    preview.src = blobUrl; preview.load(); text(document.getElementById("voice-duration"), clock(duration)); document.getElementById("recording-controls").hidden = true; document.getElementById("voice-preview").hidden = false; document.getElementById("voice-composer").hidden = true;
  }
  async function startRecording() {
    showComposerMessage(); if (!selected) { showComposerMessage("Select a conversation first.", true); return; }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { showComposerMessage("Voice recording is not supported by this browser.", true); return; }
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}, video: false});
      const mime = ["audio/ogg;codecs=opus", "audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find(type => !MediaRecorder.isTypeSupported || MediaRecorder.isTypeSupported(type)) || "";
      recorder = new MediaRecorder(stream, mime ? {mimeType: mime} : undefined); chunks = []; discard = false;
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = () => {
        stopTracks(); clearInterval(timer); if (discard) { resetRecording(); return; } prepareRecordingPreview();
      };
      recorder.onerror = () => { discard = true; stopTracks(); resetRecording(); showComposerMessage("Recording failed. Check your microphone and try again.", true); };
      recorder.onstart = () => {
        started = performance.now(); document.getElementById("text-composer").hidden = true; document.getElementById("voice-composer").hidden = true; document.getElementById("record-button").disabled = true; document.getElementById("recording-controls").hidden = false;
        const update = () => { const elapsed = (performance.now() - started) / 1000; text(document.getElementById("recording-timer"), clock(elapsed)); if (elapsed >= 600 && recorder?.state === "recording") recorder.stop(); }; update(); timer = setInterval(update, 1000);
      };
      recorder.start(1000);
    } catch (error) { stopTracks(); resetRecording(); const denied = error?.name === "NotAllowedError" || error?.name === "SecurityError"; const missing = error?.name === "NotFoundError" || error?.name === "DevicesNotFoundError"; showComposerMessage(denied ? "Microphone permission was denied. Allow microphone access and try again." : missing ? "No microphone was found. Connect a microphone and try again." : "The microphone could not be started.", true); }
  }
  async function sendVoice() {
    if (!selected || !blob?.size) return; const button = document.getElementById("send-voice"); button.disabled = true; text(button, "Uploading…"); showComposerMessage("Uploading voice message…"); const form = new FormData(); form.append("audio", blob, recordingFilename(blob.type));
    try { await api(`/crm/whatsapp/api/conversations/${selected}/send-audio`, {method: "POST", headers: {"Idempotency-Key": voiceKey}, body: form}); resetRecording(); showComposerMessage("Voice message sent."); await openConversation(selected); }
    catch (error) { showComposerMessage(`${error.message} Your recording is still available to retry.`, true); if (error.status === 502 || error.status === 422) voiceKey = crypto.randomUUID(); }
    finally { button.disabled = false; text(button, "Send Voice"); }
  }
  const catalogModal = document.getElementById("product-modal"), catalogResults = document.getElementById("catalog-results"), catalogStatus = document.getElementById("catalog-status"), catalogQuery = document.getElementById("catalog-query"), loadProductsButton = document.getElementById("load-products");
  let catalogCursor = null, catalogLoading = false, catalogSearchGeneration = 0;
  function closeCatalog() { catalogModal.hidden = true; }
  function catalogCard(product) {
    const card = document.createElement("article"), image = new Image(), body = document.createElement("div"), heading = document.createElement("h3"), details = document.createElement("p"), select = document.createElement("select"), button = document.createElement("button"); card.className = "catalog-card"; image.alt = product.title; if (product.image?.startsWith("https://")) image.src = product.image; text(heading, product.title); text(details, [product.vendor, product.product_type].filter(Boolean).join(" · "));
    product.variants.forEach((variant, index) => { const option = document.createElement("option"); option.value = String(index); text(option, `${variant.title === "Default Title" ? "Standard" : variant.title} · ${money(Number(variant.price))}${variant.sku ? ` · ${variant.sku}` : ""}${variant.available ? "" : " · Unavailable"}`); option.disabled = !variant.available; select.append(option); });
    const productAvailable = product.status === "ACTIVE" && product.published; const firstAvailable = productAvailable ? product.variants.findIndex(item => item.available) : -1; if (firstAvailable >= 0) select.value = String(firstAvailable); select.disabled = firstAvailable < 0; button.type = "button"; button.className = "btn btn-primary"; text(button, firstAvailable < 0 ? "Unavailable" : "Send Product"); button.disabled = firstAvailable < 0;
    select.onchange = () => { const variant = product.variants[Number(select.value)]; if (variant?.image?.startsWith("https://")) image.src = variant.image; };
    button.onclick = async () => { if (!selected) { text(catalogStatus, "Select a conversation first."); return; } const variant = product.variants[Number(select.value)]; if (!variant?.available) return; button.disabled = true; text(button, "Sending…"); text(catalogStatus, "Sending product to customer…"); try { await api(`/crm/whatsapp/api/conversations/${selected}/send-product`, {method: "POST", headers: {"Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID()}, body: JSON.stringify({product_id: product.id, variant_id: variant.id})}); closeCatalog(); showComposerMessage("Product sent."); await openConversation(selected); } catch (error) { text(catalogStatus, error.message); } finally { button.disabled = false; text(button, "Send Product"); } };
    body.append(heading, details, select, button); card.append(image, body); return card;
  }
  async function loadCatalog(append = false) {
    if (catalogLoading) return; catalogLoading = true; const generation = ++catalogSearchGeneration; text(catalogStatus, "Loading Shopify products…"); if (!append) { catalogCursor = null; catalogResults.replaceChildren(); }
    try { const data = await api(`/crm/whatsapp/api/products?q=${encodeURIComponent(catalogQuery.value.trim())}&limit=20${catalogCursor ? `&cursor=${encodeURIComponent(catalogCursor)}` : ""}`); if (generation !== catalogSearchGeneration) return; catalogResults.append(...data.products.map(catalogCard)); catalogCursor = data.next_cursor || null; loadProductsButton.hidden = !data.has_next_page; text(catalogStatus, data.products.length ? "Select an available variant to send." : "No matching products found."); } catch (error) { text(catalogStatus, error.message); } finally { catalogLoading = false; }
  }
  document.getElementById("catalog-button").onclick = () => { if (!selected) { showComposerMessage("Select a conversation first.", true); return; } catalogModal.hidden = false; catalogQuery.focus(); loadCatalog(); };
  document.getElementById("close-catalog").onclick = closeCatalog; document.getElementById("catalog-close-footer").onclick = closeCatalog; loadProductsButton.onclick = () => loadCatalog(true); catalogModal.onclick = event => { if (event.target === catalogModal) closeCatalog(); };
  catalogQuery.addEventListener("input", () => { clearTimeout(catalogQuery._timer); catalogQuery._timer = setTimeout(() => loadCatalog(), 300); });
  document.addEventListener("keydown", event => { if (event.key === "Escape" && !catalogModal.hidden) closeCatalog(); });

  document.getElementById("search").addEventListener("input", () => { clearTimeout(window._searchTimer); window._searchTimer = setTimeout(loadList, 250); });
  document.querySelectorAll("#filters button").forEach(button => { if (button.dataset.filter === filter) { document.querySelectorAll("#filters button").forEach(item => item.classList.remove("selected")); button.classList.add("selected"); } button.onclick = () => { filter = button.dataset.filter; document.querySelectorAll("#filters button").forEach(item => item.classList.toggle("selected", item === button)); loadList(); }; });
  document.getElementById("mode-action").onclick = async event => { if (!selected) return; event.currentTarget.disabled = true; try { await api(`/crm/whatsapp/api/conversations/${selected}/${event.currentTarget.dataset.mode === "AI" ? "takeover" : "return-to-ai"}`, {method: "POST"}); await openConversation(selected); } finally { event.currentTarget.disabled = false; } };
  document.getElementById("composer").onsubmit = async event => { event.preventDefault(); const input = document.getElementById("message-input"), button = document.getElementById("send-button"), body = input.value.trim(); if (!selected) { showComposerMessage("Select a conversation first.", true); return; } if (!body) { showComposerMessage("Type a message before sending.", true); input.focus(); return; } button.disabled = true; text(button, "Sending…"); showComposerMessage("Sending message…"); try { await api(`/crm/whatsapp/api/conversations/${selected}/send`, {method: "POST", headers: {"Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID()}, body: JSON.stringify({message: body})}); input.value = ""; showComposerMessage("Message sent."); await openConversation(selected); } catch (error) { showComposerMessage(error.message, true); } finally { button.disabled = false; text(button, "Send"); } };
  document.getElementById("message-input").addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); document.getElementById("composer").requestSubmit(); } });
  document.getElementById("record-button").onclick = startRecording; document.getElementById("stop-recording").onclick = () => { if (recorder?.state === "recording") recorder.stop(); }; document.getElementById("cancel-recording").onclick = () => { discard = true; if (recorder?.state === "recording") recorder.stop(); else resetRecording(); }; document.getElementById("delete-recording").onclick = resetRecording; document.getElementById("send-voice").onclick = sendVoice;
  document.getElementById("details-action").onclick = () => document.getElementById("details").classList.add("open"); document.getElementById("close-details").onclick = () => document.getElementById("details").classList.remove("open"); document.getElementById("back-list").onclick = () => root.classList.remove("chat-open");

  // Phase 2 commercial-order drawer. It reuses the authenticated Shopify picker API;
  // Shopify is re-queried server-side when saving, so these browser snapshots are never authoritative.
  const orderDrawer = document.getElementById("order-drawer");
  if (orderDrawer) {
    const orderScrim = document.getElementById("order-scrim"), orderLinesNode = document.getElementById("order-lines"), orderError = document.getElementById("order-error");
    let orderLines = [], orderSearchTimer, orderSearchGeneration = 0, orderDraftKey = crypto.randomUUID();
    const orderInput = id => document.getElementById(id);
    const numeric = value => Math.max(0, Number(value) || 0);
    function closeOrderDrawer() { orderDrawer.hidden = true; orderScrim.hidden = true; document.body.classList.remove("order-drawer-open"); }
    function calculateOrderPreview() {
      const subtotal = orderLines.reduce((sum, line) => sum + numeric(line.quantity) * numeric(line.price), 0);
      const lineDiscounts = orderLines.reduce((sum, line) => sum + numeric(line.discount), 0);
      const lineTaxes = orderLines.reduce((sum, line) => sum + numeric(line.tax), 0);
      const discount = lineDiscounts + numeric(orderInput("order-discount").value), shipping = numeric(orderInput("order-shipping").value), tax = lineTaxes + numeric(orderInput("order-tax").value);
      const total = Math.max(0, subtotal - discount + shipping + tax), totals = document.getElementById("order-preview-totals"); totals.replaceChildren();
      [["Subtotal", subtotal], ["Discounts", -discount], ["Shipping", shipping], ["Tax", tax], ["Grand total", total]].forEach(([label, value], index) => { const row = document.createElement("span"); if (index === 4) row.className = "grand"; const name = document.createElement("span"), amount = document.createElement("b"); text(name, label); text(amount, money(value)); row.append(name, amount); totals.append(row); });
    }
    function renderOrderLines() {
      orderLinesNode.replaceChildren();
      if (!orderLines.length) { const empty = document.createElement("div"); empty.className = "empty"; text(empty, "Add at least one Shopify product."); orderLinesNode.append(empty); calculateOrderPreview(); return; }
      orderLines.forEach((line, index) => {
        const row = document.createElement("article"); row.className = "order-edit-line";
        const image = new Image(); image.alt = ""; if (line.image?.startsWith("https://")) image.src = line.image;
        const info = document.createElement("div"), title = document.createElement("strong"), variant = document.createElement("span"); text(title, line.title); text(variant, `${line.variantTitle === "Default Title" ? "Standard" : line.variantTitle}${line.sku ? ` · ${line.sku}` : ""} · ${money(line.price)}`); info.append(title, variant);
        const quantity = document.createElement("input"); quantity.type = "number"; quantity.min = "0.001"; quantity.step = "0.001"; quantity.value = line.quantity; quantity.setAttribute("aria-label", `Quantity for ${line.title}`); quantity.oninput = () => { line.quantity = quantity.value; calculateOrderPreview(); };
        const remove = document.createElement("button"); remove.type = "button"; remove.className = "btn btn-secondary"; text(remove, "Remove"); remove.onclick = () => { orderLines.splice(index, 1); renderOrderLines(); };
        row.append(image, info, quantity, remove); orderLinesNode.append(row);
      }); calculateOrderPreview();
    }
    function openOrderDrawer(order = null) {
      if (!selected && !order) { showComposerMessage("Select a conversation first.", true); return; }
      const conversation = currentConversationData?.conversation || {};
      orderInput("order-id").value = order?.id || ""; text(document.getElementById("order-drawer-title"), order ? `Edit ${order.internal_order_number}` : "Create Order");
      orderInput("order-customer-name").value = order?.customer_name || conversation.customer_name || "";
      orderInput("order-phone").value = order?.whatsapp_phone || conversation.phone_number || "";
      orderInput("order-alt-phone").value = order?.alternative_phone || currentConversationData?.order?.alternative_phone || "";
      orderInput("order-contact").value = order?.delivery_contact_person || currentConversationData?.order?.contact_person_name || conversation.customer_name || "";
      orderInput("order-address").value = order?.delivery_address || currentConversationData?.order?.delivery_address || "";
      orderInput("order-city").value = order?.city || ""; orderInput("order-district").value = order?.district || ""; orderInput("order-postal").value = order?.postal_code || ""; orderInput("order-notes").value = order?.customer_notes || "";
      orderInput("order-discount").value = order?.discount_total || "0"; orderInput("order-shipping").value = order?.shipping_total || "0"; orderInput("order-tax").value = order?.tax_total || "0";
      orderLines = (order?.lines || []).map(line => ({productId: line.shopify_product_id, variantId: line.shopify_variant_id, title: line.product_name, variantTitle: line.variant_name, sku: line.sku, image: line.product_image_url, url: line.product_url, price: line.unit_price, quantity: line.quantity, discount: line.discount_amount, tax: line.tax_amount}));
      orderDraftKey = crypto.randomUUID(); renderOrderLines(); text(orderError, ""); orderDrawer.hidden = false; orderScrim.hidden = false; document.body.classList.add("order-drawer-open");
    }
    function orderProductCard(product) {
      const card = document.createElement("article"), image = new Image(), content = document.createElement("div"), title = document.createElement("strong"), select = document.createElement("select"), add = document.createElement("button"); card.className = "order-picker-card"; image.alt = product.title; if (product.image?.startsWith("https://")) image.src = product.image; text(title, product.title);
      product.variants.forEach((variant, index) => { const option = document.createElement("option"); option.value = index; option.disabled = !variant.available; text(option, `${variant.title === "Default Title" ? "Standard" : variant.title} · ${money(variant.price)}${variant.sku ? ` · ${variant.sku}` : ""}${variant.available ? "" : " · Unavailable"}`); select.append(option); });
      const first = product.status === "ACTIVE" && product.published ? product.variants.findIndex(item => item.available) : -1; if (first >= 0) select.value = first; select.disabled = first < 0; add.type = "button"; add.className = "btn btn-primary"; add.disabled = first < 0; text(add, first < 0 ? "Unavailable" : "Add");
      add.onclick = () => { const variant = product.variants[Number(select.value)]; if (!variant?.available) return; const duplicate = orderLines.find(item => item.variantId === variant.id); if (duplicate) duplicate.quantity = String(numeric(duplicate.quantity) + 1); else orderLines.push({productId: product.id, variantId: variant.id, title: product.title, variantTitle: variant.title, sku: variant.sku, image: variant.image || product.image, url: product.url, price: variant.price, quantity: "1", discount: "0", tax: "0"}); renderOrderLines(); document.getElementById("order-product-search").hidden = true; };
      select.onchange = () => { const variant = product.variants[Number(select.value)]; if (variant?.image?.startsWith("https://")) image.src = variant.image; }; content.append(title, select, add); card.append(image, content); return card;
    }
    async function searchOrderProducts() {
      const generation = ++orderSearchGeneration, results = document.getElementById("order-product-results"), status = document.getElementById("order-product-status"); text(status, "Searching Shopify…");
      try { const data = await api(`/crm/whatsapp/api/products?q=${encodeURIComponent(orderInput("order-product-query").value.trim())}&limit=20`); if (generation !== orderSearchGeneration) return; results.replaceChildren(...data.products.map(orderProductCard)); text(status, data.products.length ? "Choose an available variant." : "No products found."); } catch (error) { text(status, error.message); }
    }
    document.getElementById("create-order-button").onclick = () => openOrderDrawer();
    document.getElementById("close-order").onclick = closeOrderDrawer; document.getElementById("cancel-order").onclick = closeOrderDrawer; orderScrim.onclick = closeOrderDrawer;
    document.getElementById("order-add-product").onclick = () => { document.getElementById("order-product-search").hidden = false; orderInput("order-product-query").focus(); searchOrderProducts(); };
    orderInput("order-product-query").oninput = () => { clearTimeout(orderSearchTimer); orderSearchTimer = setTimeout(searchOrderProducts, 300); };
    ["order-discount", "order-shipping", "order-tax"].forEach(id => orderInput(id).oninput = calculateOrderPreview);
    document.getElementById("order-form").onsubmit = async event => {
      event.preventDefault(); if (!orderLines.length) { text(orderError, "Add at least one product."); return; } const save = document.getElementById("save-order"), orderId = orderInput("order-id").value;
      const payload = {crm_conversation_id: selected, customer_name: orderInput("order-customer-name").value, whatsapp_phone: orderInput("order-phone").value, alternative_phone: orderInput("order-alt-phone").value, delivery_contact_person: orderInput("order-contact").value, delivery_address: orderInput("order-address").value, city: orderInput("order-city").value, district: orderInput("order-district").value, postal_code: orderInput("order-postal").value, customer_notes: orderInput("order-notes").value, order_discount: orderInput("order-discount").value, shipping_total: orderInput("order-shipping").value, tax_total: orderInput("order-tax").value, lines: orderLines.map(line => ({shopify_product_id: line.productId, shopify_variant_id: line.variantId, quantity: line.quantity, discount_amount: line.discount, tax_amount: line.tax}))};
      save.disabled = true; text(save, "Saving…"); text(orderError, "");
      try { const csrfData = await api("/api/sales-orders/csrf"); const data = await api(orderId ? `/api/sales-orders/${orderId}` : "/api/sales-orders", {method: orderId ? "PUT" : "POST", headers:{"Content-Type":"application/json", "X-CSRF-Token":csrfData.csrf_token, ...(orderId ? {} : {"Idempotency-Key":orderDraftKey})}, body:JSON.stringify(payload)}); location.href = `/crm/sales-orders/${data.order.id}`; } catch (error) { text(orderError, error.message); save.disabled = false; text(save, "Save Draft"); }
    };
    const editId = Number(orderDrawer.dataset.editOrder); if (editId) api(`/api/sales-orders/${editId}`).then(data => openOrderDrawer(data.order)).catch(error => showComposerMessage(error.message, true));
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { loadList(); if (selected) openConversation(selected, false); } });
  loadList(); if (selected) openConversation(selected, true); setInterval(loadList, 7000); setInterval(() => { if (selected && !document.hidden) openConversation(selected, false); }, 4000);
})();
