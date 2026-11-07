(() => {
 'use strict';
 const root=document.getElementById('crm-analytics');if(!root)return;
 const form=document.getElementById('dashboard-filters'),feedback=document.getElementById('dash-feedback'),dialog=document.getElementById('dash-details');
 const initial=new URLSearchParams(location.search),number=new Intl.NumberFormat('en-GB');
 const state={period:initial.get('period')||'today',date_from:initial.get('date_from')||'',date_to:initial.get('date_to')||'',staff_id:initial.get('staff_id')||'',country:initial.get('country')||'',source:initial.get('source')||'all',queue:initial.get('queue')||'needs_reply',staff_offset:Number(initial.get('staff_offset'))||0};
 let requestGeneration=0,controller=null,lastData=null,detailState=null,detailGeneration=0;
 const node=(tag,text)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;return el;};
 const count=value=>number.format(value||0);
 const local=value=>value?new Date(value).toLocaleString('en-GB',{timeZone:'Asia/Colombo'}):'—';
 const caption=text=>{const el=node('p',text);el.className='dash-empty';return el;};
 function button(label,handler,key){const el=node('button',label);el.type='button';el.onclick=handler;if(key)el.dataset.focusKey=key;return el;}
 const names={unique_contacts:'Unique contacts',first_contacts:'First recorded contacts',incoming:'Incoming messages',staff_sent:'Staff messages sent',contacted:'Customers contacted'};
 const queueNames={needs_reply:'Needs our reply',unassigned:'Unassigned',followups:'Follow-ups due',payment:'Payment verification',dispatch:'Awaiting dispatch'};
 function params(extra={}){const query=new URLSearchParams();for(const [key,value] of Object.entries({...state,...extra}))if(value!==''&&value!=null)query.set(key,String(value));return query;}
 function writeURL(){history.replaceState(null,'',location.pathname+'?'+params());}
 async function api(query,signal){
   const response=await fetch('/crm/dashboard/api?'+query,{credentials:'same-origin',cache:'no-store',signal});
   let data={};try{data=await response.json();}catch(_){}
   if(!response.ok||response.redirected)throw new Error(typeof data.detail==='string'?data.detail:'Dashboard is unavailable. Refresh or sign in again.');return data;
 }
 function updateControls(){
   form.querySelectorAll('[data-period]').forEach(el=>el.setAttribute('aria-pressed',String(el.dataset.period===state.period)));
   document.getElementById('dash-custom').hidden=state.period!=='custom';
   for(const key of ['date_from','date_to','source'])form.elements[key].value=state[key];
 }
 function fillSelect(name,items,empty){
   const el=form.elements[name];const signature=JSON.stringify(items);
   if(el.dataset.signature!==signature){
     el.replaceChildren(new Option(empty,''),...items.map(row=>new Option(row.label,String(row.value))));el.dataset.signature=signature;
   }
   el.value=state[name];
 }
 function waiting(value){
   if(!value)return '—';const minutes=Math.max(0,Math.floor((Date.now()-new Date(value).getTime())/60000));
   return minutes<60?minutes+'m':minutes<1440?Math.floor(minutes/60)+'h '+minutes%60+'m':Math.floor(minutes/1440)+'d '+Math.floor(minutes%1440/60)+'h';
 }
 function render(data){
   const focus=document.activeElement,focusKey=focus?.dataset.focusKey;
   const m=data.metrics,kpis=document.getElementById('dash-kpis');kpis.setAttribute('aria-busy','false');kpis.replaceChildren();
   if(!m)kpis.append(caption('WhatsApp activity is unavailable with your current section access.'));
   else Object.entries(names).forEach(([key,label],index)=>{
     const el=button('',()=>showDetail(key,label),'metric-'+key),icon=node('span',['◎','＋','▤','↗','♙'][index]);icon.className='dash-icon';icon.setAttribute('aria-hidden','true');
     const text=node('span'),title=node('span',label);title.className='label';text.append(title,node('strong',count(m[key])));el.append(icon,text);el.setAttribute('aria-label',`${label}: ${count(m[key])}. View matching activity`);kpis.append(el);
   });
   document.getElementById('dash-secondary').textContent=m?`${state.staff_id?'Contacts use assignment/sharing; outgoing totals use the selected sender. ':''}Returning contacts: ${count(m.returning_contacts)} · AI messages: ${count(m.ai_sent)} · System messages: ${count(m.system_sent)} · Automatic / unknown trigger: ${count(m.unknown_sent)}`:'';
   const breakdown=document.getElementById('dash-breakdown');breakdown.replaceChildren();
   if(m)for(const [key,label] of Object.entries(data.categories)){const el=button('',()=>showDetail('staff_sent',label,{category:key}),'category-'+key);el.append(node('span',label),node('strong',count(m[key])));breakdown.append(el);}
   else breakdown.append(caption('Message breakdown unavailable.'));
   const tabs=document.getElementById('dash-queues'),attention=document.getElementById('dash-attention');tabs.replaceChildren();attention.replaceChildren();
   const available=Object.entries(data.attention.counts).filter(([,value])=>value!==null);
   for(const [key,value] of available){const el=button(queueNames[key]+' '+count(value),()=>{state.queue=key;writeURL();refresh();},'queue-'+key);el.setAttribute('aria-pressed',String(key===state.queue));tabs.append(el);}
   if(data.attention.counts[state.queue]===null && available.length){state.queue=available[0][0];writeURL();refresh();return;}
   for(const item of data.attention.items){
     const row=node('div');row.className='dash-work';const name=node('div');name.append(node('strong',item.name),node('small',item.reason));
     const wait=node('span',waiting(item.waiting_since));wait.className='dash-wait';wait.dataset.overdue=String(item.waiting_since && Date.now()-new Date(item.waiting_since).getTime()>3600000);
     const owner=node('span',item.owner);owner.className='dash-owner';row.append(name,wait,owner);
     if(item.work && item.can_manage){const actions=node('div');actions.className='work-actions';window.CRMWorkActions?.buttons(actions,{id:item.conversation_id,name:item.name,work:item.work,can_manage:true},()=>{});row.append(actions);}
     if(item.url){const link=node('a','Open →');link.href=item.url;link.setAttribute('aria-label','Open '+item.name);row.append(link);}attention.append(row);
   }
   if(!data.attention.items.length)attention.append(caption(available.length?'No matching current work in this queue.':'Current work is unavailable with your section access.'));
   const all=document.getElementById('dash-all-attention');all.hidden=!data.attention.counts[state.queue];all.onclick=()=>showDetail('queue','Current · '+queueNames[state.queue]);
   const pipeline=document.getElementById('dash-pipeline');pipeline.replaceChildren();
   if(data.pipeline===null)pipeline.append(caption('CRM / Leads access is required.'));
   else if(!data.pipeline.length)pipeline.append(caption('No leads created in this period.'));
   else{
     const max=Math.max(...data.pipeline.map(row=>row.count),1);
     for(const item of data.pipeline){
       const label=data.lead_labels[item.status]||item.status.toLowerCase().replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase()),el=button('',()=>showDetail('pipeline',label,{lead_status:item.status}),'pipeline-'+item.status);el.className='dash-stage';
       const bar=node('span');bar.className='dash-bar';bar.setAttribute('aria-hidden','true');const fill=node('i');fill.style.width=(item.count/max*100)+'%';bar.append(fill);
       el.append(node('span',label),bar,node('strong',count(item.count)));el.setAttribute('aria-label',label+': '+count(item.count)+'. View leads');pipeline.append(el);
     }
   }
   const tbody=document.getElementById('dash-staff'),staffRegion=document.getElementById('dash-staff-region'),staffPages=document.getElementById('dash-staff-pages');tbody.replaceChildren();
   const team=data.staff_activity;staffRegion.hidden=!team;staffPages.hidden=!team;
   document.getElementById('dash-staff-note').textContent=!team?'Team-wide staff comparison is administrator-only, and requires WhatsApp access.':!team.rows.length?'No staff activity recorded in this period.':'';
   if(team){for(const row of team.rows){
     const tr=node('tr');let label=row.name+(row.active?'':' · inactive / historical');
     const values=[label,row.customers,row.text,row.quick_messages,row.saved_voices,row.recorded_voice,row.products,row.images_files+row.videos,row.consent,row.other,row.total,row.failed];
     values.forEach((value,index)=>{const cell=node(index===0?'th':'td',index===0?value:count(value));if(index===0)cell.scope='row';if(index===10)cell.className='dash-total';tr.append(cell);});
     const action=node('td');if(row.actor_id!=null)action.append(button('View sends',()=>showDetail('staff_sent',row.name,{actor_id:row.actor_id}),'staff-'+row.key));else action.textContent='Historical';tr.append(action);tbody.append(tr);
   }
   document.getElementById('dash-staff-count').textContent=`${team.total?team.offset+1:0}–${Math.min(team.offset+50,team.total)} of ${team.total}`;
   document.getElementById('dash-staff-prev').disabled=team.offset===0;document.getElementById('dash-staff-next').disabled=team.offset+50>=team.total;}
   const delivery=document.getElementById('dash-delivery');delivery.replaceChildren();
   if(m)for(const [key,label] of [['staff_sent','Accepted for sending'],['delivered','Delivered (includes read)'],['read','Read'],['delivery_failed','Later delivery failures'],['failed_attempts','Failed attempts'],['delivery_pending','Pending / unknown delivery']]){const el=node('div');el.append(node('span',label),node('strong',count(m[key])));delivery.append(el);}
   else delivery.append(caption('Delivery tracking unavailable.'));
   fillSelect('staff_id',data.filters.staff.map(u=>({value:u.id,label:u.name+(u.active?'':' · inactive')})),data.access.team?'All staff':'All accessible activity');
   fillSelect('country',data.filters.countries,'All countries');for(const name of ['staff_id','country','source'])form.elements[name].disabled=!data.access.whatsapp;
   document.getElementById('dash-range').textContent=data.window.label;
   document.getElementById('dash-updated').textContent='Updated '+local(data.generated_at);
   document.getElementById('dash-coverage').textContent=m?`Current selection: ${count(m.legacy_incoming)} legacy incoming records without provider IDs; ${count(m.unresolved_incoming)} incoming messages with unresolved numbers; ${count(m.unattributed)} accepted staff messages without stable sender attribution; ${count(m.claim_only_sends)} accepted claim-only sends using attempt timestamps.`:'';
   if(focusKey&&!focus.isConnected){const replacement=[...root.querySelectorAll('[data-focus-key]')].find(el=>el.dataset.focusKey===focusKey);replacement?.focus({preventScroll:true});}
 }
 async function refresh(){
   controller?.abort();controller=new AbortController();const token=++requestGeneration;
   try{
     const data=await api(params(),controller.signal);if(token!==requestGeneration)return;lastData=data;feedback.dataset.error='false';feedback.textContent='';render(data);
   }catch(error){if(error.name==='AbortError'||token!==requestGeneration)return;feedback.dataset.error='true';feedback.textContent=error.message+(lastData?' Displaying the last successful refresh.':' No figures are available yet.');}
 }
 async function loadDetail(){
   if(!detailState)return;const token=++detailGeneration,status=document.getElementById('dash-details-status');status.textContent='Loading matching records…';
   try{
     const data=await api(params({...detailState.extra,detail:detailState.mode,offset:detailState.offset}));if(token!==detailGeneration||!dialog.open)return;
     const list=document.getElementById('dash-details-list');list.replaceChildren();
     for(const row of data.detail.rows){const el=node('div');el.className='dash-detail-row';const text=node('div');text.append(node('strong',row.name),node('small',[row.phone,row.category||row.reason,row.at?local(row.at):row.owner].filter(Boolean).join(' · ')));el.append(text);if(row.url){const a=node('a','Open →');a.href=row.url;el.append(a);}list.append(el);}
     if(!data.detail.rows.length)list.append(caption('No matching records.'));
     status.textContent=`${count(data.detail.total)} matching records · ${detailState.mode==='queue'?'Current work':'Selected reporting period'}`;
     document.getElementById('dash-details-prev').disabled=detailState.offset===0;document.getElementById('dash-details-next').disabled=detailState.offset+50>=data.detail.total;
   }catch(error){if(token===detailGeneration)status.textContent=error.message;}
 }
 function showDetail(mode,label,extra={}){detailState={mode,extra,offset:0};document.getElementById('dash-details-title').textContent=label;document.getElementById('dash-details-list').replaceChildren();if(!dialog.open)dialog.showModal();loadDetail();}
 document.getElementById('dash-details-close').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{detailGeneration++;detailState=null;});
 document.getElementById('dash-details-prev').onclick=()=>{detailState.offset=Math.max(0,detailState.offset-50);loadDetail();};document.getElementById('dash-details-next').onclick=()=>{detailState.offset+=50;loadDetail();};
 document.getElementById('dash-refresh').onclick=refresh;
 document.getElementById('dash-staff-prev').onclick=()=>{state.staff_offset=Math.max(0,state.staff_offset-50);writeURL();refresh();};document.getElementById('dash-staff-next').onclick=()=>{state.staff_offset+=50;writeURL();refresh();};
 form.querySelectorAll('[data-period]').forEach(el=>el.onclick=()=>{state.period=el.dataset.period;state.staff_offset=0;updateControls();if(state.period==='custom'){form.elements.date_from.focus();return;}writeURL();refresh();});
 form.onsubmit=event=>{event.preventDefault();for(const key of ['staff_id','country','source','date_from','date_to'])state[key]=form.elements[key].value;state.staff_offset=0;writeURL();refresh();};
 updateControls();
 for(const name of ['staff_id','country'])if(state[name]){form.elements[name].append(new Option(state[name],state[name]));form.elements[name].value=state[name];}
 if(matchMedia('(max-width:600px)').matches)document.getElementById('dash-advanced').open=false;
 for(let i=0;i<5;i++){const el=node('div');el.className='dash-loading';document.getElementById('dash-kpis').append(el);}
 document.addEventListener('conversation-work-updated',()=>refresh());
 refresh();setInterval(()=>{if(!document.hidden&&!dialog.open)refresh();},30000);
})();
