/* Separate admin-only synthetic flow. No cart/payment/Shopify endpoints. */
(()=>{'use strict';
const root=document.getElementById('pos'),open=document.getElementById('pos-test-open');if(!root||!open)return;
const dialog=document.getElementById('pos-test-dialog'),content=document.getElementById('pos-test-content'),message=document.getElementById('pos-test-message');
const namespace='pixiepinks.receipt-test.'+document.getElementById('pos-cashier').textContent;
function el(tag,text){const e=document.createElement(tag);if(text!==undefined)e.textContent=String(text);return e;}
async function api(path='',payload){const r=await fetch('/crm/pos/api/hardware/test-checkouts'+path,{credentials:'same-origin',...(payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':root.dataset.csrf},body:JSON.stringify(payload)})});const value=await r.json();if(!r.ok)throw Error(typeof value.detail==='string'?value.detail:'Test checkout unavailable');return value;}
let busy=false,selected='';
function button(text,fn){const b=el('button',text);b.type='button';b.onclick=async()=>{if(busy)return;busy=true;b.disabled=true;try{await fn();message.textContent='';await refresh();}catch(e){message.textContent=e.message+' Do not create another test to retry an uncertain print.';}finally{busy=false;b.disabled=false;}};return b;}
async function refresh(){const data=await api();content.replaceChildren();
const label=el('label','Receipt-only connector '),select=el('select');select.setAttribute('aria-label','Test receipt connector');
for(const d of data.devices){const o=el('option',d.name+' · register '+d.register_id+' · '+d.id);o.value=d.id;select.append(o);}if(!selected){const pending=JSON.parse(localStorage.getItem(namespace)||'null');selected=pending?.device||'';}if(selected)select.value=selected;selected=select.value;select.onchange=()=>{selected=select.value;};label.append(select);content.append(label);
const create=button('Create Test Checkout',async()=>{if(!selected)throw Error('Choose an active receipt-only connector');
let pending=JSON.parse(localStorage.getItem(namespace)||'null');
if(pending&&pending.device!==selected)throw Error('An unresolved create request belongs to another device; recover it first');
if(!pending){pending={device:selected,key:crypto.randomUUID()};localStorage.setItem(namespace,JSON.stringify(pending));}
const value=await api('',{device_id:pending.device,key:pending.key});localStorage.setItem(namespace,JSON.stringify({...pending,id:value.id}));});
content.append(create);
const pending=JSON.parse(localStorage.getItem(namespace)||'null');
if(pending?.id)content.append(button('Prepare another separate test',async()=>{if(window.confirm('Create a different test identity? Never use a new test to retry uncertain output.'))localStorage.removeItem(namespace);}));
for(const row of data.rows){const section=el('section');section.append(el('strong',row.reference),el('p','TEST checkout: '+row.status+' · Simulated LKR '+row.simulated_total+' · No money received'));
const j=row.receipt;section.append(el('p','Automatic Receipt Status: '+(j?j.status+' · '+j.id:'NOT QUEUED')));
if(j)section.append(el('p',j.result?.physical_output_verified===true?'Operator confirmed physical output':'Physical output NOT verified by printer submission'));
if(row.status==='DRAFT')section.append(button('Finalize Test',async()=>{if(!window.confirm('Send exactly one TEST — NOT A VALID SALE receipt? No real payment or order will be created.'))return;await api('/'+row.id+'/finalize',{confirm:true});}));
if(j)section.append(el('p','Inspect submission and confirm actual paper output in Hardware using this exact job ID. Never replay UNKNOWN jobs.'));
content.append(section);}
}
open.onclick=async()=>{dialog.showModal();try{await refresh();}catch(e){message.textContent=e.message;}};
document.getElementById('pos-test-close').onclick=()=>dialog.close();
api().then(()=>{open.hidden=false;}).catch(()=>{});
setInterval(()=>{if(dialog.open&&!busy)refresh().catch(e=>{message.textContent=e.message;});},5000);
})();
