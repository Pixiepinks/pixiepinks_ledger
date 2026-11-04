(() => {
  'use strict';
  const root=document.getElementById('inbox'), panel=document.getElementById('collaboration-panel');
  if (!root || !panel) return;
  const openButton=document.getElementById('team-notes-open');
  openButton.addEventListener('click',()=>{
    if(!cid)return;
    if(!panel.open)panel.showModal();
    openButton.setAttribute('aria-expanded','true');load();
  });
  byClose();
  function byClose(){
    document.getElementById('team-notes-close').addEventListener('click',()=>panel.close());
    panel.addEventListener('close',()=>openButton.setAttribute('aria-expanded','false'));
    panel.addEventListener('click',event=>{
      if(event.target!==panel)return;
      const box=panel.getBoundingClientRect();
      if(event.clientX<box.left || event.clientX>box.right || event.clientY<box.top || event.clientY>box.bottom)panel.close();
    });
  }
  const csrf=root.dataset.assignmentCsrf, admin=root.dataset.admin==='true';
  let cid=null, generation=0, version=0, loading=false, staff=[], before=null, noteKey=null;
  const byId=id=>document.getElementById(id);
  const say=value=>{byId('collaboration-feedback').textContent=value;};
  const el=(tag,value)=>{const node=document.createElement(tag);if(value!=null)node.textContent=value;return node;};
  async function api(path,body) {
    const res=await fetch('/crm/whatsapp/api'+path,{credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},...(body ? {method:'POST',body:JSON.stringify(body)} : {})});
    let data={};try{data=await res.json();}catch(_){}
    if(!res.ok || res.redirected)throw new Error(data.detail || 'Unable to load collaboration. Refresh and try again.');
    return data;
  }
  const path=(suffix,id=cid)=>`/conversations/${id}/${suffix}`;
  function rights(conversation) {
    const canReply=conversation.can_reply!==false, manage=conversation.can_manage!==false;
    byId('composer').hidden=!canReply;byId('mode-action').hidden=!manage;
    // Staff with a reply grant can message, but cannot edit sales/lead state through sharing.
    const lead=byId('inbox-lead-status');if(lead && !manage)lead.hidden=true;
    byId('shared-permission').textContent=manage?'Private team workspace. Notes do not answer the customer.':canReply?'Shared access: read, private notes and customer replies.':'Shared access: read and private notes only. Customer replies are disabled.';
  }
  function renderNotes(notes,prepend=false) {
    const nodes=notes.map(n=>{
      const article=el('article');article.dataset.noteId=n.id;
      article.append(el('strong',`${n.author} · Private note`),el('div',n.body),el('small',new Date(n.created_at).toLocaleString('en-GB',{timeZone:'Asia/Colombo'})));
      if(n.mentions.length)article.append(el('small',n.mentions.map(u=>'@'+u.username).join(' ')));
      return article;
    });
    if(prepend)byId('private-notes').prepend(...nodes);else byId('private-notes').replaceChildren(...nodes);
  }
  async function load() {
    if(!cid || loading)return;
    const id=cid,gen=generation;loading=true;
    try {
      const [data,notes]=await Promise.all([api(path('collaboration',id)),api(path('notes',id))]);
      if(gen!==generation || id!==cid)return;
      version=data.version;
      const chosen=new Set([...byId('private-note-mentions').selectedOptions].map(o=>o.value));
      byId('private-note-mentions').replaceChildren(...data.participants.map(u=>{const o=new Option(u.username,u.id);o.selected=chosen.has(String(u.id));return o;}));
      renderNotes(notes.notes);before=notes.next_before;byId('notes-older').hidden=!before;
      if(admin) {
        staff=data.share_candidates;
        const selected=byId('share-user').value;
        byId('share-user').replaceChildren(...staff.filter(u=>u.id!==Number(root.dataset.userId)).map(u=>new Option(u.username,u.id)));
        if([...byId('share-user').options].some(o=>o.value===selected))byId('share-user').value=selected;
        byId('share-list').replaceChildren(...data.shares.map(s=>{
          const row=el('div');row.append(el('span',`${s.username}: ${s.can_reply?'read, notes & replies':'read & notes'}`));
          const revoke=el('button','Revoke');revoke.type='button';revoke.className='btn btn-secondary';
          revoke.onclick=()=>saveShare(s.user_id,false,true);row.append(revoke);return row;
        }));
        byId('share-audit').replaceChildren(...data.audit.map(a=>el('div',`${new Date(a.at).toLocaleString('en-GB',{timeZone:'Asia/Colombo'})} · ${a.actor} ${a.action.toLowerCase()} ${a.user}${a.action==='REVOKED'?'':a.can_reply?' (replies allowed)':' (read & notes)'}`)));
      }
    }catch(e){if(gen===generation)say(e.message);}
    finally{if(gen===generation)loading=false;}
  }
  async function saveShare(uid,canReply,revoke) {
    const id=cid,gen=generation;if(!id)return;
    try{await api(path('sharing',id),{user_id:uid,can_reply:canReply,revoke,expected_version:version});if(gen===generation){say(revoke?'Shared access revoked.':'Shared access saved.');byId('share-reply').checked=false;await load();}}
    catch(e){if(gen===generation){say(e.message);await load();}}
  }
  byId('share-user')?.addEventListener('change',()=>{byId('share-reply').checked=false;});
  byId('share-form')?.addEventListener('submit',e=>{e.preventDefault();saveShare(Number(byId('share-user').value),byId('share-reply').checked,false);});
  byId('private-note-form').addEventListener('submit',async e=>{
    e.preventDefault();const id=cid,gen=generation;if(!id)return;
    const button=e.submitter;button.disabled=true;
    const body=byId('private-note-body').value,mentions=[...byId('private-note-mentions').selectedOptions].map(o=>Number(o.value));
    noteKey ||= crypto.randomUUID();
    try{await api(path('notes',id),{body,mentions,client_key:noteKey});if(gen===generation){byId('private-note-body').value='';noteKey=null;say('Private note added. Nothing sent to customer.');await load();}}
    catch(err){if(gen===generation)say(err.message);}
    finally{button.disabled=false;}
  });
  // Editing after an uncertain submission is a new note, not a reused content key.
  byId('private-note-body').addEventListener('input',()=>{noteKey=null;});
  byId('private-note-mentions').addEventListener('change',()=>{noteKey=null;});
  byId('notes-older').onclick=async()=>{
    const gen=generation;if(!cid || !before)return;
    try{const data=await api(path(`notes?before_id=${before}`));if(gen===generation){renderNotes(data.notes,true);before=data.next_before;byId('notes-older').hidden=!before;}}
    catch(e){if(gen===generation)say(e.message);}
  };
  root.addEventListener('inbox-conversation',e=>{
    cid=e.detail.id;generation++;loading=false;before=null;noteKey=null;if(panel.open)panel.close();
    byId('private-note-body').value='';byId('private-notes').replaceChildren();byId('private-note-mentions').replaceChildren();byId('share-list')?.replaceChildren();byId('share-audit')?.replaceChildren();if(byId('share-reply'))byId('share-reply').checked=false;say('');
  });
  root.addEventListener('inbox-conversation-data',e=>{
    if(cid!==e.detail.id){cid=e.detail.id;generation++;loading=false;}
    byId('team-notes-customer').textContent=e.detail.conversation.customer_name || 'WhatsApp customer';
    // Inbox renders its mode control after dispatching this event.
    queueMicrotask(()=>{if(cid===e.detail.id)rights(e.detail.conversation);});
    load();
  });
})();
