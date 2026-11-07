(() => {
 'use strict';
 const dialog=document.getElementById('work-dialog');if(!dialog)return;
 let current=null,token=null,busy=false;
 const $=id=>document.getElementById(id);
 async function api(path,options={}){
   const r=await fetch(path,{credentials:'same-origin',cache:'no-store',...options});let data={};try{data=await r.json();}catch(_){}
   if(!r.ok||r.redirected)throw new Error(typeof data.detail==='string'?data.detail:'Could not update conversation. Refresh and review.');return data;
 }
 async function auth(){if(!token)token=await api('/crm/whatsapp/api/work-csrf');return token;}
 function localInput(utc){return new Date(new Date(utc).getTime()+330*60000).toISOString().slice(0,16);}
 async function open(item,action,done){
   if(busy)return;
   current={item,action,done,key:crypto.randomUUID()};$('work-feedback').textContent='';$('work-note').value='';$('work-reason').value='';
   $('work-title').textContent=action==='bulk'?'Resolve selected conversations':{resolve:'Mark as resolved',defer:'Follow up later',reopen:'Reopen conversation',cancel:'Cancel follow-up'}[action];
   $('work-customer').textContent=action==='bulk'?`${item.length} explicitly selected conversations`:item.name||'WhatsApp customer';
   $('work-schedule').hidden=action!=='defer';$('work-due').required=action==='defer';$('work-due').value=localInput(item.work?.due_at||new Date(Date.now()+3600000));
   $('work-confirm').disabled=true;dialog.showModal();
   const session=current;
   try{const info=await auth();if(current!==session||!dialog.open)return;
     const select=$('work-assignee');select.replaceChildren(new Option('Unassigned',''),new Option('Myself',String(info.user_id)));
     if(info.admin){const data=await api('/crm/whatsapp/api/assignees');if(current!==session||!dialog.open)return;select.replaceChildren(new Option('Unassigned',''),...data.assignees.map(u=>new Option(u.username,String(u.id))));}
     select.value=item.work?.followup_user_id||'';$('work-confirm').disabled=false;
   }catch(e){$('work-feedback').textContent=e.message;}
 }
 $('work-close').onclick=()=>{if(!busy)dialog.close();};dialog.addEventListener('cancel',e=>{if(busy)e.preventDefault();});dialog.addEventListener('close',()=>{if(!busy)current=null;});
 $('work-form').onsubmit=async event=>{
   event.preventDefault();if(busy||!current)return;
   const session=current;busy=true;$('work-confirm').disabled=true;$('work-close').disabled=true;
   const reason=[$('work-reason').value,$('work-note').value.trim()].filter(Boolean).join(': ');
   const snapshot=c=>({version:c.work.version,last_inbound_id:c.work.last_inbound_id,review_token:c.work.review_token});
   let path,payload;
   if(session.action==='bulk'){path='/crm/whatsapp/api/work-status/bulk';payload={key:session.key,reason,items:session.item.map(i=>({id:i.id,...snapshot(i)}))};}
   else{path=`/crm/whatsapp/api/conversations/${session.item.id}/work-status`;payload={action:session.action,key:session.key,reason,...snapshot(session.item)};
     if(session.action==='defer'){payload.due_at=$('work-due').value+':00+05:30';payload.followup_user_id=$('work-assignee').value?Number($('work-assignee').value):null;}}
   try{const result=await api(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrf_token},body:JSON.stringify(payload)});
     dialog.close();current=null;session.done?.(result);document.dispatchEvent(new CustomEvent('conversation-work-updated',{detail:result}));
   }catch(e){$('work-feedback').textContent=e.message;}
   finally{busy=false;$('work-confirm').disabled=false;$('work-close').disabled=false;}
 };
 function buttons(container,item,done){
   const focus=container.contains(document.activeElement)?document.activeElement.dataset.workAction:null;
   container.replaceChildren();if(!item.work)return;
   const status=document.createElement('strong');status.textContent=item.work.status==='RESOLVED'?'Resolved':item.work.status==='FOLLOW_UP_LATER'?'Follow up · '+new Date(item.work.due_at).toLocaleString('en-GB',{timeZone:'Asia/Colombo'}):item.work.attention_required?'Needs reply':'No pending reply';container.append(status);
   if(item.work.window_closed){const label=document.createElement('span');label.textContent='WhatsApp window closed';container.append(label);}
   const actions=!item.can_manage?[]:item.work.status==='RESOLVED'?['reopen']:item.work.status==='FOLLOW_UP_LATER'?['resolve','defer','cancel']:['resolve','defer'];
   for(const action of actions){const b=document.createElement('button');b.type='button';b.textContent={resolve:'Resolve',defer:item.work.status==='FOLLOW_UP_LATER'?'Reschedule':'Follow up',reopen:'Reopen',cancel:'Cancel follow-up'}[action];b.dataset.workAction=action;b.onclick=()=>open(item,action,done);container.append(b);}
   const history=document.createElement('button');history.type='button';history.dataset.workAction='history';history.textContent='History';history.onclick=async()=>{
     try{const data=await api(`/crm/whatsapp/api/conversations/${item.id}/work-history`);const pane=document.createElement('dialog');pane.className='work-dialog work-history';const title=document.createElement('h2');title.textContent='Conversation status history';pane.append(title);
       if(!data.events.length)pane.append(document.createTextNode('No status changes yet.'));
       for(const e of data.events){const p=document.createElement('p');p.textContent=`${new Date(e.at).toLocaleString('en-GB',{timeZone:'Asia/Colombo'})} · ${e.actor}\n${e.previous_status} → ${e.status}\n${e.reason||''}`;pane.append(p);}
       let offset=data.events.length;
       const more=document.createElement('button');more.textContent='Load older changes';more.hidden=offset>=data.total;
       more.onclick=async()=>{more.disabled=true;try{const older=await api(`/crm/whatsapp/api/conversations/${item.id}/work-history?offset=${offset}`);if(!pane.open)return;
         for(const e of older.events){const p=document.createElement('p');p.textContent=`${new Date(e.at).toLocaleString('en-GB',{timeZone:'Asia/Colombo'})} · ${e.actor}\n${e.previous_status} → ${e.status}\n${e.reason||''}`;pane.insertBefore(p,more);}
         offset+=older.events.length;more.hidden=offset>=older.total;
       }catch(e){more.textContent=e.message+' · Retry';}finally{more.disabled=false;}};pane.append(more);
       const close=document.createElement('button');close.textContent='Close';close.onclick=()=>pane.close();pane.append(close);pane.addEventListener('close',()=>pane.remove());document.body.append(pane);pane.showModal();
     }catch(e){const note=document.createElement('span');note.textContent=e.message;container.append(note);}};container.append(history);
   if(focus)(container.querySelector(`[data-work-action="${focus}"]`)||container.querySelector("button"))?.focus({preventScroll:true});
 }
 window.CRMWorkActions={open,buttons,api};
})();
