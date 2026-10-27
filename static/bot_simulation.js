(() => {
  "use strict";
  const root = document.getElementById("bot-simulator"), form = document.getElementById("bot-simulation-form"),
    run = document.getElementById("bot-simulation-run"), status = document.getElementById("bot-simulation-status"),
    results = document.getElementById("bot-simulation-results");
  const allowed = root.dataset.canRun === "true";
  let running = false;
  run.disabled = !allowed;
  status.textContent = allowed ? "Choose DRAFT or LIVE, enter a synthetic question and run once. No request is sent by filling a sample." : "Only administrators and sales managers can run billable AI simulations.";
  function clearResult() {
    if (!running && !results.hidden) { results.hidden = true; status.textContent = "Inputs changed. Run again to generate a new preview; no automatic request was sent."; }
  }
  form.addEventListener("input", clearResult); form.addEventListener("change", clearResult);
  const presets = {greeting:"Hello!", bag:"Do you have school bags?", toy:"Can you show me available toys?", gift:"I am looking for a birthday gift.", delivery:"How does your delivery work?", returns:"What is your returns policy?"};
  form.querySelectorAll("[data-simulator-preset]").forEach(button => button.onclick = () => {
    if (running) return;
    form.elements.message.value = presets[button.dataset.simulatorPreset]; clearResult();
    status.textContent = "Sample filled. Review the configuration, then click Run AI simulation. Nothing has been sent.";
  });
  form.onsubmit = async event => {
    event.preventDefault(); if (running || !allowed) return;
    const body = Object.fromEntries(["mode", "message", "context_text", "catalog_mode", "product_handle"].map(name => [name, form.elements[name].value]));
    body.product_handle = body.product_handle.trim();
    running = true; results.hidden = true;
    form.querySelectorAll("input,textarea,select,button").forEach(node => node.disabled = true);
    status.textContent = "Loading configuration and generating a preview; automatic recommendations may use two AI calls…";
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 60000);
    try {
      const response = await fetch("/crm/bot-management/api/simulate", {method:"POST",credentials:"same-origin",cache:"no-store",
        headers:{"Content-Type":"application/json","X-CSRF-Token":root.dataset.csrf},body:JSON.stringify(body),signal:controller.signal});
      const data = await response.json(); if (!response.ok) throw new Error(data.detail || "Simulation unavailable.");
      document.getElementById("bot-simulation-reply").textContent = data.reply || "No AI reply generated.";
      document.getElementById("bot-simulation-usage").textContent = `${data.status} · ${data.model} · Tokens: ${data.tokens ?? "not reported"} · ${data.trace.mode}`;
      document.getElementById("bot-simulation-warnings").textContent = data.warnings.join(" ");
      document.getElementById("bot-simulation-trace").textContent = JSON.stringify(data.trace,null,2);
      document.getElementById("bot-simulation-notice").textContent = data.notice;
      const products = document.getElementById("bot-simulation-products"); products.replaceChildren();
      for (const product of data.products) { const row = document.createElement("p"), link = document.createElement("a");
        link.textContent = product.title; link.href = `https://www.pixiepinks.shop/products/${encodeURIComponent(product.handle)}`;
        link.target = "_blank"; link.rel = "noopener noreferrer"; row.append(link);
        if (product.image) { const img = document.createElement("img");
          const url = new URL(product.image);
          if (url.protocol === "https:" && (url.hostname === "cdn.shopify.com" || url.hostname.endsWith(".myshopify.com"))) {
            img.src = url.href; img.alt = product.title; img.width = 100; img.loading = "lazy"; row.prepend(img);
          }
        }
        if (product.price != null) { const price = document.createElement("span");
          price.textContent = ` · ${product.currency || "LKR"} ${product.price}`; row.append(price); }
        products.append(row); }
      if (!data.products.length) products.textContent = "No products verified for this preview.";
      results.hidden = false;
      status.textContent = data.status === "GENERATED" ? "AI preview generated. Review its language and facts; no customer action occurred." : "The provider did not generate a reply. This is not a successful simulation.";
    } catch (error) {
      status.textContent = error.name === "AbortError" ? "Preview timed out; no automatic retry was made. A provider request may already have incurred charges." : error.message;
    } finally {
      clearTimeout(timeout); running = false;
      form.querySelectorAll("input,textarea,select,button").forEach(node => node.disabled = false); run.disabled = !allowed;
    }
  };
})();
