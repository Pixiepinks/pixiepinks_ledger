(() => {'use strict';
window.QuestionFlowActions={attach(box,step,changed){
  const root=document.createElement('section'),panels={};
  const input=(area,title,value,tag='input',limit=200)=>{const label=document.createElement('label');label.textContent=title;const el=document.createElement(tag);el.value=value||'';el.required=true;el.setAttribute('aria-label',title);if(tag!=='select')el.maxLength=limit;label.append(el);area.append(label);el.oninput=changed;return el;};
  for(const kind of ['voice','details','checkout']){const area=document.createElement('fieldset');area.dataset.action=kind;panels[kind]=area;root.append(area);}
  const voiceArea=panels.voice;
  const query=input(voiceArea,'Search explanation recordings','');query.required=false;
  const find=document.createElement('button');find.type='button';find.className='btn btn-secondary';find.textContent='Find explanation recordings';voiceArea.append(find);
  const voices=input(voiceArea,'Explanation Saved Voice','','select');
  const empty=document.createElement('option');empty.value='';empty.textContent='Choose a ready Saved Voice';voices.append(empty);
  if(step.voice_id){const option=document.createElement('option');option.value=step.voice_id+':'+step.revision;option.textContent=step.voice_title||'Selected recording';voices.append(option);voices.value=option.value;}
  const audio=document.createElement('audio');audio.controls=true;audio.preload='none';voiceArea.append(audio);
  const status=document.createElement('p');status.setAttribute('role','status');voiceArea.append(status);
  const preview=()=>{if(voices.value)audio.src='/crm/saved-voices/api/'+voices.value.split(':')[0]+'/audio';else audio.removeAttribute('src');changed();};voices.onchange=preview;if(step.voice_id)audio.src='/crm/saved-voices/api/'+step.voice_id+'/audio';
  find.onclick=async()=>{status.textContent='Loading recordings…';try{const response=await fetch('/crm/quick-messages/customer-questions/voices?q='+encodeURIComponent(query.value));const data=await response.json();if(!response.ok)throw Error(data.detail||'Recordings unavailable');if(!box.isConnected)return;const old=voices.selectedOptions[0],value=voices.value;voices.replaceChildren(empty);for(const row of data.voices){const option=document.createElement('option');option.value=row.id+':'+row.revision;option.textContent=row.title;voices.append(option);}if(value&&![...voices.options].some(o=>o.value===value)&&old)voices.append(old);voices.value=value;status.textContent=data.voices.length?'Preview and select a recording.':'Prepare a ready recording in Saved Voices first.';}catch(e){status.textContent=e.message;}};
  query.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();find.click();}};
  const rows=document.createElement('div');panels.details.append(rows);
  const addRow=(value={label:'',value:''})=>{const row=document.createElement('div');row.className='product-detail-row';input(row,'Detail label',value.label,'input',80);input(row,'Detail value',value.value,'input',200);const remove=document.createElement('button');remove.type='button';remove.className='btn btn-secondary';remove.textContent='Remove row';remove.onclick=()=>{row.remove();changed();};row.append(remove);rows.append(row);};
  (step.rows||[{label:'',value:''}]).forEach(addRow);
  const add=document.createElement('button');add.type='button';add.className='btn btn-secondary';add.textContent='Add detail row';add.onclick=()=>{if(rows.children.length<25){addRow();changed();}};panels.details.append(add);
  const note=document.createElement('p');note.textContent='WhatsApp receives a readable message with one label and value per line. Add Brand, Size, Power, Warranty, Delivery or any other details.';panels.details.append(note);
  const source=input(panels.checkout,'Product step answer key',step.product_key||'','select',30);let sourceKey=step.product_key||'';source.onchange=()=>{sourceKey=source.value;changed();};
  root.sources=()=>{const current=source.value||sourceKey;source.replaceChildren();const empty=document.createElement('option');empty.value='';empty.textContent='Choose an earlier product step';source.append(empty);for(const other of document.querySelectorAll('#question-steps > fieldset')){if(other===box)break;if(other.querySelector('[data-field=type]')?.value==='products'){const option=document.createElement('option');option.value=other.querySelector('[data-field=key]').value;option.textContent=option.value;source.append(option);}}if(current&&![...source.options].some(option=>option.value===current)){const missing=document.createElement('option');missing.value=current;missing.textContent='Missing earlier product step: '+current;source.append(missing);}source.value=current;};
  const address=input(panels.checkout,'Delivery address question',step.address_prompt||'භාණ්ඩය එවිය යුතු සම්පූර්ණ ලිපිනය එවන්න. නිවසේ අංකය, වීදිය, ප්‍රදේශය සහ නගරය ඇතුළත් කරන්න.','textarea',500);
  const checkoutNote=document.createElement('p');checkoutNote.textContent='Collect name → address → optional nearest city → Phone 1 → optional Phone 2 → Confirm / Edit. Uses the price on the selected product card, quantity 1 and free delivery, with no greasing service. Place checkout last. Staff invoice and payment verification continue as before.';panels.checkout.append(checkoutNote);
  const scope={serviceConfig:{collect_delivery:true,payment:step.payment}};Object.defineProperty(scope,'isConnected',{get:()=>box.isConnected});
  let payment=null;
  root.sync=kind=>{if(kind==='checkout'&&!payment){payment=window.QuestionPayment.attach(scope,step.payment,changed);panels.checkout.append(payment);}for(const [key,area] of Object.entries(panels)){area.hidden=key!==kind;area.disabled=key!==kind;}root.hidden=!panels[kind];};
  root.value=kind=>kind==='voice'?{voice_id:Number(voices.value.split(':')[0])||null,revision:Number(voices.value.split(':')[1])||null,voice_title:voices.selectedOptions[0]?.textContent||''}:kind==='details'?{rows:[...rows.children].map(row=>{const values=row.querySelectorAll('input');return {label:values[0].value,value:values[1].value};})}:{product_key:source.value,address_prompt:address.value,...(scope.serviceConfig.payment?{payment:scope.serviceConfig.payment}:{})};
  return root;
}};
})();
