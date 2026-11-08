(()=>{'use strict';const root=document.querySelector('#question-builder');if(!root)return;
const form=root.querySelector('form'),steps=root.querySelector('#question-steps'),feedback=root.querySelector('#question-feedback');let version=0,csrf=root.dataset.csrf,busy=false;
function field(box,label,key,value,max,tag='input'){
  const wrap=document.createElement('label');wrap.append(document.createTextNode(label));
  const el=document.createElement(tag);el.value=value;el.required=true;if(max)el.maxLength=max;el.dataset.field=key;wrap.append(el);box.append(wrap);return el;
}
function add(step){
  const box=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent='Question';box.append(legend);
  field(box,'Answer key','key',step.key,30);
  field(box,'Question text','body',step.body,1024,'textarea');
  const type=field(box,'Answer format','type','',null,'select');
  for(const [value,label] of [['buttons','Buttons — up to 3 options'],['list','List — up to 10 options']]){const option=document.createElement('option');option.value=value;option.textContent=label;type.append(option);}type.value=step.type||'buttons';
  const listButton=field(box,'List button text','list_button',step.list_button||'Choose option',20);
  const options=field(box,'Option labels (one per line)','options',step.options.join('\n'),null,'textarea');
  const help=document.createElement('small');box.append(help);
  const preview=document.createElement('div');preview.className='question-preview';box.append(preview);
  const render=()=>{
    const isList=type.value==='list',count=isList?10:3,limit=isList?24:20;
    listButton.parentElement.hidden=!isList;listButton.required=isList;listButton.disabled=!isList;
    help.textContent=`${isList?'List':'Buttons'}: 1–${count} options, up to ${limit} characters each.`;
    const labels=options.value.split('\n');
    options.setCustomValidity(labels.length>count||labels.some(label=>!label.trim()||label!==label.trim()||label.length>limit)||new Set(labels).size!==labels.length?`Enter 1–${count} different options, up to ${limit} characters each, without blank lines.`:'');
    preview.replaceChildren();const body=document.createElement('p');body.textContent=box.querySelector('[data-field=body]').value;preview.append(body);
    if(isList){const opener=document.createElement('strong');opener.textContent=listButton.value;preview.append(opener);const note=document.createElement('small');note.textContent='Customers tap this button to open the list below.';preview.append(note);}
    for(const value of labels){const badge=document.createElement('span');badge.textContent=value;preview.append(badge);}
  };
  box.addEventListener('input',render);box.addEventListener('change',render);
  const remove=document.createElement('button');remove.type='button';remove.className='btn btn-secondary';remove.textContent='Remove question';remove.onclick=()=>box.remove();box.append(remove);steps.append(box);render();
}
async function request(data){const response=await fetch(location.pathname+'/api',{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:data?JSON.stringify(data):undefined});const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Unable to save questions');return result;}
request().then(data=>{version=data.version;csrf=data.csrf_token;root.querySelector('#questions-enabled').checked=data.enabled;root.querySelector('#questions-title').value=data.definition.title;data.definition.steps.forEach(add);}).catch(e=>feedback.textContent=e.message);
root.querySelector('#question-add').onclick=()=>{if(steps.children.length<10)add({key:'question_'+(steps.children.length+1),body:'',options:['Yes','No']});};
form.onsubmit=async event=>{
  event.preventDefault();if(busy)return;busy=true;const submit=form.querySelector('[type=submit]');submit.disabled=true;feedback.textContent='Saving…';
  try{
    const definition={title:root.querySelector('#questions-title').value,steps:[...steps.children].map(box=>{
      const value=key=>box.querySelector(`[data-field=${key}]`).value;
      return {key:value('key'),body:value('body'),type:value('type'),options:value('options').split('\n'),...(value('type')==='list'?{list_button:value('list_button')}:{})};
    })};
    const data=await request({version,enabled:root.querySelector('#questions-enabled').checked,definition});version=data.version;feedback.textContent='Saved. Existing sequences keep their original questions.';
  }catch(e){feedback.textContent=e.message;}finally{busy=false;submit.disabled=false;}
};
})();
