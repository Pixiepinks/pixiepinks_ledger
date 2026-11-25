/* Read-only conversation projection. One current-conversation cache, no persistent PII. */
(()=>{'use strict';
const root=document.getElementById('inbox'),panel=document.getElementById('inbox-customer-panel');if(!root||!panel)return;
const notice=document.getElementById('customer-panel-notice'),fields=document.getElementById('customer-panel-fields'),retry=document.getElementById('customer-panel-retry');
const TTL=12000;let cid=null,generation=0,lastAttempt=null,flight=null,controller=null,dirty=false,lastValue=null,signature='';
const setText=(el,value)=>{value=String(value??'');if(el.textContent!==value)el.textContent=value;};
const money=value=>{const raw=String(value??'');if(!/^-?\d+(?:\.\d{1,2})?$/.test(raw))return '—';const [whole,fraction='']=raw.split('.');return 'Rs. '+whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')+'.'+fraction.padEnd(2,'0');};
const localTime=value=>{if(!value)return '—';try{return new Intl.DateTimeFormat('en-GB',{day:'2-digit',month:'short',year:'numeric',hour:'numeric',minute:'2-digit',hour12:true,timeZone:'Asia/Colombo'}).format(new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(value)?value:value+'Z')).replace(/\b(am|pm)\b/g,v=>v.toUpperCase());}catch(_){return '—';}};
const labels={UNREGISTERED:'Not registered. Register when creating an order.',REGISTERED:'Registered customer. Membership pending first qualifying purchase.',ACTIVE:'Active loyalty member.',PENDING:'Registered customer. No current qualifying purchase.',SUSPENDED:'Membership suspended.',INELIGIBLE:'Membership ineligible — identity review required.',AMBIGUOUS:'Multiple customer matches — identity review required.',SUGGESTED:'Suggested match — confirm when creating an order.'};
function render(value){
 panel.dataset.stale='false';setText(notice,value.state==='REGISTERED'&&value.membership_status==='PENDING_ACTIVATION'?'Registered customer. Membership activation pending.':labels[value.state]);retry.hidden=true;
 const next=JSON.stringify(value);if(next===signature)return;signature=next;lastValue=value;
 const rows=[];
 if(value.customer_id){
  rows.push(['customer','Customer',value.name+' · ID '+value.customer_id],['mobile','Registered mobile',value.mobile||'Unavailable']);
  if(value.membership_id)rows.push(['membership','Membership',value.membership_id]);
  if(value.state==='SUGGESTED')rows.push(['status','Membership status',value.membership_status==='PENDING_FIRST_PURCHASE'?'Pending first qualifying purchase':value.membership_status==='PENDING_ACTIVATION'?'Membership activation pending':value.membership_status.replaceAll('_',' ')]);
  rows.push(['purchases','Qualifying purchases',value.qualifying_count]);
  if(Object.hasOwn(value,'lifetime_net'))rows.push(['lifetime','Lifetime purchases',money(value.lifetime_net)]);
  if(Object.hasOwn(value,'lifetime_savings'))rows.push(['savings','Loyalty savings',money(value.lifetime_savings)]);
  if(value.activated_at)rows.push(['activated','Member since',localTime(value.activated_at)]);
 }
 const wanted=new Set(rows.map(row=>row[0]));
 for(const child of [...fields.children])if(child.dataset.field&&!wanted.has(child.dataset.field)&&child.dataset.field!=='view')child.remove();
 for(const [key,label,text] of rows){let row=fields.querySelector('[data-field="'+key+'"]');if(!row){row=document.createElement('div');row.className='detail-row';row.dataset.field=key;row.append(document.createElement('span'),document.createElement('span'));fields.append(row);}setText(row.firstChild,label);setText(row.lastChild,text);}
 let link=fields.querySelector('[data-field=view]');
 if(value.can_view_member&&/^\/crm\/loyalty\/members\/\d+$/.test(value.membership_url||'')){
  if(!link){link=document.createElement('a');link.dataset.field='view';link.className='btn btn-secondary';link.textContent='View membership';fields.append(link);}if(link.getAttribute('href')!==value.membership_url)link.setAttribute('href',value.membership_url);
 }else link?.remove();
}
function select(id){
 if(cid===id)return;generation++;controller?.abort();controller=null;flight=null;dirty=false;cid=id;signature='';lastValue=null;lastAttempt=null;
 fields.replaceChildren();panel.dataset.stale='false';retry.hidden=true;setText(notice,cid?'Checking customer information…':'Select a conversation.');
}
function unavailable(restricted=false){
 if(restricted){fields.replaceChildren();lastValue=null;signature='';}
 panel.dataset.stale=lastValue?'true':'false';setText(notice,'Customer information temporarily unavailable.'+(lastValue?' Last verified information shown.':''));retry.hidden=false;
}
async function refresh(force=false){
 if(!cid)return;if(flight){if(force)dirty=true;return flight.promise;}
 if(!force&&lastAttempt!==null&&performance.now()-lastAttempt<TTL)return;
 const id=cid,version=generation;lastAttempt=performance.now();controller=new AbortController();const signal=controller.signal;
 const token={};flight=token;token.promise=(async()=>{
  try{
   const response=await fetch('/crm/whatsapp/api/conversations/'+id+'/customer-panel',{credentials:'same-origin',cache:'no-store',signal});
   if(version!==generation||id!==cid)return;
   if(response.redirected||!response.ok){unavailable(response.redirected||[401,403,404].includes(response.status));return;}
   const value=await response.json();if(version!==generation||id!==cid)return;
   if(value.viewer_id!==Number(root.dataset.userId)){unavailable(true);return;}
   if(value.conversation_id!==id||!Object.hasOwn(labels,value.state))throw Error();
   if(!['UNREGISTERED','AMBIGUOUS'].includes(value.state)&&(!Number.isSafeInteger(value.customer_id)||value.customer_id<1||typeof value.name!=='string'||!Number.isSafeInteger(value.qualifying_count)||value.qualifying_count<0||typeof value.membership_status!=='string'))throw Error();
   if(value.state==='ACTIVE'&&(!value.membership_id||value.membership_status!=='ACTIVE'))throw Error();
   render(value);
  }catch(error){if(version===generation&&id===cid&&error.name!=='AbortError')unavailable();}
  finally{if(version===generation&&flight===token){flight=null;controller=null;if(dirty){dirty=false;refresh(true);}}}
 })();return token.promise;
}
root.addEventListener('inbox-conversation',event=>{select(Number(event.detail.id)||null);refresh();});
root.addEventListener('inbox-conversation-data',event=>{select(Number(event.detail.id)||null);refresh();});
root.addEventListener('inbox-customer-invalidated',event=>{if(Number(event.detail.id)===cid)refresh(true);});
retry.onclick=()=>refresh(true);
window.addEventListener('pagehide',()=>select(null));
})();
