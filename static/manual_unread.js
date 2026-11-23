/* Internal reminders only: never touches actual unread counts or Meta read state. */
window.CRMManualUnread = (() => {
  const root=document.getElementById('inbox'), palette={blue:'#1d4ed8',purple:'#7e22ce',orange:'#9a3412',green:'#15803d',pink:'#be185d'};
  const me=Number(root.dataset.userId),admin=root.dataset.admin==='true';
  function controls(conversation,refresh){
    const box=document.createElement('div');box.className='manual-unread-controls';
    const marks=conversation.manual_unread||[],dots=document.createElement('div');dots.className='manual-unread-dots';
    const feedback=document.createElement('small');feedback.className='manual-unread-feedback';feedback.setAttribute('role','status');
    for(const mark of marks){const dot=document.createElement('button');dot.type='button';dot.className='manual-unread-dot';dot.style.backgroundColor=palette[mark.colour]||palette.blue;dot.title=`Marked unread by ${mark.name}`;dot.setAttribute('aria-label',dot.title);dot.onclick=()=>{feedback.textContent=dot.title;};dots.append(dot);}
    const menu=document.createElement('details');menu.className='manual-unread-menu';const toggle=document.createElement('summary');toggle.textContent='⋮';toggle.setAttribute('aria-label',`Unread actions for ${conversation.customer_name}`);toggle.title='Unread actions';menu.append(toggle);
    const actions=document.createElement('div');actions.className='manual-unread-actions';
    function action(label,staffId,mark,disabled=false){const button=document.createElement('button');button.type='button';button.textContent=label;button.disabled=disabled;
      button.onclick=async()=>{if(box.dataset.busy==='true')return;box.dataset.busy='true';const buttons=[...actions.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);feedback.textContent='Saving internal unread mark…';
        try{const response=await fetch(`/crm/whatsapp/api/conversations/${conversation.id}/manual-unread${mark?'':'/'+staffId}`,{method:mark?'POST':'DELETE',credentials:'same-origin',headers:{'X-CSRF-Token':root.dataset.assignmentCsrf},cache:'no-store'});if(!response.ok||response.redirected)throw Error('Could not save. Refresh and check access before retrying.');const data=await response.json();conversation.manual_unread=data.manual_unread;menu.open=false;feedback.textContent='Unread mark saved.';refresh();}
        catch(error){feedback.textContent=error.message;}finally{box.dataset.busy='false';buttons.forEach(b=>b.disabled=false);}};actions.append(button);}
    action('Mark as Unread',me,true,marks.some(m=>m.staff_id===me));action('Clear My Unread Mark',me,false,!marks.some(m=>m.staff_id===me));
    for(const mark of marks){const name=document.createElement('span');name.textContent=`Marked by ${mark.name}`;actions.append(name);if(admin&&mark.staff_id!==me)action(`Clear mark: ${mark.name}`,mark.staff_id,false);}
    menu.append(actions);box.append(dots,menu,feedback);return box;
  }
  return {controls};
})();
