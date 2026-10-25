(() => {
  "use strict";
  const root = document.getElementById("inbox"), picker = document.getElementById("attachment-file"), preview = document.getElementById("attachment-preview"), menu = document.getElementById("attachment-menu"), attach = document.getElementById("attachment-button"), send = document.getElementById("attachment-send"), remove = document.getElementById("attachment-remove"), check = document.getElementById("attachment-check"), caption = document.getElementById("attachment-caption"), status = document.getElementById("attachment-status"), progress = document.getElementById("attachment-progress"), media = document.getElementById("attachment-preview-media");
  let policy, file, objectUrl, operation, busy = false, generation = 0, ownsComposer = false;
  const current = () => Number(root.dataset.activeConversation || root.dataset.initial) || null;
  const storageKey = () => `whatsapp-media:${policy.staff_id}:${current()}`;
  const remember = value => { try { if (value) sessionStorage.setItem(storageKey(), JSON.stringify(value)); else sessionStorage.removeItem(storageKey()); } catch (_) { /* status remains available on server */ } };
  const message = text => { status.textContent = text; };
  const active = value => {
    busy = value; window.inboxMediaBusy = value;
    attach.disabled = value || !policy; send.disabled = value || !file || !!operation;
    remove.disabled = value; caption.disabled = value || !!operation;
    const owns = value || !!file || !!operation;
    if (owns || ownsComposer) {
      ["record-button", "catalog-button", "send-button"].forEach(id => { document.getElementById(id).disabled = owns; });
      document.getElementById("text-composer").hidden = owns;
    }
    ownsComposer = owns;
  };
  function clear() {
    if (busy) return;
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null; file = null; operation = null; picker.value = ""; caption.value = "";
    media.replaceChildren(); preview.hidden = true; check.hidden = true; progress.hidden = true; message(""); active(false);
  }
  function renderOutcome(data) {
    if (data.status === "SENT") {
      remember(null); operation = null; file = null;
      if (objectUrl) URL.revokeObjectURL(objectUrl); objectUrl = null; media.replaceChildren();
      message("Attachment sent. Delivery updates appear in the conversation."); check.hidden = true; remove.textContent = "Close";
    } else if (["UPLOAD_FAILED", "SEND_FAILED"].includes(data.status)) {
      message(data.status === "UPLOAD_FAILED" ? "Upload failed. No customer message was sent. Remove and select the file to start a new attempt." : "Meta rejected this attachment message. Remove and review the file or reply window before trying again.");
      remember(null); check.hidden = true; remove.textContent = "Close";
    } else {
      message("Send outcome is not confirmed. Check the conversation and send status before starting another attempt. This attachment will not be sent again automatically.");
      check.hidden = false; remove.textContent = "Dismiss";
    }
  }
  async function checkStatus() {
    if (!operation || busy) return;
    const target = current(), key = operation.key;
    try {
      const response = await fetch(`/crm/whatsapp/api/conversations/${target}/media-sends/${key}`, {credentials: "same-origin", cache: "no-store"});
      if (target !== current() || key !== operation?.key) return;
      if (response.ok) renderOutcome(await response.json());
      else message("Send status is unavailable. Check the conversation before sending another attachment.");
    } catch (_) { message("Connection unavailable. Check status again before resending."); }
    active(false);
  }
  async function restore() {
    const ticket = ++generation; clear();
    try {
      if (!policy) { const response = await fetch("/crm/whatsapp/api/media-policy", {credentials: "same-origin", cache: "no-store"}); if (!response.ok) throw new Error(); policy = await response.json(); }
      if (ticket !== generation) return;
      active(false);
      let saved; try { saved = JSON.parse(sessionStorage.getItem(storageKey())); } catch (_) { /* no saved receipt */ }
      if (!saved || !/^[A-Za-z0-9_-]{16,100}$/.test(saved.key)) return;
      operation = saved; preview.hidden = false;
      document.getElementById("attachment-filename").textContent = "Previous attachment send";
      document.getElementById("attachment-info").textContent = "File contents are not retained in your browser.";
      active(false); await checkStatus();
    } catch (_) { attach.disabled = true; }
  }
  attach.disabled = true;
  attach.onclick = () => { menu.hidden = !menu.hidden; attach.setAttribute("aria-expanded", String(!menu.hidden)); };
  menu.querySelectorAll("[data-media-category]").forEach(button => { button.onclick = () => {
    if (!policy || !current() || busy) return;
    picker.accept = Object.entries(policy.formats).filter(([, info]) => info.type === button.dataset.mediaCategory).map(([ext]) => ext).join(",");
    menu.hidden = true; attach.setAttribute("aria-expanded", "false"); picker.click();
  }; });
  picker.onchange = () => {
    if (busy) return;
    const chosen = picker.files[0]; if (!chosen) return;
    if (operation && !confirm("Check the conversation before starting another send. Continue with a new attachment?")) { picker.value = ""; return; }
    const info = policy?.formats[`.${chosen.name.split(".").pop().toLowerCase()}`];
    clear(); preview.hidden = false;
    if (!info || !chosen.size || chosen.size > info.max_bytes) { message("Unsupported file or configured size limit exceeded."); return; }
    file = chosen; remove.textContent = "Remove"; caption.value = "";
    document.getElementById("attachment-filename").textContent = file.name;
    document.getElementById("attachment-info").textContent = `${info.mime} · ${(file.size / 1048576).toFixed(2)} MB`;
    if (["image", "video"].includes(info.type)) {
      objectUrl = URL.createObjectURL(file);
      const element = document.createElement(info.type === "image" ? "img" : "video");
      if (info.type === "image") element.alt = "Selected photo"; else { element.controls = true; element.preload = "metadata"; }
      element.src = objectUrl; media.append(element);
    }
    active(false);
  };
  remove.onclick = () => {
    if (operation && !check.hidden && !confirm("The send may already have reached the customer. Dismiss this notice? The saved receipt remains available after refresh.")) return;
    clear();
  };
  check.onclick = checkStatus;
  send.onclick = async () => {
    if (busy || !file || operation || !current()) return;
    if (caption.value.length > policy.caption_max_length) { message("Caption exceeds 1024 characters."); return; }
    const target = current(), key = crypto.randomUUID(), form = new FormData();
    form.append("file", file, file.name); form.append("caption", caption.value);
    operation = {key}; remember(operation); active(true); progress.hidden = false; progress.value = 0; message("Uploading attachment…");
    const xhr = new XMLHttpRequest(); xhr.open("POST", `/crm/whatsapp/api/conversations/${target}/send-media`); xhr.timeout = 150000;
    xhr.setRequestHeader("X-CSRF-Token", policy.csrf_token); xhr.setRequestHeader("Idempotency-Key", key);
    xhr.upload.onprogress = event => { if (event.lengthComputable) { progress.value = event.loaded / event.total * 100; message(progress.value >= 100 ? "Validating and sending attachment…" : `Uploading ${Math.round(progress.value)}%…`); } };
    xhr.onload = () => {
      let data; try { data = JSON.parse(xhr.responseText); } catch (_) { data = {}; }
      if (xhr.status >= 200 && xhr.status < 300 || data.status === "OUTCOME_UNCERTAIN") renderOutcome(data);
      else if (xhr.status === 503 && data.status === "VALIDATION_UNAVAILABLE") {
        remember(null); operation = null; message(data.error); check.hidden = true;
      }
      else if ([400, 403, 404, 408, 413, 415, 422, 429].includes(xhr.status)) {
        remember(null); operation = null; message(data.detail || "Attachment request rejected. Review the file and try again.");
      } else { message(data.detail || "Send result is unavailable. Check status and the conversation before trying again."); check.hidden = false; }
      progress.hidden = true; active(false);
    };
    xhr.onerror = xhr.ontimeout = () => { message("Connection interrupted. The server may still be sending. Check send status before trying again."); check.hidden = false; progress.hidden = true; active(false); };
    xhr.send(form);
  };
  window.addEventListener("beforeunload", event => { if (busy) { event.preventDefault(); event.returnValue = ""; } });
  root.addEventListener("inbox-conversation", restore);
  restore();
})();
