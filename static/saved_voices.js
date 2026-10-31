(() => {
  "use strict";
  const library = document.getElementById("saved-voice-library"), dialog = document.getElementById("saved-voice-dialog");
  if (!library && !dialog) return;
  const root = library || dialog, inbox = document.getElementById("inbox"), list = root.querySelector("#saved-voice-list"),
    status = root.querySelector("#saved-voice-status"), search = root.querySelector("#saved-voice-search"), more = root.querySelector("#saved-voice-more"),
    form = document.getElementById("saved-voice-form"), send = document.getElementById("saved-voice-send"), check = document.getElementById("saved-voice-check");
  let rows = [], generation = 0, csrf = library?.dataset.csrf || "", staff, chosen, target, busy = false, timer, edit, previewUrl;
  const text = (tag, value) => { const node = document.createElement(tag); node.textContent = value; return node; };
  const clock = seconds => { const total = Math.round(seconds); return `${Math.floor(total / 60)}:${String(total % 60).padStart(2,"0")}`; };
  async function api(path, options = {}) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 120000);
    try {
      const response = await fetch(path, {credentials:"same-origin", cache:"no-store", ...options, signal:controller.signal});
      if (response.redirected) throw new Error("Please sign in again to use Saved Voices.");
      let data = {}; try { data = await response.json(); } catch (_) {}
      if (!response.ok) { const error = new Error(typeof data.detail === "string" ? data.detail : "Request could not be completed."); error.status = response.status; throw error; }
      return data;
    } finally { clearTimeout(timeout); }
  }
  function pause() { root.querySelectorAll("audio").forEach(audio => audio.pause()); }
  root.addEventListener("play", event => root.querySelectorAll("audio").forEach(audio => { if (audio !== event.target) audio.pause(); }), true);
  function render() {
    list.replaceChildren();
    for (const row of rows) {
      const card = text("article", ""); card.className = "voice-library-card"; card.dataset.selected = String(chosen?.id === row.id);
      card.append(text("h3", row.title), text("small", `${row.category} · ${row.language} · ${clock(row.duration_seconds)}${row.active ? "" : " · Inactive"}`));
      const audio = document.createElement("audio"); audio.controls = true; audio.preload = "none"; audio.src = `/crm/saved-voices/api/${row.id}/audio`;
      const ready = row.validation_current === true;
      const state = ready ? "Ready to send · OGG/Opus" : row.processing_status === "PROCESSING" ? "Processing" : row.processing_status === "FAILED" ? "Conversion failed" : "Needs validation";
      card.append(text("strong", state));
      if (!ready) {
        audio.removeAttribute("src");
        if (row.normalization_error) card.append(text("p",row.normalization_error));
        const prepare = text("button", "Prepare recording"); prepare.type = "button"; prepare.disabled = busy;
        prepare.onclick = async () => {
          if (busy) return; busy = true; status.textContent = "Processing recording…"; render();
          try { await api(`/crm/saved-voices/api/${row.id}/prepare`, {method:"POST",headers:{"X-CSRF-Token":csrf}}); status.textContent = "Ready to send. Preview the normalized recording before choosing it."; }
          catch (error) { status.textContent = error.message; }
          finally { busy = false; await load(); }
        }; card.append(prepare);
      }
      if (row.audio_diagnostics?.source && row.audio_diagnostics?.normalized) {
        const detail = document.createElement("details"), summary = text("summary", "Audio details");
        const source = row.audio_diagnostics.source, output = row.audio_diagnostics.normalized;
        detail.append(summary, text("small", `${source.container} / ${source.codec} → ${output.container} / ${output.codec} · ${output.channels} channel · ${output.sample_rate} Hz · ${output.duration_seconds}s · ${output.size_bytes} bytes`));
        card.append(detail);
      }
      card.append(audio, text("p", row.transcript));
      if (dialog) {
        const choose = text("button", "Choose recording"); choose.type = "button"; choose.className = "btn btn-secondary";
        choose.disabled = busy || row.ready_to_send !== true; choose.onclick = () => {
          if (busy || row.ready_to_send !== true) return; pause(); chosen = {...row};
          document.getElementById("saved-voice-selection").hidden = false;
          document.getElementById("saved-voice-selected-title").textContent = row.title;
          document.getElementById("saved-voice-selected-transcript").textContent = row.transcript;
          document.getElementById("saved-voice-selected-audio").src = audio.src;
          send.disabled = false; check.hidden = !pending(); render();
        }; card.append(choose);
      } else if (library.dataset.manage === "true") {
        const change = text("button", "Edit details"), toggle = text("button", row.active ? "Deactivate" : "Activate");
        change.type = toggle.type = "button"; change.className = toggle.className = "btn btn-secondary";
        change.disabled = toggle.disabled = busy;
        change.onclick = () => {
          if (busy) return; edit = row;
          for (const name of ["title","category","transcript","language"]) form.elements[name].value = row[name];
          form.elements.audio.disabled = true; form.elements.audio.required = false;
          document.getElementById("saved-voice-file-label").hidden = true;
          document.getElementById("saved-voice-editor-title").textContent = "Edit recording details";
          document.getElementById("saved-voice-save").textContent = "Save details";
          document.getElementById("saved-voice-cancel").hidden = false; form.scrollIntoView({block:"start",behavior:"smooth"});
        };
        toggle.onclick = async () => {
          if (busy) return; busy = true; render();
          try { await saveDetails(row, !row.active); status.textContent = row.active ? "Recording deactivated." : "Recording activated."; await load(); }
          catch (error) { status.textContent = error.message; } finally { busy = false; render(); }
        }; card.append(change, toggle);
      }
      list.append(card);
    }
  }
  async function load(append = false) {
    const version = ++generation, offset = append ? rows.length : 0;
    const query = new URLSearchParams({q:search.value,offset:String(offset),inactive:String(!!document.getElementById("saved-voice-inactive")?.checked)});
    try {
      const data = await api(`/crm/saved-voices/api?${query}`); if (version !== generation) return;
      csrf = data.csrf_token; staff = data.staff_id; rows = append ? rows.concat(data.voices) : data.voices;
      more.hidden = rows.length >= data.total; render();
      if (!rows.length) status.textContent = "No saved recordings found.";
    } catch (error) { if (version === generation) status.textContent = error.message; }
  }
  search.oninput = () => { clearTimeout(timer); generation++; timer = setTimeout(() => load(), 250); };
  more.onclick = () => load(true);
  document.getElementById("saved-voice-inactive")?.addEventListener("change", () => load());
  async function saveDetails(row, active, values) {
    return api(`/crm/saved-voices/api/${row.id}`, {method:"PATCH",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},
      body:JSON.stringify({...Object.fromEntries(["title","category","transcript","language"].map(name => [name, values ? values[name] : row[name]])), active, revision:row.revision})});
  }
  function resetForm() {
    edit = null; form.reset(); form.elements.audio.disabled = false; form.elements.audio.required = true;
    document.getElementById("saved-voice-file-label").hidden = false; document.getElementById("saved-voice-cancel").hidden = true;
    document.getElementById("saved-voice-save").textContent = "Upload recording";
    document.getElementById("saved-voice-editor-title").textContent = "Upload a recording";
    const audio = document.getElementById("saved-voice-upload-preview"); audio.pause(); audio.removeAttribute("src"); audio.hidden = true;
    if (previewUrl) URL.revokeObjectURL(previewUrl); previewUrl = null;
  }
  if (form) {
    form.elements.audio.onchange = () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      const file = form.elements.audio.files[0], audio = document.getElementById("saved-voice-upload-preview");
      if (!file) { audio.hidden = true; return; }
      previewUrl = URL.createObjectURL(file); audio.src = previewUrl; audio.hidden = false;
    };
    document.getElementById("saved-voice-cancel").onclick = resetForm;
    form.onsubmit = async event => {
      event.preventDefault(); if (busy) return;
      const payload = new FormData(form), values = Object.fromEntries(payload); busy = true;
      form.querySelectorAll("input,textarea,select,button").forEach(node => node.disabled = true);
      status.textContent = edit ? "Saving details…" : "Processing recording…";
      try {
        if (edit) await saveDetails(edit, edit.active, values);
        else await api("/crm/saved-voices/api", {method:"POST",headers:{"X-CSRF-Token":csrf},body:payload});
        resetForm(); status.textContent = "Recording saved. You can pick it from Saved Voices in the inbox."; await load();
      } catch (error) { status.textContent = error.message; }
      finally { busy = false; form.querySelectorAll("input,textarea,select,button").forEach(node => node.disabled = false); form.elements.audio.disabled = !!edit; render(); }
    };
  }
  const pendingKey = () => `saved-voice:${staff}:${target}:${chosen?.id}`;
  function pending() { try { return JSON.parse(sessionStorage.getItem(pendingKey()) || "null"); } catch (_) { return null; } }
  async function transmit(checkOnly = false) {
    if (busy || !chosen || !target) return;
    const cid = target, recording = {...chosen}, slot = pendingKey();
    let attempt = pending(), outcome;
    busy = true; send.disabled = true; check.disabled = true; render();
    try {
      if (attempt) {
        try { outcome = await api(`/crm/whatsapp/api/conversations/${cid}/saved-voice-sends/${encodeURIComponent(attempt.key)}`); }
        catch (error) { if (error.status !== 404) throw error; }
      }
      if (!outcome && !checkOnly) {
        attempt = attempt || {key:crypto.randomUUID(), revision:recording.revision};
        // If storage is unavailable, fail before sending rather than lose recovery.
        sessionStorage.setItem(slot, JSON.stringify(attempt));
        outcome = await api(`/crm/whatsapp/api/conversations/${cid}/send-saved-voice`, {method:"POST",
          headers:{"Content-Type":"application/json","X-CSRF-Token":csrf,"Idempotency-Key":attempt.key},
          body:JSON.stringify({voice_id:recording.id,revision:attempt.revision})});
      }
      if (outcome?.status === "SENT" || outcome?.status === "FAILED") sessionStorage.removeItem(slot);
      if (target === cid && dialog.open) {
        status.textContent = !outcome ? "This attempt was not accepted. Click Send Voice to send it." :
          outcome.delivery_status === "failed" ? `WhatsApp reported delivery failed${outcome.delivery_error_code ? ` (code ${outcome.delivery_error_code})` : ""}. Review before sending again.` :
          outcome.status === "SENT" ? "Submitted to WhatsApp. Confirm playback on the recipient phone. The transcript was not sent as text." :
          outcome.status === "FAILED" ? "The voice was not sent. You may try again with Send Voice." :
          outcome.status === "UNKNOWN" ? "Delivery is uncertain. Check the customer's chat before sending this recording again. No automatic retry was made." :
          "Voice send is in progress. Use Check send status; do not send again.";
      }
      if (outcome?.status === "SENT") inbox.dispatchEvent(new CustomEvent("saved-voice-sent"));
    } catch (error) {
      if (error.status === 422 || (error.status === 409 && (error.message.startsWith("This recording changed") || error.message.startsWith("Recording was normalized")))) {
        sessionStorage.removeItem(slot); chosen = null;
        document.getElementById("saved-voice-selection").hidden = true;
      }
      if (target === cid && dialog.open) {
        status.textContent = error.status === 422 ? `Nothing was sent. ${error.message}` : `${error.message} Check send status before trying again.`;
        status.scrollIntoView({block:"nearest"});
      }
    }
    finally { busy = false; if (dialog.open) { send.disabled = !chosen; check.disabled = false; check.hidden = !pending(); render(); } }
  }
  if (dialog) {
    document.getElementById("saved-voices-open").onclick = async () => {
      if (busy || window.inboxMediaBusy) return;
      target = Number(inbox.dataset.activeConversation); if (!target) return;
      chosen = null; send.disabled = true; check.hidden = true; check.disabled = false; document.getElementById("saved-voice-selection").hidden = true;
      rows = []; render();
      const cid = target;
      document.getElementById("saved-voice-recipient").textContent = "Loading customer…";
      status.textContent = "Loading recordings. Nothing has been sent."; dialog.showModal();
      try {
        const data = await api(`/crm/whatsapp/api/conversations/${cid}`);
        if (target !== cid || !dialog.open) return;
        document.getElementById("saved-voice-recipient").textContent = `To: ${data.conversation.customer_name || "Customer"} · ${data.conversation.phone_number}`;
        status.textContent = "Choose a recording. Nothing has been sent."; load();
      } catch (error) { if (target === cid && dialog.open) status.textContent = error.message; }
    };
    const close = () => { generation++; pause(); dialog.close(); chosen = null; target = null; };
    document.getElementById("saved-voice-close").onclick = close;
    dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
    inbox.addEventListener("inbox-conversation", close);
    send.onclick = () => transmit(false); check.onclick = () => transmit(true);
  } else load();
})();
