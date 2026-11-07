/* UI state only. Conversation authorization always remains on the server. */
window.createInboxFilters = (root,onChange) => {
  const row=document.getElementById('inbox-filter-row'), dialog=document.getElementById('inbox-filters-dialog');
  const open=document.getElementById('inbox-filters-open'), owner=document.getElementById('inbox-filter-owner'), lead=document.getElementById('inbox-filter-lead');
  const extras=document.getElementById('active-extra-filters'), feedback=document.getElementById('inbox-filters-feedback');
  const left=document.getElementById('filter-scroll-left'),right=document.getElementById('filter-scroll-right');
  const admin=root.dataset.admin==='true', defaults=()=>({mailbox:admin?'all':'accessible',replyQueue:'all',unread:false,mode:'',payment:false,ownerId:'',ownerLabel:'',leadStatus:''});
  const params=new URLSearchParams(location.search), legacy=params.get('filter');
  let state=defaults(),clearDraft=false,openGeneration=0;
  if(['mine','shared',...(admin?['all','unassigned']:['accessible'])].includes(params.get('mailbox')))state.mailbox=params.get('mailbox');
  if(['needs_reply','waiting_customer','follow_up_later','followups_due','resolved','window_expired'].includes(params.get('reply_queue')))state.replyQueue=params.get('reply_queue');
  state.unread=legacy==='unread'||params.get('unread')==='true';state.payment=legacy==='payment'||params.get('payment_required')==='true';
  state.mode=legacy==='ai'?'AI':legacy==='human'?'HUMAN':['AI','HUMAN'].includes(params.get('mode'))?params.get('mode'):'';
  if(admin && /^\d+$/.test(params.get('owner_id')||'')){state.ownerId=params.get('owner_id');state.ownerLabel='Assigned staff';}
  if([...lead.options].some(o=>o.value===params.get('lead_status')))state.leadStatus=params.get('lead_status');
  function arrows(){left.hidden=row.scrollLeft<2;right.hidden=row.scrollWidth-row.clientWidth-row.scrollLeft<2;}
  function render(){
    const d=defaults(),all=Object.keys(d).filter(k=>k!=='ownerLabel').every(k=>state[k]===d[k]);
    row.querySelectorAll('[data-key]').forEach(button=>{
      const key=button.dataset.key;
      const active=key==='all'?all:button.dataset.value?state[key]===button.dataset.value:state[key]===true;
      button.classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));
    });
    extras.replaceChildren();
    for(const [key,label] of [['ownerId',state.ownerLabel],['leadStatus',[...lead.options].find(o=>o.value===state.leadStatus)?.text]]){
      if(!state[key])continue;
      const button=document.createElement('button');button.type='button';button.className='selected';button.dataset.remove=key;button.textContent=`${label} ×`;button.setAttribute('aria-label',`Remove ${label} filter`);
      button.onclick=()=>{state[key]='';if(key==='ownerId')state.ownerLabel='';commit();open.focus({preventScroll:true});};extras.append(button);
    }
    requestAnimationFrame(arrows);
  }
  function commit(){
    render();
    const url=new URL(location.href);
    ['filter','mailbox','reply_queue','unread','mode','payment_required','owner_id','lead_status'].forEach(k=>url.searchParams.delete(k));
    const d=defaults();
    if(state.mailbox!==d.mailbox)url.searchParams.set('mailbox',state.mailbox);
    if(state.replyQueue!=='all')url.searchParams.set('reply_queue',state.replyQueue);
    if(state.unread)url.searchParams.set('unread','true');if(state.mode)url.searchParams.set('mode',state.mode);
    if(state.payment)url.searchParams.set('payment_required','true');if(state.ownerId)url.searchParams.set('owner_id',state.ownerId);
    if(state.leadStatus)url.searchParams.set('lead_status',state.leadStatus);
    history.replaceState(null,'',url);onChange({...state});
  }
  row.querySelectorAll('[data-key]').forEach(button=>button.onclick=()=>{
    const key=button.dataset.key;
    if(key==='all')state=defaults();
    else if(button.dataset.value)state[key]=state[key]===button.dataset.value?defaults()[key]:button.dataset.value;
    else state[key]=!state[key];
    commit();button.scrollIntoView({block:'nearest',inline:'nearest'});
  });
  open.onclick=async()=>{
    clearDraft=false;feedback.textContent='';lead.value=state.leadStatus;
    if(owner){owner.replaceChildren(new Option('Any staff member',''));if(state.ownerId)owner.add(new Option(state.ownerLabel,state.ownerId));owner.value=state.ownerId;owner.disabled=true;}
    dialog.showModal();open.setAttribute('aria-expanded','true');const generation=++openGeneration;
    if(owner){
      try{
        const response=await fetch('/crm/whatsapp/api/assignees',{credentials:'same-origin',cache:'no-store'});
        if(!response.ok||response.redirected)throw new Error('Could not load staff. Close and try again.');
        const data=await response.json();if(!dialog.open || generation!==openGeneration)return;
        const selected=owner.value;owner.replaceChildren(new Option('Any staff member',''),...data.assignees.map(u=>new Option(u.username,String(u.id))));
        if(selected && ![...owner.options].some(o=>o.value===selected))owner.add(new Option(`${state.ownerLabel} (unavailable)`,selected));
        owner.value=selected;owner.disabled=false;
      }catch(error){if(dialog.open && generation===openGeneration)feedback.textContent=error.message;}
    }
  };
  document.getElementById('inbox-filters-close').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>{openGeneration++;open.setAttribute('aria-expanded','false');open.focus({preventScroll:true});});
  document.getElementById('inbox-filters-clear').onclick=()=>{clearDraft=true;if(owner)owner.value='';lead.value='';feedback.textContent='All filters will be cleared when you apply.';};
  document.getElementById('inbox-filters-form').onsubmit=event=>{
    event.preventDefault();if(clearDraft)state=defaults();
    state.leadStatus=lead.value;
    if(owner){state.ownerId=owner.value;state.ownerLabel=owner.selectedOptions[0]?.text||'';}
    commit();dialog.close();
    if(state.ownerId||state.leadStatus)open.scrollIntoView({block:'nearest',inline:'nearest'});
  };
  left.onclick=()=>row.scrollBy({left:-220,behavior:'smooth'});right.onclick=()=>row.scrollBy({left:220,behavior:'smooth'});
  row.addEventListener('scroll',arrows,{passive:true});new ResizeObserver(arrows).observe(row);
  row.addEventListener('focusin',event=>{if(event.target!==row)event.target.scrollIntoView({block:'nearest',inline:'nearest'});});
  row.addEventListener('wheel',event=>{
    if(event.deltaX||event.shiftKey||!event.deltaY)return;
    const previous=row.scrollLeft;row.scrollLeft+=event.deltaY;
    if(row.scrollLeft!==previous)event.preventDefault();
  },{passive:false});
  render();return {...state};
};
