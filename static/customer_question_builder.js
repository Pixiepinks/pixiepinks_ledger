(()=>{'use strict';const root=document.querySelector('#question-builder');if(!root)return;
const form=root.querySelector('form'),steps=root.querySelector('#question-steps'),feedback=root.querySelector('#question-feedback');let version=0,csrf=root.dataset.csrf,busy=false,flowId=1,loaded=null;
function field(box,label,key,value,max,tag='input'){
  const wrap=document.createElement('label');wrap.append(document.createTextNode(label));
  const el=document.createElement(tag);el.value=value;el.required=true;if(max)el.maxLength=max;el.dataset.field=key;wrap.append(el);box.append(wrap);return el;
}
function boxes(){return [...steps.children];}
function refreshRoutes(){
  const all=boxes();
  for(const box of all){
    box.actionPanel?.sources();
    const area=box.querySelector('.question-routes');if(['products','voice','details','checkout','text'].includes(box.querySelector('[data-field=type]').value)){area.replaceChildren();continue;}const labels=box.querySelector('[data-field=options]').value.split('\n');area.replaceChildren();
    for(const label of labels){
      const current=box.routes.get(label)??'__next';
      const select=field(area,`If “${label}”, next step`,'route','',null,'select');
      select.dataset.answer=label;
      const choices=[['__next','Next question in order'],['__finish','Finish — staff continues'],...all.filter(item=>item!==box).map(item=>{const key=item.querySelector('[data-field=key]').value;return [key,key||'(Question needs a key)'];})];
      if(!choices.some(([value])=>value===current))choices.push([current,`Missing question: ${current}`]);
      for(const [value,text] of choices){const option=document.createElement('option');option.value=value;option.textContent=text;select.append(option);}select.value=current;
      select.onchange=()=>{box.routes.set(label,select.value);previewFlow();};
    }
  }
  previewFlow();
}
function definition(){return {title:root.querySelector('#questions-title').value,steps:boxes().map((box,index,all)=>{
  const value=key=>box.querySelector(`[data-field=${key}]`).value,options=value('options').split('\n');
  if(['voice','details','checkout','text'].includes(value('type')))return {key:value('key'),type:value('type'),...(['details','text'].includes(value('type'))?{body:value('body')}:{}),...(value('type')==='text'?{}:box.actionPanel.value(value('type')))};
  if(value('type')==='products')return {key:value('key'),type:'products',products:box.selectedProducts,...(box.querySelector('[data-field=continue_flow]').checked?{continue_flow:true}:{}),...(box.productChoose?{choose:true,choice_body:box.choiceBody,choice_button:box.choiceButton,...(box.serviceEnabled?{service:box.serviceConfig}:{})}:{})};
  return {key:value('key'),body:value('body'),type:value('type'),options,
    next:options.map(label=>{const target=box.routes.get(label)||'__next';return target==='__finish'?null:target==='__next'?(all[index+1]?.querySelector('[data-field=key]').value??null):target;}),
    ...(value('type')==='list'?{list_button:value('list_button')}:{})};})};}
function previewFlow(){
  const area=root.querySelector('#question-flow-preview');area.replaceChildren();const data=definition().steps;
  if(!data.length){area.textContent='Add a first question to start the flow.';return;}
  const entry=document.createElement('strong');entry.textContent=`Start → ${data[0].key||'(first question)'}`;area.append(entry);
  for(const step of data){const group=document.createElement('div');const title=document.createElement('b');title.textContent=step.key||'(Question needs a key)';group.append(title);
    if(['voice','details','checkout','text'].includes(step.type)){const row=document.createElement('p');row.textContent=step.type==='voice'?`Send Saved Voice: ${step.voice_title||'Choose recording'} → next step`:step.type==='text'?'Ask typed question → wait for answer → next step':step.type==='details'?`Send ${step.rows.length} detail rows → next step`:`Collect delivery details → confirmation${step.payment?' → payment voice → bank image → deposit slip → staff verification':''}`;group.append(row);area.append(group);continue;}
    if(step.type==='products'){const row=document.createElement('p');row.textContent=`Send ${step.products.length} selected products → ${step.choose?(step.service?'Customer chooses → Saved Voice → Greasing Yes/No → '+(step.service.collect_delivery?'Delivery details → Confirm / Edit → '+(step.service.payment?'Payment voice → Bank image → Deposit slip → Staff verification':'Staff invoice'):'Staff continues'):(step.continue_flow?'Numbered list → Customer chooses → Next step':'Numbered list → Customer chooses → Staff continues')):(step.continue_flow?'Next step (single product is selected automatically)':'Finish — staff continues')}`;group.append(row);area.append(group);continue;}
    step.options.forEach((label,i)=>{const row=document.createElement('p');row.textContent=`${label} → ${step.next[i]===null?'Finish — staff continues':step.next[i]}`;group.append(row);});area.append(group);}
}
function add(step){
  const box=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent='Question';box.append(legend);
  field(box,'Answer key','key',step.key,30);
  field(box,'Question text','body',step.body||'',1024,'textarea');
  const type=field(box,'Answer format','type','',null,'select');
  for(const [value,label] of [['buttons','Buttons — up to 3 options'],['list','List — up to 10 options'],['products','Send selected products — up to 20'],['voice','Send Saved Voice'],['details','Send product details'],['checkout','Collect order details / checkout'],['text','Ask typed question']]){const option=document.createElement('option');option.value=value;option.textContent=label;type.append(option);}type.value=step.type||'buttons';
  const listButton=field(box,'List button text','list_button',step.list_button||'Choose option',20);
  const options=field(box,'Option labels (one per line)','options',(step.options||[]).join('\n'),null,'textarea');
  box.previousLabels=[...(step.options||[])];
  box.routes=new Map((step.options||[]).map((label,index)=>[label,step.next?(step.next[index]===null?'__finish':step.next[index]):'__next']));
  const productPicker=window.QuestionProducts.attach(box,step.products,()=>previewFlow(),step);const productFields=document.createElement('fieldset');productFields.append(productPicker);box.append(productFields);
  const continueLabel=document.createElement('label'),continueFlow=document.createElement('input');continueFlow.type='checkbox';continueFlow.dataset.field='continue_flow';continueFlow.checked=step.continue_flow===true;continueLabel.append(continueFlow,document.createTextNode(' Continue to the next step after products / customer choice'));box.append(continueLabel);
  box.actionPanel=window.QuestionFlowActions.attach(box,step,()=>previewFlow());box.append(box.actionPanel);
  const routes=document.createElement('div');routes.className='question-routes';box.append(routes);
  const help=document.createElement('small');box.append(help);
  const preview=document.createElement('div');preview.className='question-preview';box.append(preview);
  const render=()=>{
    const isProducts=type.value==='products',isAction=['voice','details','checkout','text'].includes(type.value),isQuestion=!isProducts&&!isAction;legend.textContent=isProducts?'Product step':isAction?'Flow action':'Question';productPicker.hidden=!isProducts;productFields.hidden=!isProducts;productFields.disabled=!isProducts;continueLabel.hidden=!isProducts;box.actionPanel.sync(type.value);
    for(const field of [box.querySelector('[data-field=body]'),options]){const visible=isQuestion||(field.dataset.field==='body'&&['details','text'].includes(type.value));field.parentElement.hidden=!visible;field.required=visible;field.disabled=!visible;}
    routes.hidden=!isQuestion;preview.hidden=!isQuestion;help.hidden=!isQuestion;
    const isList=type.value==='list',count=isList?10:3,limit=isList?24:20;
    listButton.parentElement.hidden=!isList;listButton.required=isList;listButton.disabled=!isList;
    help.textContent=`${isList?'List':'Buttons'}: 1–${count} options, up to ${limit} characters each.`;
    const labels=options.value.split('\n');
    options.setCustomValidity(isQuestion&&(labels.length>count||labels.some(label=>!label.trim()||label!==label.trim()||label.length>limit)||new Set(labels).size!==labels.length)?`Enter 1–${count} different options, up to ${limit} characters each, without blank lines.`:'');
    preview.replaceChildren();const body=document.createElement('p');body.textContent=box.querySelector('[data-field=body]').value;preview.append(body);
    if(isList){const opener=document.createElement('strong');opener.textContent=listButton.value;preview.append(opener);const note=document.createElement('small');note.textContent='Customers tap this button to open the list below.';preview.append(note);}
    for(const value of labels){const badge=document.createElement('span');badge.textContent=value;preview.append(badge);}
  };
  box.addEventListener('input',event=>{
    if(event.target.dataset.field==='options'){
      const labels=options.value.split('\n');
      if(labels.length===box.previousLabels.length){
        const old=new Map(box.routes);
        labels.forEach((label,i)=>{if(!old.has(label)&&old.has(box.previousLabels[i]))box.routes.set(label,old.get(box.previousLabels[i]));});
      }
      box.previousLabels=labels;
    }
    render();if(['key','options','type'].includes(event.target.dataset.field))refreshRoutes();
  });
  box.addEventListener('change',event=>{render();if(event.target===type)refreshRoutes();});
  for(const [label,direction] of [['Move up',-1],['Move down',1]]){const move=document.createElement('button');move.type='button';move.className='btn btn-secondary';move.textContent=label;move.onclick=()=>{const neighbor=direction<0?box.previousElementSibling:box.nextElementSibling;if(neighbor){steps.insertBefore(direction<0?box:neighbor,direction<0?neighbor:box);refreshRoutes();}};box.append(move);}
  const remove=document.createElement('button');remove.type='button';remove.className='btn btn-secondary';remove.textContent='Remove question';remove.onclick=()=>{box.remove();refreshRoutes();};box.append(remove);steps.append(box);render();refreshRoutes();
}
async function request(data){const response=await fetch(location.pathname+'/api',{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:data?JSON.stringify(data):undefined});const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Unable to save questions');return result;}
const picker=root.querySelector('#question-flow-select');
function dirty(){return loaded&&(JSON.stringify(definition())!==loaded.flow||root.querySelector('#questions-enabled').checked!==loaded.enabled);}
async function load(id=flowId){
  const response=await fetch(location.pathname+'/api?flow_id='+id),data=await response.json();if(!response.ok)throw Error(data.detail||'Unable to load flow');
  flowId=data.flow.id;version=data.version;csrf=data.csrf_token;root.querySelector('#questions-enabled').checked=data.enabled;root.querySelector('#questions-title').value=data.definition.title;
  steps.replaceChildren();data.definition.steps.forEach(add);picker.replaceChildren();
  for(const flow of data.flows){const option=document.createElement('option');option.value=flow.id;option.textContent=flow.title+(flow.archived?' (Archived)':'');picker.append(option);}picker.value=flowId;
  root.querySelector('#question-edit-fields').disabled=data.flow.archived;
  root.querySelector('#question-archive').textContent=data.flow.archived?'Restore flow':'Archive flow';
  root.querySelector('#question-archive').dataset.action=data.flow.archived?'restore':'archive';
  root.querySelector('#question-library-note').textContent=data.flow.archived?'Archived: unavailable for new customer runs. Existing runs continue.':'Edit this flow or select another. New customers start only when staff explicitly choose Start in the Inbox.';
  loaded={flow:JSON.stringify(definition()),enabled:data.enabled};
}
load().catch(e=>feedback.textContent=e.message);
picker.onchange=async()=>{if(busy){picker.value=flowId;return;}if(dirty()&&!confirm('Discard unsaved flow edits?')){picker.value=flowId;return;}try{await load(Number(picker.value));feedback.textContent='';}catch(e){feedback.textContent=e.message;}};
async function libraryAction(action){
  if(busy)return;
  if(action!=='toggle'&&dirty()&&!confirm('Discard unsaved flow edits?'))return;
  busy=true;feedback.textContent='Saving…';
  try{
    const payload={action,version,flow_id:flowId,title:root.querySelector('#question-new-title').value,enabled:root.querySelector('#questions-enabled').checked};
    const response=await fetch(location.pathname+'/flows',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(payload)});
    const result=await response.json();if(!response.ok)throw Error(result.detail||'Unable to update flow library');
    if(action==='toggle'){version=result.version;loaded.enabled=root.querySelector('#questions-enabled').checked;}else await load(result.flow_id||flowId);feedback.textContent='Flow library updated.';
  }catch(e){feedback.textContent=e.message;}finally{busy=false;}
}
root.querySelector('#question-create').onclick=()=>libraryAction('create');
root.querySelector('#question-duplicate').onclick=()=>libraryAction('duplicate');
root.querySelector('#question-archive').onclick=event=>libraryAction(event.target.dataset.action);
root.querySelector('#question-toggle').onclick=()=>libraryAction('toggle');
root.querySelector('#question-add').onclick=()=>{if(steps.children.length<50){let i=steps.children.length+1;while(boxes().some(box=>box.querySelector('[data-field=key]').value==='question_'+i))i++;add({key:'question_'+i,body:'',options:['Yes','No']});}};
form.onsubmit=async event=>{
  event.preventDefault();if(busy)return;busy=true;const submit=form.querySelector('[type=submit]');submit.disabled=true;feedback.textContent='Saving…';
  try{
    const flow=definition();
    const data=await request({version,enabled:root.querySelector('#questions-enabled').checked,definition:flow,flow_id:flowId});version=data.version;loaded={flow:JSON.stringify(flow),enabled:root.querySelector('#questions-enabled').checked};feedback.textContent='Saved. Existing sequences keep their original questions.';
  }catch(e){feedback.textContent=e.message;}finally{busy=false;submit.disabled=false;}
};
})();
