(() => {
  "use strict";
  const base="/crm/bot-management/learning/api", root=document.getElementById("learning-center"), error=document.getElementById("learning-error");
  let csrf="", role="", dashboard, selected=null, offset=0, total=0, searchTimer, generation=0;
  const element=id=>document.getElementById(id), text=(node,value)=>{node.textContent=value==null?"Not available":String(value);};
  const date=value=>value?new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Colombo",dateStyle:"medium",timeStyle:"short"}).format(new Date(value)):"Not yet";
  async function api(path,method="GET",body=null,key=null){
    const response=await fetch(base+path,{method,credentials:"same-origin",cache:"no-store",headers:method==="GET"?{}:{"Content-Type":"application/json","X-CSRF-Token":csrf,"Idempotency-Key":key||crypto.randomUUID()},...(body?{body:JSON.stringify(body)}:{})});
    const data=await response.json();if(!response.ok)throw new Error(data.detail||"Learning request unavailable.");return data;
  }
  function table(target,rows,keys){
    target.replaceChildren();if(!rows.length){text(target,"No records yet.");return;}
    const table=document.createElement("table"),head=document.createElement("tr");keys.forEach(key=>{const th=document.createElement("th");text(th,key.replaceAll("_"," "));head.append(th);});table.append(head);
    rows.forEach(row=>{const tr=document.createElement("tr");keys.forEach(key=>{const td=document.createElement("td"),value=row[key];if(key==="diagnostics"){const button=document.createElement("button");button.type="button";button.textContent="View Diagnostics";button.disabled=!["ADMIN","SALES_MANAGER"].includes(role);button.onclick=()=>safe(()=>runDiagnostics(row.id));td.append(button);tr.append(td);return;}text(td,key.endsWith("_at")||key==="timestamp"?date(value):typeof value==="object"?JSON.stringify(value):value);tr.append(td);});table.append(tr);});target.append(table);
  }
  function show(panel){root.querySelectorAll("[data-panel]").forEach(node=>{node.hidden=node.dataset.panel!==panel;});root.querySelectorAll("[data-tab]").forEach(node=>node.classList.toggle("selected",node.dataset.tab===panel));}
  async function refresh(){
    dashboard=await api("/dashboard");csrf=dashboard.csrf_token;role=dashboard.role;
    const config=dashboard.settings,gates=dashboard.environment_gates;
    text(element("learning-latest-run"),dashboard.latest_run?runText(dashboard.latest_run):"No runs recorded.");
    element("learning-inspect-sources").disabled=role!=="ADMIN";
    text(element("learning-notice"),config.enabled&&gates.learning?"Learning enabled for explicitly eligible conversations. Publication remains separately gated.":"Learning is disabled. No customer conversations are being sent for learning. Complete privacy review and staged deployment before enabling.");
    if(config.notify_in_app&&["ADMIN","SALES_MANAGER"].includes(role)&&(dashboard.candidates.PENDING_REVIEW||0)>0)element("learning-notice").textContent+=` ${dashboard.candidates.PENDING_REVIEW} suggestions await manager review.`;
    const run=element("learning-run");run.disabled=!["ADMIN","SALES_MANAGER"].includes(role)||!gates.learning||!config.enabled||!config.privacy_reviewed||!config.notice_configured||!config.provider_reviewed;
    const metrics=[["Analyzed today",dashboard.today_analyzed],["Analyzed this week",dashboard.week_analyzed],["New candidates today",dashboard.new_candidates],["Auto publications today",dashboard.auto_published_today],["Pending reviews",dashboard.candidates.PENDING_REVIEW||0],["Rejected lessons",dashboard.candidates.REJECTED||0],["Published knowledge",dashboard.published_knowledge],["Provider-reported tokens",dashboard.actual_tokens],["Reserved cost estimate (USD)",Number(dashboard.reserved_cost_estimate).toFixed(4)],["Learning errors",dashboard.errors]];
    element("learning-metrics").replaceChildren(...metrics.map(([label,value])=>{const card=document.createElement("article"),count=document.createElement("strong"),name=document.createElement("span");text(count,value);text(name,label);card.append(count,name);return card;}));
    table(element("learning-outcomes"),[{period:"Last 7 days",...dashboard.observations},{period:"Previous 7 days (baseline)",...dashboard.baseline}],["period","observed_ai_conversations","correction_rate","takeover_rate","fallback_conversations","repeat_clarifications","retrieved_version_uses","resolved_annotations"]);
    text(element("learning-schedule"),`Last successful run: ${date(dashboard.last_successful_run?.completed_at)}. Next configured due: ${date(dashboard.next_schedule_due)}. Worker last seen: ${date(dashboard.worker_last_seen)}. External Railway cron deployment is not verified by this dashboard.`);
    const fields=element("learning-settings-fields");fields.replaceChildren();
    Object.entries(config).forEach(([name,value])=>{const label=document.createElement("label"),caption=document.createElement("span"),input=document.createElement("input");text(caption,name.replaceAll("_"," "));input.name=name;input.type=typeof value==="boolean"?"checkbox":typeof value==="number"?"number":"text";if(input.type==="checkbox")input.checked=value;else{input.value=Array.isArray(value)?value.join(","):value;if(input.type==="number")input.step="any";}input.disabled=role!=="ADMIN"||name==="timezone";label.append(caption,input);fields.append(label);});
    element("learning-settings-form").querySelector('button').disabled=role!=="ADMIN";
    element("learning-source-form").querySelectorAll('input,button').forEach(node=>node.disabled=role!=="ADMIN");
    ["learning-pause","learning-rollback-all"].forEach(id=>element(id).disabled=role!=="ADMIN");
    element("learning-case-form").querySelectorAll('input,select,button').forEach(node=>node.disabled=!["ADMIN","SALES_MANAGER"].includes(role));
    element("learning-feedback-form").querySelectorAll('input,select,button').forEach(node=>node.disabled=!["ADMIN","SALES_MANAGER"].includes(role));
  }
  async function suggestions(){
    const token=++generation,q=new URLSearchParams({q:element("learning-search").value,status:element("learning-status").value,language:element("learning-language").value,category:element("learning-category").value,product_category:element("learning-product-category").value,conflicts:String(element("learning-conflicts").checked),offset:String(offset)});
    if(element("learning-from").value)q.set("date_from",element("learning-from").value+"T00:00:00+05:30");if(element("learning-to").value)q.set("date_to",element("learning-to").value+"T23:59:59+05:30");
    const data=await api("/candidates?"+q);if(token!==generation)return;total=data.total;const rows=element("learning-candidates");rows.replaceChildren();
    data.items.forEach(row=>{const tr=document.createElement("tr");[row.title,`${row.category} / ${row.language}`,row.risk_level,row.evidence_count,row.confidence_score,row.validation_status,row.publication_status,date(row.created_at)].forEach(value=>{const td=document.createElement("td");text(td,value);tr.append(td);});const td=document.createElement("td"),button=document.createElement("button");button.type="button";text(button,"Review");button.onclick=()=>safe(()=>detail(row.id));td.append(button);tr.append(td);rows.append(tr);});
    text(element("learning-total"),`${total} suggestions`);element("learning-prev").disabled=offset===0;element("learning-next").disabled=offset+50>=total;
  }
  async function detail(id){
    const data=await api(`/candidates/${id}`);selected=data.candidate;element("learning-detail").hidden=false;
    text(element("learning-detail-title"),selected.title);text(element("learning-detail-meta"),`${selected.category} · ${selected.language} · ${selected.risk_level} · ${selected.publication_status} · ${selected.evidence_count} source conversations · ${selected.analysis_summary?.reason || "Human response evidence"} · ${selected.rejection_reason || ""}`);
    element("learning-detail-content").value=selected.proposed_content;element("learning-edit-title").value=selected.title;element("learning-verified").checked=false;
    ["learning-evidence","learning-evaluations","learning-product","learning-versions"].forEach((id,index)=>{const pre=document.createElement("pre");text(pre,JSON.stringify([data.evidence,data.evaluations,data.current_shopify_product,data.versions][index],null,2));element(id).replaceChildren(pre);});
    const manage=["ADMIN","SALES_MANAGER"].includes(role);element("learning-review-actions").querySelectorAll("button").forEach(node=>node.disabled=!manage);element("learning-restore").disabled=!manage;
    element("learning-detail-content").disabled=element("learning-edit-title").disabled=!manage;
    element("learning-restore-version").replaceChildren(...data.versions.filter(row=>row.content).map(row=>new Option(`Version ${row.version_number} · ${row.publication_status}`,row.id)));
    element("learning-detail").scrollIntoView({block:"nearest"});
  }
  function runText(job){
    const values=job.summary||{}, reasons=Object.entries(values.skip_reasons||{}).map(([name,count])=>`${name}: ${count}`).join(" · ");
    const label=job.status==="COMPLETED"&&job.conversations_processed===0?(values.outcome?"Completed — No eligible conversations found":"Completed — historical diagnostics unavailable"):job.status;
    return `${label} · ${job.conversations_processed} processed · ${job.candidates_generated} candidates${values.sources_evaluated!=null?` · ${values.sources_evaluated} sources evaluated`:""}${reasons?` · ${reasons}`:""}`;
  }
  async function runDiagnostics(id){
    const data=await api(`/jobs/${id}/diagnostics`), values=data.diagnostics||{};
    element("learning-run-diagnostics").hidden=false; text(element("learning-diagnostics-status"),data.historical_available?runText(data.job):"No diagnostic snapshot was recorded for this historical job. Inspect current sources separately; current state cannot prove historical skip reasons.");
    table(element("learning-diagnostics-summary"),Object.entries(values).filter(([key])=>key!=="sources").map(([metric,value])=>({metric,value})),["metric","value"]);
    table(element("learning-diagnostics-sources"),values.sources||[],["source_id","conversation_id","reason","origin_counts","window_counts","consent_at","messages_from","cursor","minimum_evidence","last_analysis_status"]);
    element("learning-run-diagnostics").focus();
  }
  async function history(){const data=await api("/history");table(element("learning-jobs"),data.jobs.map(job=>({...job,result:runText(job)})),["id","result","created_at","completed_at","conversations_processed","candidates_generated","candidates_auto_published","token_usage","estimated_cost","error_count","error_summary","diagnostics"]);table(element("learning-audit"),data.audit,["timestamp","actor_id","action","entity_type","entity_id","previous_state","new_state"]);}
  async function sources(){if(!["ADMIN","SALES_MANAGER"].includes(role)){text(element("learning-sources"),"Only managers may inspect source eligibility.");return;}const data=await api("/sources");table(element("learning-sources"),data.items,["id","conversation_id","allowed","consent_reference","consent_at","withdrawn_at","legal_hold","last_message_id","evaluation_only"]);const cases=await api("/regression-cases");table(element("learning-cases"),cases.items,["id","source_id","sanitized_question","expected_behavior","language","active"]);}
  async function watchJob(id,attempt=0){
    try{const job=await api(`/jobs/${id}`);text(element("learning-notice"),`Job ${id}: ${runText(job)}. Cron checks approximately every 15 minutes.`);text(element("learning-latest-run"),runText(job));if(["QUEUED","RUNNING","RETRY"].includes(job.status)&&attempt<45)setTimeout(()=>watchJob(id,attempt+1),5000);else await history();}catch(_){text(element("learning-notice"),`Job ${id} is queued; status could not be refreshed. Check History.`);}
  }
  element("learning-diagnostics-close").onclick=()=>{element("learning-run-diagnostics").hidden=true;};
  element("learning-inspect-sources").onclick=()=>safe(async()=>{const data=await api("/diagnostics/sources");element("learning-run-diagnostics").hidden=false;text(element("learning-diagnostics-status"),"Current state only; not a reconstruction of historical runs. No customer content is included.");table(element("learning-diagnostics-summary"),[{backend:data.database_backend,effective_enabled:data.effective_enabled,total_sources:data.total_sources,environment:data.environment}],["backend","effective_enabled","total_sources","environment"]);table(element("learning-diagnostics-sources"),data.sources,["source_id","conversation_id","reason","origin_counts","window_counts","consent_at","messages_from","cursor","last_analysis_status"]);element("learning-run-diagnostics").focus();});
  async function safe(action){error.textContent="";try{await action();}catch(failure){text(error,failure.message);}}
  root.querySelectorAll("[data-tab]").forEach(node=>node.onclick=()=>safe(async()=>{show(node.dataset.tab);if(node.dataset.tab==="suggestions")await suggestions();if(node.dataset.tab==="history")await history();if(node.dataset.tab==="sources")await sources();}));
  root.querySelectorAll("[data-action]").forEach(button=>button.onclick=()=>safe(async()=>{button.disabled=true;try{const body=button.dataset.action==="edit"?{content:element("learning-detail-content").value,title:element("learning-edit-title").value}:button.dataset.action==="approve"?{verified:element("learning-verified").checked}:{};await api(`/candidates/${selected.id}/${button.dataset.action}`,"POST",body);await detail(selected.id);await refresh();await suggestions();}finally{button.disabled=false;}}));
  element("learning-restore").onclick=()=>safe(async()=>{await api(`/candidates/${selected.id}/rollback`,"POST",{version_id:Number(element("learning-restore-version").value)});await detail(selected.id);await refresh();});
  element("learning-detail-close").onclick=()=>{element("learning-detail").hidden=true;};
  element("learning-run").onclick=()=>safe(async()=>{const data=await api("/run","POST",{});text(element("learning-notice"),`Queued job ${data.job_id}. The existing cron checks approximately every 15 minutes; this request does not analyze conversations.`);await history();watchJob(data.job_id);});
  element("learning-settings-form").onsubmit=event=>{event.preventDefault();safe(async()=>{const body={};Object.entries(dashboard.settings).forEach(([name,value])=>{const input=element("learning-settings-form").elements.namedItem(name);body[name]=typeof value==="boolean"?input.checked:typeof value==="number"?Number(input.value):Array.isArray(value)?input.value.split(",").map(x=>x.trim()).filter(Boolean):input.value;});await api("/settings","PATCH",body);await refresh();});};
  element("learning-source-form").onsubmit=event=>{event.preventDefault();safe(async()=>{const fields=event.target.elements;await api("/sources","POST",{conversation_id:Number(fields.namedItem("conversation_id").value),allowed:fields.namedItem("allowed").checked,erase:fields.namedItem("erase").checked,legal_hold:fields.namedItem("legal_hold").checked,evaluation_only:fields.namedItem("evaluation_only").checked,consent_reference:fields.namedItem("consent_reference").value,...(fields.namedItem("messages_from").value?{messages_from:fields.namedItem("messages_from").value+"+05:30"}:{})});await sources();});};
  element("learning-case-form").onsubmit=event=>{event.preventDefault();safe(async()=>{const fields=event.target.elements;await api("/regression-cases","POST",{source_id:Number(fields.namedItem("source_id").value),message_ids:fields.namedItem("message_ids").value.split(",").map(x=>Number(x.trim())),expected_behavior:fields.namedItem("expected_behavior").value,language:fields.namedItem("language").value});await sources();});};
  element("learning-feedback-form").onsubmit=event=>{event.preventDefault();safe(async()=>{const fields=event.target.elements;await api("/feedback","POST",{conversation_id:Number(fields.namedItem("conversation_id").value),version_id:Number(fields.namedItem("version_id").value)||null,kind:fields.namedItem("kind").value});await refresh();});};
  ["learning-search","learning-product-category"].forEach(id=>element(id).oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{offset=0;safe(suggestions);},200);});
  ["learning-status","learning-language","learning-category","learning-from","learning-to","learning-conflicts"].forEach(id=>element(id).onchange=()=>{offset=0;safe(suggestions);});
  element("learning-prev").onclick=()=>{offset=Math.max(0,offset-50);safe(suggestions);};element("learning-next").onclick=()=>{offset+=50;safe(suggestions);};
  element("learning-pause").onclick=()=>safe(async()=>{await api("/emergency-pause","POST",{});await refresh();});
  element("learning-rollback-all").onclick=()=>safe(async()=>{if(confirm("Disable all learned knowledge? Administrator bot policies remain unchanged.")){await api("/emergency-pause","POST",{rollback_all:true});await refresh();}});
  safe(async()=>{await refresh();show("dashboard");});
})();
