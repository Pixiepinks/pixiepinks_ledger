(() => {
  "use strict";
  const el=id=>document.getElementById(id), base="/crm/leads/api/whatsapp", dialog=el("wa-lead-dialog");
  let offset=0, total=0, csrf="", statuses=[], statusLabels={}, assignees=[], generation=0, selected=null, detail=null, refOffset=0, searchTimer, controller;
  const write=(node,value)=>{node.textContent=value==null||value===""?"—":String(value);};
  const statusLabel=value=>statusLabels[value]||(value?value.replaceAll("_"," "):"Status not set");
  const date=value=>value?new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Colombo",dateStyle:"medium",timeStyle:"short"}).format(new Date(value)):"—";
  async function api(path,method="GET",body=null,key=null,signal=null){
    const response=await fetch(base+path,{method,credentials:"same-origin",cache:"no-store",signal,headers:method==="GET"?{}:{"Content-Type":"application/json","X-CSRF-Token":csrf,"Idempotency-Key":key||crypto.randomUUID()},...(body?{body:JSON.stringify(body)}:{})});
    if(response.redirected)throw new Error("Sign in again to access WhatsApp leads.");
    const data=await response.json();if(!response.ok)throw new Error(data.detail||"CRM request failed.");return data;
  }
  function safeLink(value){try{const url=new URL(value);return url.protocol==="https:"&&!url.username&&!url.password&&(!url.port||url.port==="443")&&["facebook.com","instagram.com","fb.me","fb.watch"].some(host=>url.hostname===host||url.hostname.endsWith("."+host))?url.href:null;}catch(_){return null;}}
  function referralCard(referral,history=false){
    const card=document.createElement("article"),body=document.createElement("div"),heading=document.createElement("strong"),meta=document.createElement("small");card.className="wa-lead-referral"+(history?"":" wa-lead-referral-compact");
    write(heading,referral.headline||({ad:"Advertisement referral",post:"Post referral"})[referral.source_type]||"Meta referral");
    write(meta,`${referral.source_type==="ad"?"Ad":referral.source_type==="post"?"Post":"Referral"}${referral.source_id?` ID: ${referral.source_id}`:" · ID unavailable"}`);if(history)body.append(heading);body.append(meta);
    if(history&&referral.body){const description=document.createElement("p");write(description,referral.body);body.append(description);}
    if(referral.preview_url&&/^\/crm\/whatsapp\/api\/conversations\/\d+\/messages\/\d+\/referrals\/\d+\/preview\?v=[a-f0-9]+$/.test(referral.preview_url)){
      const image=new Image();image.src=referral.preview_url;image.alt=referral.preview_kind==="video_thumbnail"?"Advertisement video thumbnail":"Referral creative preview";image.loading="lazy";image.referrerPolicy="no-referrer";
      image.onerror=()=>{image.remove();const unavailable=document.createElement(history?"small":"span");if(history)write(unavailable,"Preview unavailable");else{unavailable.className="wa-lead-preview-unavailable";unavailable.setAttribute("role","img");unavailable.setAttribute("aria-label","Preview unavailable");unavailable.title="Preview unavailable";unavailable.textContent="—";}if(history)body.append(unavailable);else card.prepend(unavailable);};card.append(image);
    }
    const source=history?safeLink(referral.source_url):null;if(source){const link=document.createElement("a");link.href=source;link.target="_blank";link.rel="noopener noreferrer";write(link,"View source ↗");body.append(link);}
    if(history){const time=document.createElement("small");write(time,`Captured ${date(referral.captured_at)} · Message ${referral.message_id}`);body.append(time);if(referral.customer_message){const message=document.createElement("p");write(message,`Customer: ${referral.customer_message}`);body.append(message);}}
    card.append(body);return card;
  }
  function tab(whatsapp){el("wa-leads-pane").hidden=!whatsapp;el("saved-leads-pane").hidden=whatsapp;el("wa-leads-tab").setAttribute("aria-pressed",String(whatsapp));el("saved-leads-tab").setAttribute("aria-pressed",String(!whatsapp));}
  el("wa-leads-tab").onclick=()=>tab(true);el("saved-leads-tab").onclick=()=>{if(el("saved-leads-pane").hidden)location.assign("/crm/leads?view=saved");};
  el("showLeadFormBtn").addEventListener("click",()=>tab(false));
  // Preserve the selected saved-lead date/search view across normal GET submits.
  if(new URLSearchParams(location.search).has("range_type")||new URLSearchParams(location.search).has("query")||new URLSearchParams(location.search).get("view")==="saved")tab(false);
  async function load(){
    const token=++generation;el("wa-leads-error").textContent="";el("wa-leads-retry").hidden=true;write(el("wa-leads-result"),"Loading WhatsApp leads…");
    try{
      const query=new URLSearchParams({q:el("wa-leads-search").value,source:el("wa-leads-source").value,status:el("wa-leads-status").value,assigned:el("wa-leads-assigned").value,offset:String(offset)});
      const data=await api("?"+query);if(token!==generation)return;total=data.total;csrf=data.csrf_token;if(offset>=total&&offset>0){offset=total?Math.floor((total-1)/50)*50:0;return load();}
      if(!statuses.length){statuses=data.statuses;statusLabels=data.status_labels||{};assignees=data.assignees;for(const status of statuses)el("wa-leads-status").add(new Option(statusLabel(status),status));for(const name of assignees)el("wa-leads-assigned").add(new Option(name,name));}
      el("wa-leads-rows").replaceChildren(...data.items.map(item=>{
        const row=document.createElement("tr"),customer=document.createElement("td"),name=document.createElement("strong"),phone=document.createElement("small"),mode=document.createElement("small");row.dataset.conversationId=String(item.conversation_id);write(name,item.customer_name);write(phone,item.phone_number);write(mode,`${item.mode}${item.lead?` · ${item.lead.lead_no}`:""}`);customer.append(name,phone,mode);row.append(customer);
        const activity=document.createElement("td");write(activity,date(item.last_activity));row.append(activity);
        const source=document.createElement("td");if(item.latest_referral){source.append(referralCard(item.latest_referral));}else write(source,"WhatsApp · No referral received");row.append(source);
        for(const value of [item.lead?.product_interest,item.lead?(statusLabel(item.lead.status)):"Not organized",item.lead?.assigned_to,item.lead?.next_followup]){const cell=document.createElement("td");write(cell,value);row.append(cell);}
        const actions=document.createElement("td"),wrap=document.createElement("div"),inbox=document.createElement("a"),manage=document.createElement("button");wrap.className="wa-lead-actions";inbox.href=item.inbox_url;write(inbox,"Open Inbox");manage.type="button";write(manage,item.lead?"Manage / referrals":"Organize / referrals");manage.onclick=()=>open(item.conversation_id);wrap.append(inbox,manage);actions.append(wrap);row.append(actions);return row;
      }));
      write(el("wa-leads-result"),total?`${offset+1}–${Math.min(offset+50,total)} of ${total} WhatsApp conversations` :"No WhatsApp conversations match these filters.");el("wa-leads-prev").disabled=offset===0;el("wa-leads-next").disabled=offset+50>=total;
    }catch(error){if(token!==generation)return;el("wa-leads-rows").replaceChildren();write(el("wa-leads-error"),error.message);write(el("wa-leads-result"),"WhatsApp leads could not be loaded.");el("wa-leads-retry").hidden=false;}
  }
  let detailGeneration=0, actionAttempt=null;
  async function open(cid){selected=cid;refOffset=0;actionAttempt=null;if(!dialog.open)dialog.showModal();await detailLoad();}
  async function detailLoad(){
    controller?.abort();controller=new AbortController();const token=++detailGeneration;
    el("wa-lead-dialog-error").textContent="";el("wa-lead-form").hidden=true;el("wa-lead-fields").disabled=true;write(el("wa-lead-summary"),"Loading conversation…");el("wa-lead-link").hidden=true;el("wa-lead-referrals").replaceChildren();write(el("wa-lead-dialog-status"),"Loading lead and referral history…");
    try{
      const data=await api("/"+selected,"GET",null,null,controller.signal);if(token!==detailGeneration||!dialog.open)return;detail=data;
      write(el("wa-lead-summary"),`${data.customer_name} · ${data.phone_number} · Conversation ${data.conversation_id}`);
      el("wa-lead-edit-status").replaceChildren(...statuses.map(value=>new Option(statusLabel(value),value)));
      el("wa-lead-edit-assigned").replaceChildren(new Option("Unassigned",""),...assignees.map(name=>new Option(name,name)));
      if(data.lead){el("wa-lead-form").hidden=false;el("wa-lead-fields").disabled=false;el("wa-lead-edit-status").value=data.lead.status;el("wa-lead-edit-assigned").value=data.lead.assigned_to||"";if(data.lead.assigned_to&&!assignees.includes(data.lead.assigned_to)){el("wa-lead-edit-assigned").add(new Option(data.lead.assigned_to,data.lead.assigned_to));el("wa-lead-edit-assigned").value=data.lead.assigned_to;}el("wa-lead-product").value=data.lead.product_interest||"";el("wa-lead-followup").value=data.lead.next_followup||"";el("wa-lead-note").value="";el("wa-lead-view").href=data.lead.url;el("wa-lead-view").hidden=false;}
      else{el("wa-lead-link").hidden=false;el("wa-lead-match").replaceChildren(new Option("Choose existing CRM lead",""),...data.matching_leads.map(item=>new Option(`${item.lead_no} · ${item.customer_name}`,item.id)));el("wa-lead-match").disabled=!data.matching_leads.length;write(el("wa-lead-create"),data.matching_leads.length?"Link selected CRM lead":"Create CRM record");write(el("wa-lead-link-help"),data.matching_leads.length?"Existing records match this number. Choose one; its saved status and follow-up will be preserved.":"This creates one CRM record. It does not send a message or change AI/HUMAN mode.");el("wa-lead-view").hidden=true;}
      el("wa-lead-referrals").replaceChildren(...data.referrals.map(ref=>referralCard(ref,true)));if(!data.referrals.length)write(el("wa-lead-referrals"),"No referral received. Advertisement origin cannot be confirmed.");refOffset=data.referrals.length;el("wa-lead-more-referrals").hidden=refOffset>=data.referral_total;write(el("wa-lead-dialog-status"),`${data.referral_total} stored referrals. Previews may be unavailable or expired.`);
    }catch(error){if(error.name!=="AbortError"&&token===detailGeneration)write(el("wa-lead-dialog-error"),"Could not load lead details. Close and retry.");}
  }
  async function save(body){
    const target=selected,fingerprint=JSON.stringify(body);if(!actionAttempt||actionAttempt.cid!==target||actionAttempt.body!==fingerprint)actionAttempt={cid:target,body:fingerprint,key:crypto.randomUUID()};const attempt=actionAttempt;el("wa-lead-dialog-error").textContent="";
    try{await api(`/${target}/lead`,"POST",body,attempt.key);if(actionAttempt===attempt)actionAttempt=null;if(target===selected&&dialog.open)await detailLoad();await load();}
    catch(error){if(target===selected&&dialog.open)write(el("wa-lead-dialog-error"),error.message);}
  }
  el("wa-lead-create").onclick=async event=>{if(detail.matching_leads.length&&!el("wa-lead-match").value){write(el("wa-lead-dialog-error"),"Choose the existing CRM record first.");return;}event.currentTarget.disabled=true;try{await save(el("wa-lead-match").value?{lead_id:Number(el("wa-lead-match").value)}:{});}finally{el("wa-lead-create").disabled=false;}};
  el("wa-lead-form").onsubmit=async event=>{event.preventDefault();el("wa-lead-save").disabled=true;try{await save({status:el("wa-lead-edit-status").value,assigned_to:el("wa-lead-edit-assigned").value,product_interest:el("wa-lead-product").value,next_followup:el("wa-lead-followup").value,note:el("wa-lead-note").value});}finally{el("wa-lead-save").disabled=false;}};
  el("wa-lead-more-referrals").onclick=async()=>{const target=selected;try{const data=await api(`/${target}?offset=${refOffset}`);if(target!==selected||!dialog.open)return;el("wa-lead-referrals").append(...data.referrals.map(ref=>referralCard(ref,true)));refOffset+=data.referrals.length;el("wa-lead-more-referrals").hidden=refOffset>=data.referral_total;}catch(_){write(el("wa-lead-dialog-error"),"Could not load more referrals. Try again.");}};
  el("wa-lead-close").onclick=()=>dialog.close();dialog.addEventListener("close",()=>{const target=selected;++detailGeneration;controller?.abort();detail=null;selected=null;(document.querySelector(`[data-conversation-id="${target}"] button`)||el("wa-leads-search")).focus();});
  dialog.addEventListener("keydown",event=>{if(event.key!=="Tab")return;const nodes=[...dialog.querySelectorAll("button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href]")].filter(node=>!node.hidden&&!node.closest("fieldset[disabled]")&&node.getClientRects().length);const first=nodes[0],last=nodes[nodes.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}});
  el("wa-leads-search").oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{offset=0;load();},300);};
  for(const id of ["wa-leads-source","wa-leads-status","wa-leads-assigned"])el(id).onchange=()=>{offset=0;load();};
  el("wa-leads-prev").onclick=()=>{offset=Math.max(0,offset-50);load();};el("wa-leads-next").onclick=()=>{offset+=50;load();};el("wa-leads-retry").onclick=load;
  load();
})();
