(() => {
  "use strict";
  const root = document.getElementById("inbox"), list = document.getElementById("conversation-list"), messages = document.getElementById("messages"), errorBox = document.getElementById("send-error");
  let selected = Number(root.dataset.initial) || null, filter = new URLSearchParams(location.search).get("filter") || "all";
  let recorder, stream, timer, blob, blobUrl, started, duration, voiceKey, chunks = [], discard = false;
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
    try {
      const search = document.getElementById("search").value;
      const data = await api(`/crm/whatsapp/api/conversations?filter=${encodeURIComponent(filter)}&search=${encodeURIComponent(search)}`);
      list.replaceChildren(...data.conversations.map(conversationRow)); text(document.getElementById("list-status"), `${data.conversations.length} shown`);
      if (!data.conversations.length) { const empty = document.createElement("div"); empty.className = "empty"; text(empty, search ? "No search results." : "No WhatsApp conversations."); list.replaceChildren(empty); }
    } catch (_) { list.innerHTML = '<div class="empty">Could not load conversations.</div>'; }
  }
  function detailRow(label, value) { const row = document.createElement("div"), name = document.createElement("span"), content = document.createElement("span"); row.className = "detail-row"; text(name, label); text(content, value); row.append(name, content); return row; }
  function renderDetails(data) {
    const profile = document.getElementById("profile"); profile.replaceChildren(); const heading = document.createElement("h3"); text(heading, "Customer profile");
    profile.append(heading, detailRow("Name", data.conversation.customer_name), detailRow("WhatsApp", data.conversation.phone_number), detailRow("Mode", data.conversation.mode));
    const order = document.getElementById("order"); order.replaceChildren();
    if (!data.order) { const empty = document.createElement("div"); empty.className = "empty"; text(empty, "No active order."); order.append(empty); return; }
    const value = data.order, orderHeading = document.createElement("h3"); text(orderHeading, "Order summary"); order.append(orderHeading);
    if (value.product_image_url?.startsWith("https://")) { const image = new Image(); image.className = "product-thumb"; image.alt = value.product_title; image.src = value.product_image_url; order.append(image); }
    order.append(detailRow("Product", value.product_title), detailRow("Variant", value.variant_title || "—"));
    if (value.bicycle_size) order.append(detailRow("Bicycle", `${value.bicycle_gender || ""} · age ${value.bicycle_age || "—"} · ${value.bicycle_size}\"`));
    order.append(detailRow("Product", money(value.product_price)), detailRow("Initial service", money(value.service_charge)), detailRow("Delivery", value.delivery_charge ? money(value.delivery_charge) : "FREE"), detailRow("TOTAL", money(value.total)), detailRow("Status", value.payment_required ? "PAYMENT DETAILS REQUIRED" : value.status.replaceAll("_", " ")));
    if (value.contact_person_name) order.append(detailRow("Contact", value.contact_person_name), detailRow("Primary phone", value.primary_phone), detailRow("Alternative phone", value.alternative_phone), detailRow("Delivery address", value.delivery_address));
    if (value.product_url?.startsWith("https://")) { const link = document.createElement("a"); link.href = value.product_url; link.target = "_blank"; link.rel = "noopener noreferrer"; text(link, "Open in Shopify ↗"); order.append(link); }
  }
  function renderMessage(message) {
    const wrapper = document.createElement("div"), bubble = document.createElement("div"), metadata = document.createElement("small"); wrapper.className = `message ${message.direction}`; bubble.className = "bubble";
    if (message.message_type === "audio" && message.media_url) { bubble.classList.add("audio-bubble"); const label = document.createElement("span"), audio = document.createElement("audio"); text(label, `Voice message${message.duration_seconds ? ` · ${clock(message.duration_seconds)}` : ""}`); audio.controls = true; audio.preload = "metadata"; audio.src = message.media_url; bubble.append(label, audio); } else text(bubble, message.text);
    text(metadata, `${message.direction === "inbound" ? "Customer" : message.source.startsWith("staff") ? "Staff" : "AI"} · ${new Date(message.timestamp).toLocaleString()} · ${message.status}`); wrapper.append(bubble, metadata); return wrapper;
  }
  async function openConversation(id, markRead = false) {
    selected = id; root.classList.add("chat-open"); document.getElementById("chat-empty").hidden = true; document.getElementById("chat").hidden = false;
    try {
      if (markRead) await api(`/crm/whatsapp/api/conversations/${id}/read`, {method: "POST"}); const data = await api(`/crm/whatsapp/api/conversations/${id}`);
      text(document.getElementById("chat-name"), data.conversation.customer_name); text(document.getElementById("chat-phone"), data.conversation.phone_number); document.getElementById("mode-badge").replaceChildren(badge(data.conversation.mode, data.conversation.mode.toLowerCase()));
      const action = document.getElementById("mode-action"); text(action, data.conversation.mode === "AI" ? "Take Over" : "Return to AI"); action.dataset.mode = data.conversation.mode; document.getElementById("payment-banner").hidden = !data.conversation.payment_required;
      messages.replaceChildren(...data.messages.map(renderMessage)); messages.scrollTop = messages.scrollHeight; renderDetails(data); loadList();
    } catch (_) { messages.innerHTML = '<div class="empty">Conversation not found or could not be loaded.</div>'; }
  }
  const stopTracks = () => { stream?.getTracks().forEach(track => track.stop()); stream = null; };
  function resetRecording() {
    clearInterval(timer); stopTracks(); recorder = null; chunks = []; duration = 0; voiceKey = null; if (blobUrl) URL.revokeObjectURL(blobUrl); blobUrl = null; blob = null;
    document.getElementById("recording-controls").hidden = true; document.getElementById("voice-preview").hidden = true; document.getElementById("text-composer").hidden = false; document.getElementById("send-button").hidden = false;
  }
  async function startRecording() {
    text(errorBox, ""); if (!selected) { text(errorBox, "Select a conversation first."); return; }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { text(errorBox, "Voice recording is not supported by this browser."); return; }
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}, video: false});
      const mime = ["audio/ogg;codecs=opus", "audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find(type => MediaRecorder.isTypeSupported(type)) || "";
      recorder = new MediaRecorder(stream, mime ? {mimeType: mime} : undefined); chunks = []; discard = false; started = performance.now();
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = () => {
        stopTracks(); clearInterval(timer); if (discard) { resetRecording(); return; } duration = (performance.now() - started) / 1000;
        blob = new Blob(chunks, {type: recorder.mimeType || "audio/webm"}); blobUrl = URL.createObjectURL(blob); voiceKey = crypto.randomUUID(); document.getElementById("voice-preview-audio").src = blobUrl; text(document.getElementById("voice-duration"), clock(duration)); document.getElementById("recording-controls").hidden = true; document.getElementById("voice-preview").hidden = false;
      };
      recorder.start(250); document.getElementById("text-composer").hidden = true; document.getElementById("send-button").hidden = true; document.getElementById("recording-controls").hidden = false;
      const update = () => { const elapsed = (performance.now() - started) / 1000; text(document.getElementById("recording-timer"), clock(elapsed)); if (elapsed >= 600 && recorder?.state === "recording") recorder.stop(); }; update(); timer = setInterval(update, 250);
    } catch (error) { stopTracks(); text(errorBox, error?.name === "NotAllowedError" ? "Microphone permission was denied. Allow microphone access and try again." : "The microphone could not be started."); }
  }
  async function sendVoice() {
    if (!selected || !blob) return; const button = document.getElementById("send-voice"); button.disabled = true; text(button, "Uploading…"); text(errorBox, ""); const form = new FormData(); form.append("audio", blob, blob.type.includes("ogg") ? "voice.ogg" : "voice.webm");
    try { await api(`/crm/whatsapp/api/conversations/${selected}/send-audio`, {method: "POST", headers: {"Idempotency-Key": voiceKey}, body: form}); resetRecording(); await openConversation(selected); }
    catch (error) { text(errorBox, `${error.message} Your recording is still available to retry.`); if (error.status === 502 || error.status === 422) voiceKey = crypto.randomUUID(); }
    finally { button.disabled = false; text(button, "Send Voice"); }
  }
  document.getElementById("search").addEventListener("input", () => { clearTimeout(window._searchTimer); window._searchTimer = setTimeout(loadList, 250); });
  document.querySelectorAll("#filters button").forEach(button => { if (button.dataset.filter === filter) { document.querySelectorAll("#filters button").forEach(item => item.classList.remove("selected")); button.classList.add("selected"); } button.onclick = () => { filter = button.dataset.filter; document.querySelectorAll("#filters button").forEach(item => item.classList.toggle("selected", item === button)); loadList(); }; });
  document.getElementById("mode-action").onclick = async event => { if (!selected) return; event.currentTarget.disabled = true; try { await api(`/crm/whatsapp/api/conversations/${selected}/${event.currentTarget.dataset.mode === "AI" ? "takeover" : "return-to-ai"}`, {method: "POST"}); await openConversation(selected); } finally { event.currentTarget.disabled = false; } };
  document.getElementById("composer").onsubmit = async event => { event.preventDefault(); if (!selected) return; const input = document.getElementById("message-input"), button = document.getElementById("send-button"), body = input.value.trim(); if (!body) return; button.disabled = true; text(errorBox, ""); try { await api(`/crm/whatsapp/api/conversations/${selected}/send`, {method: "POST", headers: {"Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID()}, body: JSON.stringify({message: body})}); input.value = ""; await openConversation(selected); } catch (error) { text(errorBox, error.message); } finally { button.disabled = false; } };
  document.getElementById("record-button").onclick = startRecording; document.getElementById("stop-recording").onclick = () => { if (recorder?.state === "recording") recorder.stop(); }; document.getElementById("cancel-recording").onclick = () => { discard = true; if (recorder?.state === "recording") recorder.stop(); else resetRecording(); }; document.getElementById("delete-recording").onclick = resetRecording; document.getElementById("send-voice").onclick = sendVoice;
  document.getElementById("details-action").onclick = () => document.getElementById("details").classList.add("open"); document.getElementById("close-details").onclick = () => document.getElementById("details").classList.remove("open"); document.getElementById("back-list").onclick = () => root.classList.remove("chat-open");
  loadList(); if (selected) openConversation(selected, true); setInterval(loadList, 7000); setInterval(() => { if (selected) openConversation(selected, false); }, 4000);
})();
