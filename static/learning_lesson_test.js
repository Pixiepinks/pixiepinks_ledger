(() => {
  "use strict";
  const root = document.getElementById("learning-center"), dialog = document.getElementById("lesson-test-dialog"),
    open = document.getElementById("learning-test-open"), form = document.getElementById("lesson-test-form"),
    close = document.getElementById("lesson-test-close"), status = document.getElementById("lesson-test-status"),
    results = document.getElementById("lesson-test-results");
  let selection = null, running = false;
  root.addEventListener("learning-test-selection", event => {
    selection = event.detail; open.disabled = !selection.manage || !selection.published || running;
    open.title = selection.published ? "Compare AI replies without changing live retrieval" : "Publish this lesson before testing it";
  });
  open.onclick = () => {
    if (open.disabled || !selection) return;
    document.getElementById("lesson-test-name").textContent = selection.title;
    results.hidden = true; status.textContent = "Enter a synthetic sample. Nothing is sent until you click Run comparison, and no WhatsApp messages are sent.";
    dialog.showModal(); form.elements.message.focus();
  };
  close.onclick = () => { if (!running) dialog.close(); };
  dialog.addEventListener("cancel", event => { if (running) event.preventDefault(); });
  function invalidateResults() {
    if (!running && !results.hidden) { results.hidden = true; status.textContent = "Inputs changed. Run a new comparison; no automatic request was sent."; }
  }
  form.addEventListener("input", invalidateResults); form.addEventListener("change", invalidateResults);
  form.elements.context_kind.onchange = () => {
    const empty = form.elements.context_kind.value === "none";
    form.elements.context_text.disabled = empty;
    form.elements.context_text.required = !empty;
    if (empty) form.elements.context_text.value = "";
  };
  const presets = {
    unclear: ["I want this.", "none", ""],
    quote: ["Is this available?", "quoted_product", "The customer quoted a previously shared red 20-inch boys bicycle. Exact stock must be verified."],
    image: ["How much is this?", "image_description", "Synthetic image description: a red bicycle. The exact product is not yet verified."],
    ad: ["Can I get more information?", "ad_description", "Synthetic advertisement description: a 20-inch boys bicycle. The exact Shopify product is not yet verified."]
  };
  dialog.querySelectorAll("[data-lesson-test-preset]").forEach(button => button.onclick = () => {
    if (running) return;
    const [message, kind, context] = presets[button.dataset.lessonTestPreset];
    form.elements.message.value = message; form.elements.context_kind.value = kind;
    form.elements.context_kind.onchange(); form.elements.context_text.value = context;
    results.hidden = true; status.textContent = "Sample filled. Review it, then click Run comparison. No request has been sent.";
  });
  form.onsubmit = async event => {
    event.preventDefault(); if (running || !selection?.published || !selection.manage) return;
    const current = {...selection}, body = {candidate_id: current.candidate_id, message: form.elements.message.value,
      mode: form.elements.mode.value, context_kind: form.elements.context_kind.value,
      context_text: form.elements.context_text.value, product_handle: form.elements.product_handle.value.trim()};
    running = true; open.disabled = true; close.disabled = true; results.hidden = true;
    dialog.querySelectorAll("input,textarea,select,button").forEach(node => node.disabled = true);
    status.textContent = "Running two AI requests. Live flags are unchanged; no WhatsApp messages will be sent…";
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 60000);
    try {
      const response = await fetch("/crm/bot-management/learning/api/lesson-test", {method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": current.csrf_token}, body: JSON.stringify(body), signal: controller.signal});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Lesson comparison is unavailable.");
      for (const [key, id] of [["without_lesson", "lesson-test-before"], ["with_lesson", "lesson-test-after"]]) {
        document.getElementById(id).textContent = data[key].reply || "No AI reply generated.";
        document.getElementById(id + "-status").textContent = `${data[key].status} · Tokens: ${data[key].tokens ?? "not reported"}`;
      }
      const trace = document.getElementById("lesson-test-trace"); trace.replaceChildren();
      const facts = [["Published version", `${data.version_id} (version ${data.version_number})`], ["Mode", data.mode],
        ["Selected lesson passes shared keyword/scope matching", data.natural_match ? "Yes" : "No"],
        ["Selected lesson supplied to model", data.lesson_supplied ? "Yes" : "No"], ["Reason", data.reason],
        ["Server-verified Shopify context", data.verified_shopify_context ? "Yes" : "No"],
        ["Input changed by privacy filtering", data.input_redacted ? "Yes" : "No"],
        ["Live retrieval flag (unchanged)", String(data.live_retrieval_enabled)], ["Model", data.model]];
      for (const [label, value] of facts) { const row = document.createElement("p"); row.textContent = `${label}: ${value}`; trace.append(row); }
      document.getElementById("lesson-test-notice").textContent = data.notice; results.hidden = false;
      status.textContent = data.without_lesson.status === "GENERATED" && data.with_lesson.status === "GENERATED"
        ? (data.lesson_supplied ? "Comparison completed. Review the behavior; this is not an automatic pass/fail assessment." : "No keyword match: the selected lesson was not supplied in the second reply. Force preview is available as a separate test mode.")
        : "One or both AI requests failed. A fallback is not presented as a successful test.";
    } catch (error) {
      status.textContent = error.name === "AbortError" ? "Test timed out. It was not automatically retried; provider requests may already have incurred charges." : error.message;
    } finally {
      clearTimeout(timeout); running = false;
      dialog.querySelectorAll("input,textarea,select,button").forEach(node => node.disabled = false);
      form.elements.context_kind.onchange(); open.disabled = !selection?.published || !selection.manage;
    }
  };
})();
