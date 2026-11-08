(() => {
  'use strict';
  const root=document.getElementById('inbox');if(!root)return;
  const mobile=matchMedia('(max-width:700px)'), byId=id=>document.getElementById(id);
  const management=byId('mobile-chat-management'), actions=byId('mobile-chat-actions');
  const menu=byId('mobile-chat-menu'), plus=byId('mobile-chat-plus'), input=byId('message-input'), messages=byId('messages');
  const homes=new Map();
  function move(node,target,before=null){
    if(!node || !target)return;
    if(!homes.has(node)){const marker=document.createComment('desktop control location');node.before(marker);homes.set(node,marker);}
    target.insertBefore(node,before);
  }
  function close(dialog){if(dialog.open)dialog.close();}
  for(const [dialog,button] of [[management,menu],[actions,plus]]){
    button.addEventListener('click',()=>{if(mobile.matches && root.classList.contains('chat-open')){dialog.showModal();button.setAttribute('aria-expanded','true');}});
    dialog.querySelector('[data-mobile-close]').addEventListener('click',()=>close(dialog));
    dialog.addEventListener('close',()=>button.setAttribute('aria-expanded','false'));
    // Close the action sheet before an existing control opens its own dialog/panel.
    dialog.addEventListener('click',event=>{
      const control=event.target.closest('button');
      if(!control || control.hasAttribute('data-mobile-close'))return;
      if(dialog===actions || ['mode-action','team-notes-open','details-action','customer-question-button'].includes(control.id))close(dialog);
    },true);
  }
  function autosize(){
    if(!mobile.matches){input.style.height='';return;}
    input.style.height='auto';input.style.height=Math.min(96,Math.max(38,input.scrollHeight))+'px';
  }
  function viewport(){
    const nearBottom=messages.scrollHeight-messages.scrollTop-messages.clientHeight<80;
    document.body.style.setProperty('--mobile-chat-height',`${window.visualViewport?.height || innerHeight}px`);
    if(nearBottom)requestAnimationFrame(()=>{if(mobile.matches && root.classList.contains('chat-open'))messages.scrollTop=messages.scrollHeight;});
  }
  function syncOpen(){
    const open=mobile.matches && root.classList.contains('chat-open');
    document.body.classList.toggle('mobile-chat-open',open);
    if(!open){close(management);close(actions);if(document.activeElement===input)input.blur();}viewport();
  }
  function layout(){
    close(management);close(actions);
    if(mobile.matches){
      move(byId('chat-header-actions'),byId('mobile-management-content'));
      move(document.querySelector('.conversation-owner-line'),byId('mobile-management-content'));
      move(byId('inbox-lead-status'),byId('mobile-management-content'));
      const text=byId('text-composer');move(byId('attachment-button'),text,input);
      move(byId('record-button'),text,byId('send-button'));
      move(byId('attachment-menu'),byId('composer'),byId('recording-controls'));
      for(const id of ['saved-voices-open','quick-messages-button','catalog-button','create-order-button'])move(byId(id),byId('mobile-actions-content'));
      input.rows=1;
    }else{
      for(const [node,marker] of homes)marker.after(node);
      input.rows=2;
    }
    autosize();syncOpen();
  }
  input.addEventListener('input',autosize);
  root.addEventListener('inbox-conversation-data',()=>{if(!input.value)autosize();});
  new MutationObserver(syncOpen).observe(root,{attributes:true,attributeFilter:['class']});
  root.addEventListener('inbox-conversation',()=>{close(management);close(actions);});
  new MutationObserver(()=>{if(byId('composer').hidden)close(actions);}).observe(byId('composer'),{attributes:true,attributeFilter:['hidden']});
  mobile.addEventListener('change',layout);
  window.visualViewport?.addEventListener('resize',viewport);
  window.addEventListener('resize',viewport);
  layout();
})();
