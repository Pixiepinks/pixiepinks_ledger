(()=>{'use strict';const drafts=new Map();document.querySelector('#inbox')?.addEventListener('inbox-conversation',()=>drafts.clear());
window.QuestionPaymentReview={attach(parent,run,cid,csrf,onChanged){
  const answer=Object.values(run.answers).find(value=>value?.payment?.receipt_message_id);
  if(!answer||!['SLIP_REVIEW','VERIFIED'].includes(answer.payment.status))return;
  const root=document.createElement('section');root.className='payment-review';const title=document.createElement('h3');title.textContent='Payment verification';const status=document.createElement('p');status.setAttribute('role','status');status.textContent='Loading payment review…';root.append(title,status);parent.append(root);
  const url=`/crm/whatsapp/api/conversations/${cid}/customer-questions/${run.id}/payment-review`;let data=null,busy=false;
  async function request(payload){const response=await fetch(url,{method:payload?'POST':'GET',headers:payload?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:payload?JSON.stringify(payload):undefined});const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Payment review unavailable');return result;}
  async function act(action,payload={}){if(busy)return;busy=true;status.textContent='Saving payment review…';root.querySelectorAll('button').forEach(button=>button.disabled=true);try{data=await request({action,version:data.version,...payload});drafts.delete(run.id);if(root.isConnected){render();status.textContent=action==='verify'?'Payment recorded and verified. Confirmation status is shown below.':'Updated.';await onChanged();}}catch(error){if(root.isConnected){status.textContent=error.message;root.querySelectorAll('button').forEach(button=>button.disabled=false);}}finally{busy=false;}}
  function button(label,fn){const b=document.createElement('button');b.type='button';b.className='btn btn-secondary';b.textContent=label;b.onclick=fn;return b;}
  function render(){root.replaceChildren(title,status);if(!data)return;status.textContent='';
    if(data.order){const link=document.createElement('a');link.href=data.order.url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=`${data.order.number} · ${data.order.payment_status} · Rs. ${data.order.total} · Outstanding Rs. ${data.order.outstanding}`;root.append(link);}
    if(data.payment.status==='VERIFIED'){
      const p=document.createElement('p');p.textContent=`Bank payment verified by ${data.payment.verified_by}. Customer confirmation: ${data.payment.confirmation_status}.`;root.append(p);
      if(!data.automation_enabled&&data.payment.confirmation_status==='QUEUED'){const note=document.createElement('p');note.textContent='Customer Question Automation is OFF. The confirmation will wait until it is enabled.';root.append(note);}
      if(data.payment.confirmation_status==='WINDOW_CLOSED'){const note=document.createElement('p');note.textContent='Payment is saved. Ask the customer to message again to reopen the reply window, then queue the confirmation.';root.append(note);if(data.can_verify)root.append(button('Queue payment confirmation',()=>act('queue_confirmation')));}
      if(data.payment.confirmation_status==='UNCERTAIN'){const note=document.createElement('p');note.textContent='Check the customer chat before sending anything again. This confirmation will not be retried automatically.';root.append(note);}
      return;
    }
    const note=document.createElement('p');note.textContent='Check the actual bank transaction against the slip. Record only the amount received. A partial payment leaves the remaining balance outstanding.';root.append(note);
    if(!data.order&&data.can_prepare)root.append(button('Create order from confirmed details',()=>act('prepare_order')));
    if(!data.can_verify){const p=document.createElement('p');p.textContent='An administrator with payment-verification permission must verify this transfer.';root.append(p);return;}
    const form=document.createElement('form');form.className='payment-review-form';
    const draft=drafts.get(run.id)||{sales_order_id:data.order?.id||'',amount:data.order?.outstanding||data.quoted_total,payment_date:data.today,payment_reference:'',bank_checked:false};drafts.set(run.id,draft);
    function field(label,key,tag='input'){const wrap=document.createElement('label');wrap.textContent=label;const input=document.createElement(tag);input.setAttribute('aria-label',label);input.required=true;input.value=draft[key];input.oninput=()=>{draft[key]=input.value;};wrap.append(input);form.append(wrap);return input;}
    const select=field('Sales order for this payment','sales_order_id','select');const empty=document.createElement('option');empty.value='';empty.textContent='Select a matching order…';select.append(empty);
    for(const order of data.orders){const option=document.createElement('option');option.value=order.id;option.textContent=`${order.number} · Rs. ${order.total} · Outstanding ${order.outstanding}`;select.append(option);}select.value=draft.sales_order_id;
    if(data.order){select.value=data.order.id;select.disabled=true;draft.sales_order_id=data.order.id;}
    const amount=field('Actual amount received (LKR)','amount');amount.type='number';amount.min='0.01';amount.step='0.01';amount.max='999999999999.99';
    select.onchange=()=>{draft.sales_order_id=select.value;const order=data.orders.find(order=>order.id===Number(select.value));if(order){amount.value=order.outstanding;draft.amount=amount.value;}};
    const date=field('Bank transaction date','payment_date');date.type='date';date.max=data.today;
    const reference=field('Bank transaction reference','payment_reference');reference.maxLength=255;
    const checkLabel=document.createElement('label'),checked=document.createElement('input');checked.type='checkbox';checked.required=true;checked.checked=draft.bank_checked;checked.setAttribute('aria-label','I checked this transaction in the bank account');checked.onchange=()=>{draft.bank_checked=checked.checked;};checkLabel.append(checked,document.createTextNode(' I checked this transaction in the bank account'));form.append(checkLabel);
    const submit=document.createElement('button');submit.type='submit';submit.className='btn btn-primary';submit.textContent='Verify payment & queue confirmation';form.append(submit);
    form.onsubmit=event=>{event.preventDefault();act('verify',{sales_order_id:Number(draft.sales_order_id),amount:draft.amount,payment_date:draft.payment_date,payment_reference:draft.payment_reference,bank_checked:draft.bank_checked});};root.append(form);
  }
  request().then(result=>{if(root.isConnected){data=result;render();}}).catch(error=>{if(root.isConnected)status.textContent=error.message;});
}};})();
