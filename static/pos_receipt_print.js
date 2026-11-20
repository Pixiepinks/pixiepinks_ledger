/* Browser preview only. No silent printing, Windows selection or hardware dispatch. */
(()=>{'use strict';const urls=new Set();window.addEventListener('pagehide',()=>{for(const url of urls)URL.revokeObjectURL(url);urls.clear();});
function node(tag,text){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;}
function mount({saleId,refundId=null,csrf,container}){
const section=node('section'),controls=node('div'),message=node('p'),links=node('div');section.className='pos-receipt-print';controls.className='pos-modal-actions';message.setAttribute('role','status');message.className='pos-receipt-message';links.className='pos-modal-actions';
const choice=node('select');choice.setAttribute('aria-label',refundId?'Refund receipt format':'Receipt format');for(const [value,label] of [['80','80 mm receipt'],['58','58 mm receipt'],['A4','A4 invoice']]){const option=node('option',label);option.value=value;choice.append(option);}choice.value='80';
const primary=node('button',refundId?'Print Refund Receipt (80 mm)':'Print Receipt (80 mm)'),other=node('button','Preview selected format');primary.type=other.type='button';primary.className='btn btn-primary pos-print-receipt';other.className='btn btn-secondary';
async function preview(profile){
// Reserve the window synchronously in the cashier's click; awaiting fetch first
// loses the browser user gesture. A blocked window still gets a clickable link.
let popup=null;try{popup=window.open('about:blank','_blank');if(popup){popup.opener=null;popup.document.title='Preparing PixiePinks receipt';popup.document.body.textContent='Preparing authorized receipt PDF…';}}catch(_){popup=null;}
primary.disabled=other.disabled=true;links.replaceChildren();message.textContent='Preparing receipt PDF…';
try{const response=await fetch('/crm/pos/api/sales/'+encodeURIComponent(saleId)+'/receipt/pdf',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({profile,refund_id:refundId})});
if(!response.ok){let detail='Receipt PDF could not be prepared. Check your connection and try again.';try{const error=await response.json();if(typeof error.detail==='string')detail=error.detail;}catch(_){}throw Error(detail);}
const blob=await response.blob();if(!response.headers.get('content-type')?.startsWith('application/pdf')||await blob.slice(0,5).text()!=='%PDF-')throw Error('The server did not return a valid receipt PDF. Sign in again or retry.');
const url=URL.createObjectURL(blob);urls.add(url);links.replaceChildren();const open=node('a','Open receipt PDF');open.href=url;open.target='_blank';open.rel='noopener';open.className='btn btn-secondary';const download=node('a','Download receipt PDF');download.href=url;download.download='pixiepinks-'+(refundId?'refund-':'')+'receipt-'+profile+'.pdf';download.className='btn btn-secondary';links.append(open,download);
let opened=false;try{if(popup&&!popup.closed){popup.location.replace(url);opened=true;}}catch(_){}
message.textContent=opened?'Receipt PDF opened. Physical printing is not confirmed.':'Receipt PDF ready. If the preview was blocked or closed, use Open receipt PDF below.';
}catch(error){try{if(popup&&!popup.closed)popup.close();}catch(_){}message.textContent=error instanceof TypeError?'Receipt preview unavailable. Check your connection and retry from Sales History.':error.message||'Receipt PDF unavailable. Retry from Sales History.';}
finally{primary.disabled=other.disabled=false;}}
primary.addEventListener('click',()=>preview('80'));other.addEventListener('click',()=>preview(choice.value));controls.append(primary,choice,other);section.append(controls,message,links,node('p','In Chrome, select POSPrinter POS-80 and Actual size / 100%. Previewing a PDF does not confirm printing.'));container.append(section);return section;}
window.PixiePinksReceiptPrint={mount};})();
