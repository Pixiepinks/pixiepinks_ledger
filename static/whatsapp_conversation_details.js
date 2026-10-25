(() => {
  "use strict";
  const root = document.getElementById("inbox"), dialog = document.getElementById("conversation-details-dialog");
  const button = document.getElementById("details-action"), content = document.getElementById("conversation-details-content");
  const status = document.getElementById("conversation-details-status"), retry = document.getElementById("conversation-details-retry");
  let controller, generation = 0;
  function report(message, failed = false) { status.textContent = message; status.dataset.error = String(failed); }
  function row(label, value) {
    const item = document.createElement("div"), name = document.createElement("span"), detail = document.createElement("span");
    item.className = "detail-row"; name.textContent = label; detail.textContent = value ?? "Not recorded";
    item.append(name, detail); content.append(item); return detail;
  }
  async function load() {
    controller?.abort(); controller = new AbortController(); const requestController = controller, token = ++generation;
    const timeout = setTimeout(() => requestController.abort(), 15000);
    const id = Number(root.dataset.activeConversation);
    content.replaceChildren(); content.setAttribute("aria-busy", "true"); retry.hidden = true; report("Loading conversation details…");
    try {
      if (!Number.isSafeInteger(id) || id < 1) throw new Error("No conversation selected");
      const response = await fetch(`/crm/whatsapp/api/conversations/${id}/details`, {credentials: "same-origin", cache: "no-store", signal: requestController.signal});
      if (!response.ok || response.redirected) throw new Error("Details unavailable");
      const {conversation: data} = await response.json();
      if (token !== generation || !dialog.open) return;
      if (data.id !== id || !Number.isSafeInteger(data.id)) throw new Error("Conversation mismatch");
      const identity = row("Conversation ID", ""), value = document.createElement("code"), copy = document.createElement("button");
      identity.className = "conversation-id-value"; value.textContent = String(data.id); value.id = "conversation-internal-id";
      copy.type = "button"; copy.className = "btn btn-secondary"; copy.textContent = "Copy"; copy.setAttribute("aria-label", "Copy internal conversation ID");
      copy.onclick = async () => {
        try {
          await navigator.clipboard.writeText(String(data.id));
          if (token === generation && dialog.open) report("Conversation ID copied.");
        } catch (_) {
          if (token === generation && dialog.open) {
            const selection = window.getSelection(), range = document.createRange(); range.selectNodeContents(value);
            selection?.removeAllRanges(); selection?.addRange(range); report("Clipboard unavailable. The ID is selected; copy it manually.", true);
          }
        }
      };
      identity.append(value, copy);
      row("Name", data.customer_name); row("WhatsApp", data.phone_number);
      const country = data.phone_country;
      row("Phone country", country ? `${country.flag} ${country.name} (${country.dialing_code})` : "Unknown");
      row("Mode", data.mode); row("Assigned staff", data.assigned_staff);
      if (data.taken_over_by) row(data.mode === "HUMAN" ? "Taken over by" : "Last taken over by", data.taken_over_by);
      const learning = data.learning;
      row("Learning eligibility", ({EXCLUDED: "Excluded", WITHDRAWN: "Withdrawn", EVALUATION_ONLY: "Evaluation only", ELIGIBLE: "Eligible"})[learning.eligibility] || "Unavailable");
      row("Learning engine", learning.engine_enabled ? "Enabled" : "Disabled");
      row("Latest analysis", learning.analysis_status ? learning.analysis_status.replaceAll("_", " ") : "Not analyzed");
      if (learning.job_status) row("Related job", learning.job_status.replaceAll("_", " "));
      if (learning.analysis_completed_at) row("Analyzed at", new Intl.DateTimeFormat("en-GB", {timeZone: "Asia/Colombo", dateStyle: "medium", timeStyle: "short"}).format(new Date(learning.analysis_completed_at)));
      report("");
    } catch (error) {
      if (token !== generation || !dialog.open) return;
      content.replaceChildren(); report("Could not load conversation details. Check your access or connection and retry.", true); retry.hidden = false;
    } finally { clearTimeout(timeout); if (token === generation) content.setAttribute("aria-busy", "false"); }
  }
  button.onclick = () => { if (!dialog.open) dialog.showModal(); button.setAttribute("aria-expanded", "true"); load(); };
  document.getElementById("conversation-details-close").onclick = () => dialog.close();
  retry.onclick = load;
  dialog.addEventListener("keydown", event => {
    if (event.key !== "Tab") return;
    const focusable = [...dialog.querySelectorAll("button:not([disabled])")].filter(node => !node.hidden && node.getClientRects().length);
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  });
  document.getElementById("conversation-details-order").onclick = () => {
    dialog.close(); const sidebar = document.getElementById("details"); sidebar.classList.add("open");
    requestAnimationFrame(() => sidebar.focus());
  };
  dialog.addEventListener("close", () => { ++generation; controller?.abort(); content.replaceChildren(); content.setAttribute("aria-busy", "false"); report(""); button.setAttribute("aria-expanded", "false"); button.focus(); });
  root.addEventListener("inbox-conversation", () => { if (dialog.open) load(); });
})();
