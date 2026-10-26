(() => {
  "use strict";
  const root=document.getElementById("inbox"),select=document.getElementById("inbox-lead-status-select"),save=document.getElementById("inbox-lead-status-save");
  const link=document.getElementById("inbox-lead-link"),linkLabel=document.getElementById("inbox-lead-link-label"),feedback=document.getElementById("inbox-lead-status-feedback"),help=document.getElementById("inbox-lead-status-help"),retry=document.getElementById("inbox-lead-status-retry");
  const base="/crm/leads/api/whatsapp";
  let active=null,generation=0,controller,attempt=null,state={loaded:false,saving:false,dirty:false,lead:null,matches:[],labels:{}};
  const label=value=>state.labels[value]||(value?value.replaceAll("_"," "):"Not organized");
  function report(message,error=false){feedback.textContent=message;feedback.dataset.error=String(error);}
  async function api(path,options={}){
    const response=await fetch(base+path,{credentials:"same-origin",cache:"no-store",...options});
    if(response.redirected)throw new Error("Sign in again to update lead status.");
    const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:"Lead status unavailable.");return data;
  }
  function controls(){
    select.disabled=!state.loaded||state.saving;link.disabled=!state.loaded||state.saving;
    linkLabel.hidden=Boolean(state.lead)||!state.matches.length;
    const selectedLink=!state.lead&&state.matches.length;
    save.disabled=!state.loaded||state.saving||!select.value||(!state.dirty&&Boolean(state.lead))||(selectedLink&&!link.value);
    save.textContent=state.saving?"Saving…":"Save status";
    help.hidden=Boolean(state.lead)||!state.loaded;
    help.textContent=selectedLink?"Choose the existing CRM lead to link before saving its status.":"Saving creates a CRM lead record; no WhatsApp message is sent.";
  }
  function current(lead){state.lead=lead;select.value=lead?.status||"";state.dirty=false;controls();}
  async function load(id){
    controller?.abort();controller=new AbortController();const request=controller,token=++generation;active=id;attempt=null;
    state={loaded:false,saving:false,dirty:false,lead:null,matches:[],labels:{}};select.replaceChildren(new Option("Loading…",""));link.replaceChildren();retry.hidden=true;controls();report("Loading lead status…");
    const timeout=setTimeout(()=>request.abort(),15000);
    try{
      const data=await api(`/${id}/status`,{signal:request.signal});if(token!==generation||active!==id)return;
      if(data.conversation_id!==id)throw new Error("Conversation mismatch.");
      state={loaded:true,saving:false,dirty:false,lead:data.lead,matches:data.matching_leads,labels:data.status_labels,csrf:data.csrf_token};
      select.replaceChildren(new Option("Choose lead status…",""),...data.statuses.map(value=>new Option(label(value),value)));
      if(data.lead?.status&&!data.statuses.includes(data.lead.status))select.add(new Option(label(data.lead.status),data.lead.status));
      link.replaceChildren(new Option("Choose existing CRM lead…",""),...state.matches.map(item=>new Option(`${item.lead_no} · ${item.customer_name}`,item.id)));
      current(data.lead);report("");
    }catch(error){if(token===generation){state.loaded=false;controls();report("Could not load lead status. Check your access or connection and retry.",true);retry.hidden=false;}}
    finally{clearTimeout(timeout);}
  }
  select.onchange=()=>{state.dirty=select.value!==(state.lead?.status||"");controls();report("");};link.onchange=()=>{controls();report("");};
  document.getElementById("inbox-lead-status-form").onsubmit=async event=>{
    event.preventDefault();if(save.disabled||!state.loaded)return;
    const target=active,token=generation,body={status:select.value};if(!state.lead&&link.value)body.lead_id=Number(link.value);
    const fingerprint=JSON.stringify(body);if(!attempt||attempt.id!==target||attempt.body!==fingerprint)attempt={id:target,body:fingerprint,key:crypto.randomUUID()};
    const requestAttempt=attempt,csrf=state.csrf;state.saving=true;controls();report("Saving lead status…");
    try{
      const data=await api(`/${target}/lead`,{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf,"Idempotency-Key":requestAttempt.key},body:JSON.stringify(body)});
      if(attempt===requestAttempt)attempt=null;
      if(token===generation&&target===active){state.saving=false;current(data.lead);report(`Saved: ${label(data.lead.status)}`);}
      root.dispatchEvent(new CustomEvent("inbox-lead-status-updated",{detail:{id:target,lead:data.lead,editorGeneration:token}}));
    }catch(error){if(token===generation&&target===active)report(error.message,true);}
    finally{if(token===generation&&target===active){state.saving=false;controls();}}
  };
  retry.onclick=()=>{if(active)load(active);};
  root.addEventListener("inbox-conversation",event=>load(event.detail.id));
  root.addEventListener("inbox-conversation-data",event=>{
    if(event.detail.id!==active||!state.loaded||state.saving||state.dirty)return;
    const data=event.detail.conversation;current(data.lead_id?{id:data.lead_id,status:data.lead_status}:null);
  });
  root.addEventListener("inbox-lead-status-updated",event=>{
    if(event.detail.id===active&&event.detail.editorGeneration!==generation&&!state.dirty&&!state.saving)load(active);
  });
  const initial=Number(root.dataset.activeConversation);if(Number.isSafeInteger(initial)&&initial>0)load(initial);
})();
