(() => {
  'use strict';
  const inbox=document.getElementById('inbox'), dashboard=document.getElementById('marketing-dashboard'), dialog=document.getElementById('marketing-consent-dialog');
  if(!dialog)return;
  const content=document.getElementById('marketing-consent-content'),feedback=document.getElementById('marketing-consent-feedback');
  const csrf=dashboard?.dataset.csrf || inbox?.dataset.assignmentCsrf;
  let generation=0,current=null,offset=0,tableGeneration=0,widgetGeneration=0,widgetCid=null,widgetLoading=false;
  const node=(tag,text)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;return el;};
  const readable=value=>(value||'—').replaceAll('_',' ');
  const date=value=>value?new Date(value).toLocaleString('en-GB',{timeZone:'Asia/Colombo'}):'—';
  const badge=status=>{const el=node('span',readable(status));el.className='marketing-status';el.dataset.status=status;return el;};
  async function api(path,body,headers={}){
    const response=await fetch(path,{credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...headers},...(body?{method:'POST',body:JSON.stringify(body)}:{})});
    let data={};try{data=await response.json();}catch(_){}
    if(!response.ok||response.redirected)throw new Error(typeof data.detail==='string'?data.detail:data.error||'Consent service is unavailable. Refresh before retrying.');return data;
  }
  function button(text,fn){const el=node('button',text);el.type='button';el.className='btn btn-secondary';el.onclick=fn;return el;}
  function field(form,label,type='text',value=''){
    const wrap=node('label',label),el=node(type==='textarea'?'textarea':'input');if(type!=='textarea')el.type=type;el.value=value;wrap.append(el);form.append(wrap);return el;
  }
  function select(form,label,options,value=null){
    const wrap=node('label',label),el=node('select');el.replaceChildren(...options.map(o=>new Option(readable(o[1]||o[0]),o[0])));if(value!==null)el.value=value;wrap.append(el);form.append(wrap);return el;
  }
  const endpoint=id=>`${dashboard?'/crm/marketing-consent/api':'/crm/whatsapp/api/marketing-consent'}/${id}`;
  async function act(row,data){
    const token=generation;
    try{feedback.textContent='Saving…';await api(endpoint(row.id)+'/action',{...data,version:row.version,key:crypto.randomUUID()});
      if(token!==generation)return;feedback.textContent='Consent change saved. No message was sent.';await loadCurrent();if(inbox)loadWidget();else loadTable();
    }catch(error){if(token===generation)feedback.textContent=error.message;}
  }
  function render(data){
    const row=data.contact;content.replaceChildren();content.append(node('h3',row.name),badge(row.status),
      node('p',`${row.phone||'No verified WhatsApp identity'} · Source: ${readable(row.source)}`),
      node('p',`Consent date: ${date(row.consent_at)} · Updated: ${date(row.updated_at)}`),
      node('p',`Eligible: ${row.eligible?'Yes':'No'}${row.needs_review?' · Staff review required':''}${row.identity_conflict?' · Identity conflict':''}`));
    if(row.latest_request)content.append(node('p',`Consent request submission: ${row.latest_request.status} · Delivery: ${row.latest_request.delivery_status||'Not reported'}`));
    if(row.opted_out_at)content.append(node('p',`Last opt-out: ${date(row.opted_out_at)} · ${readable(row.opt_out_source)}`));
    const interests=node('form');const tags=select(interests,'Product interests (not consent)',data.interests.map(t=>[t,t]));tags.multiple=true;tags.size=4;
    [...tags.options].forEach(o=>o.selected=row.interests.includes(o.value));const save=node('button','Save interests');save.className='btn btn-secondary';interests.append(save);
    interests.onsubmit=e=>{e.preventDefault();act(row,{action:'interests',interests:[...tags.selectedOptions].map(o=>o.value)});};content.append(interests);
    const out=node('form'),reason=field(out,'Customer withdrawal evidence / reason');reason.maxLength=500;reason.required=true;
    const outButton=node('button','Mark opted out');outButton.className='btn btn-secondary';out.append(outButton);
    out.onsubmit=e=>{e.preventDefault();act(row,{action:'opt_out',reason:reason.value});};content.append(out);
    content.append(button('View history',()=>history(row.id)));
    if(data.can_verify){
      const form=node('form');form.append(node('h4','Administrator: verified consent evidence'));
      const action=select(form,'Action',[['verify','Verify consent'],['correct','Correct evidence (append history)'],['invalidate','Invalidate consent'],['reconcile_identity','Reconcile canonical identity (never grants consent)']]);
      const source=select(form,'Source',[['STAFF_VERIFIED_EXTERNAL'],['WEBSITE_FORM'],['FACEBOOK_LEAD_FORM'],['CUSTOMER_REQUEST'],['IMPORT_WITH_EVIDENCE']]);
      const description=field(form,'Source description');description.maxLength=1000;
      const when=field(form,'When the customer agreed (your local time)','datetime-local');
      const wording=field(form,'Exact consent wording identifying PixiePinks and WhatsApp promotions','textarea');wording.maxLength=4000;
      const version=field(form,'Wording version');version.maxLength=100;
      const evidence=field(form,'Evidence reference (secure internal reference or HTTPS URL)');evidence.maxLength=500;
      const evidenceType=select(form,'Evidence type',[['EXTERNAL_REFERENCE'],['CRM_MESSAGE']]);
      const affirmative=field(form,'I verified affirmative permission for PixiePinks WhatsApp promotions','checkbox');
      const note=field(form,'Administrative reason');note.maxLength=500;note.required=true;
      const submit=node('button','Record / correct consent');submit.className='btn btn-secondary';form.append(submit);
      form.onsubmit=e=>{e.preventDefault();if(!['invalidate','reconcile_identity'].includes(action.value)&&!when.value){feedback.textContent='Evidence timestamp is required.';return;}
        act(row,{action:action.value,source:source.value,scope:'PIXIEPINKS_WHATSAPP_PROMOTIONS',source_description:description.value,
          evidence_at:when.value?new Date(when.value).toISOString():null,wording:wording.value,wording_version:version.value,
          evidence_type:evidenceType.value,evidence_reference:evidence.value,affirmative:affirmative.checked,reason:note.value});};content.append(form);
    }
    if(row.conversation_id){
      const form=node('form'),language=select(form,'Consent request language',[['en','English'],['si','Sinhala']]);
      const preview=node('button','Request consent — preview');preview.className='btn btn-secondary';preview.disabled=!data.can_request;
      form.append(preview,node('p',data.request_explanation||'Requires an open 24-hour service window and explicit staff confirmation.'));
      const previewArea=node('div');form.append(previewArea);
      form.onsubmit=async e=>{
        e.preventDefault();const token=generation;preview.disabled=true;
        const slot=`marketing-preview:${row.id}:${language.value}`;let key;
        try{key=sessionStorage.getItem(slot)||crypto.randomUUID();sessionStorage.setItem(slot,key);}catch(_){key=crypto.randomUUID();}
        try{
          const result=await api(`/crm/whatsapp/api/conversations/${row.conversation_id}/marketing-consent/request`,{language:language.value,key});
          if(token!==generation)return;previewArea.replaceChildren(node('pre',result.text));
          if(result.status!=='PREPARED'){if(['CANCELLED','INVALIDATED','EXPIRED'].includes(result.status)){try{sessionStorage.removeItem(slot);}catch(_){}}previewArea.append(node('p',`Existing request: ${result.status}. Check the conversation; no automatic resend.`));return;}
          const confirmWrap=node('label','I reviewed this exact request for this customer.'),confirm=node('input');confirm.type='checkbox';confirmWrap.append(confirm);previewArea.append(confirmWrap);
          const send=button('Send reviewed consent request',async()=>{
            if(!confirm.checked){feedback.textContent='Confirm the preview before sending.';return;}
            send.disabled=true;
            try{
              await api(`/crm/whatsapp/api/conversations/${row.conversation_id}/send`,{message:result.text,marketing_request_id:result.request_id},{'Idempotency-Key':result.key});
              if(token!==generation)return;feedback.textContent='Request accepted for sending. Pending is not marketing consent.';
              try{sessionStorage.removeItem(slot);}catch(_){}await loadCurrent();if(inbox)loadWidget();else loadTable();
            }catch(error){if(token===generation)feedback.textContent=error.message+' Check the existing request; do not resend automatically.';}
          });
          previewArea.append(send);
        }catch(error){if(token===generation){feedback.textContent=error.message;try{sessionStorage.removeItem(slot);}catch(_){}}}
        finally{if(token===generation)preview.disabled=!data.can_request;}
      };content.append(form);
    }
  }
  async function loadCurrent(){
    if(!current)return;const token=generation;content.setAttribute('aria-busy','true');
    try{
      let data=await api(endpoint(current.id));
      if(data.contact.conversation_id){
        try{const cap=await api(`/crm/whatsapp/api/conversations/${data.contact.conversation_id}/marketing-consent`);data={...data,...cap};}catch(_){data.can_request=false;data.request_explanation='Inbox access or a current conversation is required.';}
      }
      if(token===generation && dialog.open)render(data);
    }catch(error){if(token===generation)feedback.textContent=error.message;}
    finally{if(token===generation)content.setAttribute('aria-busy','false');}
  }
  async function open(id){generation++;current={id};feedback.textContent='Loading consent…';content.replaceChildren();if(!dialog.open)dialog.showModal();await loadCurrent();if(dialog.open && content.children.length)feedback.textContent='';}
  document.getElementById('marketing-consent-close').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{generation++;current=null;});
  async function history(id,before=null,container=null){
    const token=generation;
    try{
      const data=await api(endpoint(id)+'/history'+(before?'?before_id='+before:''));if(token!==generation)return;
      const target=container||node('section');if(!container){target.append(node('h4','Append-only consent history'));content.append(target);}
      if(!data.events.length && !before)target.append(node('p','No consent events recorded.'));
      for(const ev of data.events)target.append(node('pre',`${date(ev.at)} · ${ev.actor}\n${readable(ev.type)}: ${readable(ev.previous)} → ${readable(ev.status)}${ev.reason?'\n'+ev.reason:''}${ev.evidence_reference?'\nEvidence: '+ev.evidence_reference:''}${ev.wording?'\nWording: '+ev.wording:''}${ev.evidence_text?'\nCustomer response: '+ev.evidence_text:''}`));
      if(data.next_before){const more=button('Older history',()=>{more.remove();history(id,data.next_before,target);});target.append(more);}
    }catch(error){if(token===generation)feedback.textContent=error.message;}
  }
  async function loadWidget(){
    if(!inbox || !widgetCid || widgetLoading)return;const cid=widgetCid,token=widgetGeneration;widgetLoading=true;
    try{
      const data=await api(`/crm/whatsapp/api/conversations/${cid}/marketing-consent`);if(token!==widgetGeneration)return;
      const box=document.getElementById('marketing-consent-summary');box.replaceChildren(badge(data.contact.status),node('p',`Interested in: ${data.contact.interests.join(', ')||'—'}`),node('p',`Source: ${readable(data.contact.source)} · Consent: ${date(data.contact.consent_at)}`),node('p',`Updated: ${date(data.contact.updated_at)}`));
      document.getElementById('marketing-consent-panel').hidden=false;document.getElementById('marketing-consent-manage').onclick=()=>open(data.contact.id);
    }catch(_){if(token===widgetGeneration){document.getElementById('marketing-consent-panel').hidden=false;document.getElementById('marketing-consent-summary').textContent='Consent unavailable. Refresh to retry.';}}
    finally{if(token===widgetGeneration)widgetLoading=false;}
  }
  if(inbox){
    inbox.addEventListener('inbox-conversation',e=>{widgetCid=e.detail.id;widgetGeneration++;widgetLoading=false;document.getElementById('marketing-consent-panel').hidden=true;dialog.close();});
    inbox.addEventListener('inbox-conversation-data',e=>{widgetCid=e.detail.id;loadWidget();});
    document.getElementById('marketing-consent-from-details').onclick=async()=>{
      document.getElementById('conversation-details-dialog').close();
      try{const cid=Number(inbox.dataset.activeConversation),data=await api(`/crm/whatsapp/api/conversations/${cid}/marketing-consent`);if(Number(inbox.dataset.activeConversation)===cid)open(data.contact.id);}catch(error){feedback.textContent=error.message;dialog.showModal();}
    };
    widgetCid=Number(inbox.dataset.activeConversation)||null;if(widgetCid)loadWidget();
  }
  async function loadTable(){
    if(!dashboard)return;const token=++tableGeneration,status=document.getElementById('marketing-dashboard-status');status.textContent='Loading consent contacts…';
    try{
      const query=new URLSearchParams(new FormData(document.getElementById('marketing-search')));query.set('offset',offset);
      const data=await api('/crm/marketing-consent/api?'+query);if(token!==tableGeneration)return;
      const kpi=document.getElementById('marketing-kpis'),kpiNames={total:'Visible CRM contacts',eligible:'Eligible marketing contacts'};kpi.replaceChildren(...Object.entries(data.kpis).map(([key,value])=>node('span',`${kpiNames[key]||readable(key)}: ${value}`)));
      document.getElementById('marketing-rows').replaceChildren(...data.contacts.map(row=>{
        const tr=node('tr');[row.name,row.phone||'—',row.status,readable(row.source),date(row.consent_at),row.interests.join(', '),date(row.updated_at)].forEach((value,index)=>{const cell=node('td',index===2?null:value);if(index===2)cell.append(badge(row.status));tr.append(cell);});
        const cell=node('td');cell.append(button('View details',()=>open(row.id)));tr.append(cell);return tr;
      }));status.textContent=data.contacts.length?`${data.total} matching contacts`:'No matching consent contacts.';
      document.getElementById('marketing-prev').disabled=offset===0;document.getElementById('marketing-next').disabled=offset+50>=data.total;
    }catch(error){if(token===tableGeneration)status.textContent=error.message;}
  }
  if(dashboard){document.getElementById('marketing-search').onsubmit=e=>{e.preventDefault();offset=0;loadTable();};document.getElementById('marketing-prev').onclick=()=>{offset=Math.max(0,offset-50);loadTable();};document.getElementById('marketing-next').onclick=()=>{offset+=50;loadTable();};loadTable();}
})();
