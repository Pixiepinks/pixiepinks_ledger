(()=>{'use strict';
window.QuestionProducts={attach(box,items,changed,settings={}){
  box.selectedProducts=(items||[]).map(item=>({...item}));
  box.productChoose=Boolean(settings.choose);
  box.choiceBody=settings.choice_body||'Which product do you like?';box.choiceButton=settings.choice_button||'Choose a product';
  const root=document.createElement('section');root.className='question-product-picker';root.setAttribute('aria-label','Selected products');
  const count=document.createElement('strong'),note=document.createElement('p'),selected=document.createElement('div'),query=document.createElement('input'),search=document.createElement('button'),results=document.createElement('div'),more=document.createElement('button'),status=document.createElement('p');
  query.placeholder='Search any product range, name, size or SKU';query.maxLength=100;query.setAttribute('aria-label','Search product catalogue');
  search.type=more.type='button';search.className=more.className='btn btn-secondary';search.textContent='Search catalogue';more.textContent='Load more products';more.hidden=true;status.setAttribute('role','status');
  note.textContent='Select 1–20 product variants. Live prices and stock are checked when sending. Each selected item sends one card; enable Continue below to send the next flow step. Otherwise staff continue.';
  const type=document.createElement('select');type.setAttribute('aria-label','Filter loaded product range');const initial=document.createElement('option');initial.value='';initial.textContent='All loaded product ranges';type.append(initial);status.textContent='Search the catalogue to select products.';
  const min=document.createElement('input'),max=document.createElement('input');for(const [el,label] of [[min,'Minimum price'],[max,'Maximum price']]){el.type='number';el.min='0';el.placeholder=label;el.setAttribute('aria-label',label);}
  const collectionQuery=document.createElement('input'),findCollections=document.createElement('button'),collection=document.createElement('select'),collectionStatus=document.createElement('p');
  collectionQuery.placeholder='Find a Shopify collection, e.g. Size 12';collectionQuery.maxLength=100;collectionQuery.setAttribute('aria-label','Search Shopify collections');
  findCollections.type='button';findCollections.className='btn btn-secondary';findCollections.textContent='Find collections';collection.setAttribute('aria-label','Shopify collection');
  const allProducts=document.createElement('option');allProducts.value='';allProducts.textContent='All products — no collection filter';collection.append(allProducts);collectionStatus.setAttribute('role','status');
  const choiceLabel=document.createElement('label'),choice=document.createElement('input');choice.type='checkbox';choice.checked=box.productChoose;choiceLabel.append(choice,document.createTextNode(' Ask customer to choose after the cards (up to 10 products)'));
  const wording=document.createElement('div'),bodyLabel=document.createElement('label'),body=document.createElement('textarea'),buttonLabel=document.createElement('label'),opener=document.createElement('input');
  bodyLabel.textContent='Product-choice message';body.maxLength=1024;body.value=box.choiceBody;bodyLabel.append(body);
  buttonLabel.textContent='Product-choice button';opener.maxLength=20;opener.value=box.choiceButton;buttonLabel.append(opener);wording.append(bodyLabel,buttonLabel);
  const limit=()=>box.productChoose?10:20;
  choice.onchange=()=>{box.productChoose=choice.checked;update();changed();};
  body.oninput=()=>{box.choiceBody=body.value;changed();};opener.oninput=()=>{box.choiceButton=opener.value;changed();};
  const service=window.QuestionGreasing.attach(box,settings.service,changed);
  root.append(choiceLabel,wording,service,count,note,selected,collectionQuery,findCollections,collection,collectionStatus,query,search,type,min,max,status,results,more);let products=[],cursor=null,generation=0,collectionGeneration=0;
  async function find(){
    const current=++collectionGeneration;collectionStatus.textContent='Loading Shopify collections…';
    try{const response=await fetch('/crm/quick-messages/customer-questions/collections?q='+encodeURIComponent(collectionQuery.value));const data=await response.json();if(current!==collectionGeneration||!box.isConnected)return;if(!response.ok)throw Error(data.detail||'Collections unavailable');
      const selectedOption=[...collection.options].find(option=>option.value===collection.value),value=collection.value;collection.replaceChildren(allProducts);
      for(const row of data.collections){const option=document.createElement('option');option.value=row.id;option.textContent=row.title;collection.append(option);}
      if(value&&![...collection.options].some(option=>option.value===value))collection.append(selectedOption);collection.value=value;
      collectionStatus.textContent=data.collections.length?'Choose a collection to browse its available products.':'No matching collections. Try another name.';
    }catch(e){if(current===collectionGeneration)collectionStatus.textContent=e.message;}
  }
  findCollections.onclick=find;collectionQuery.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();find();}};
  collection.onchange=()=>{type.value='';query.value='';min.value='';max.value='';load();};
  const pair=item=>item.product_id+'|'+item.variant_id;
  const button=(text,fn)=>{const el=document.createElement('button');el.type='button';el.className='btn btn-secondary';el.textContent=text;el.onclick=fn;return el;};
  function update(){
    count.textContent=`Selected: ${box.selectedProducts.length}/${limit()}`;selected.replaceChildren();
    service.sync();wording.hidden=!box.productChoose;for(const field of [body,opener]){field.disabled=!box.productChoose;field.required=box.productChoose;}
    choice.setCustomValidity(box.productChoose&&box.selectedProducts.length>10?'Choose 10 or fewer products for a selection list.':'');
    note.textContent=box.productChoose?'Cards are numbered in this order. A matching list follows confirmed sends; the customer’s choice is saved for staff. Live prices and stock are checked before sending.':'Select 1–20 product variants. Live prices and stock are checked when sending. Each selected item sends one card; enable Continue below to send the next flow step. Otherwise staff continue.';
    box.selectedProducts.forEach((item,index)=>{const row=document.createElement('div');row.className='selected-product';const label=document.createElement('span');label.textContent=`${index+1}. ${item.label}`;row.append(label);
      const swap=delta=>{const next=index+delta;[box.selectedProducts[index],box.selectedProducts[next]]=[box.selectedProducts[next],box.selectedProducts[index]];update();changed();};
      const up=button('↑',()=>swap(-1)),down=button('↓',()=>swap(1));up.setAttribute('aria-label','Move product up');down.setAttribute('aria-label','Move product down');up.disabled=index===0;down.disabled=index===box.selectedProducts.length-1;
      row.append(up,down,button('Remove',()=>{box.selectedProducts.splice(index,1);update();draw();changed();}));selected.append(row);
    });
  }
  function draw(){
    results.replaceChildren();
    for(const product of products){
      if(type.value&&product.product_type!==type.value)continue;
      const variants=product.variants.filter(v=>(!min.value||Number(v.price)>=Number(min.value))&&(!max.value||Number(v.price)<=Number(max.value)));
      if(!variants.length)continue;
      const row=document.createElement('article');row.className='picker-product';const label=document.createElement('label'),check=document.createElement('input'),text=document.createElement('span'),variant=document.createElement('select');check.type='checkbox';text.textContent=product.title;label.append(check,text);
      variant.setAttribute('aria-label','Product variant for '+product.title);
      for(const v of variants){const option=document.createElement('option');option.value=v.id;option.textContent=`${v.title} · Rs. ${Number(v.price).toLocaleString()}${v.available?'':' · Unavailable'}`;option.disabled=!v.available;variant.append(option);}
      variant.value=(variants.find(v=>box.selectedProducts.some(item=>item.product_id===product.id&&item.variant_id===v.id))||variants.find(v=>v.available)||variants[0]).id;
      const selection=()=>({product_id:product.id,variant_id:variant.value,label:(product.title+' · '+variants.find(v=>v.id===variant.value).title).slice(0,200)});
      const sync=()=>{const item=selection();check.checked=box.selectedProducts.some(value=>pair(value)===pair(item));check.disabled=product.status!=='ACTIVE'||!product.published||!variants.find(v=>v.id===variant.value)?.available;};
      variant.onchange=sync;sync();check.onchange=()=>{const item=selection(),index=box.selectedProducts.findIndex(value=>pair(value)===pair(item));
        if(check.checked&&index<0){if(box.selectedProducts.length>=limit()){check.checked=false;status.textContent=`Select at most ${limit()} products.`;return;}box.selectedProducts.push(item);}else if(!check.checked&&index>=0)box.selectedProducts.splice(index,1);
        update();changed();};try{const url=new URL(product.image);if(url.protocol==='https:'&&url.hostname==='cdn.shopify.com'){const image=document.createElement('img');image.src=url.href;image.alt=product.title;image.loading='lazy';row.append(image);}}catch{}row.append(label,variant);results.append(row);
    }
  }
  async function load(append=false){
    const current=++generation;type.hidden=Boolean(collection.value);status.textContent='Loading catalogue…';if(!append){products=[];cursor=null;results.replaceChildren();more.hidden=true;}
    try{const response=await fetch('/crm/quick-messages/customer-questions/products?q='+encodeURIComponent(query.value)+(collection.value?'&collection_id='+encodeURIComponent(collection.value):'')+(append&&cursor?'&cursor='+encodeURIComponent(cursor):''));const data=await response.json();if(current!==generation||!box.isConnected)return;if(!response.ok)throw Error(data.detail||'Catalogue unavailable');
      products=append?[...products,...data.products]:data.products;cursor=data.next_cursor;more.hidden=!data.has_next_page;
      const old=type.value;type.replaceChildren();const all=document.createElement('option');all.value='';all.textContent='All loaded product ranges';type.append(all);
      for(const value of new Set(products.map(p=>p.product_type).filter(Boolean))){const option=document.createElement('option');option.value=value;option.textContent=value;type.append(option);}type.value=[...type.options].some(o=>o.value===old)?old:'';
      draw();status.textContent=data.collection?`${data.collection.title}: ${data.products.length} matching available products. ${data.excluded_products||0} unavailable/unpublished products excluded. Tick only the products you want.`:'Tick products to select. Range and price filters apply to loaded results.';
    }catch(e){if(current===generation)status.textContent=e.message;}
  }
  query.oninput=()=>{generation++;cursor=null;more.hidden=true;};
  search.onclick=()=>load();more.onclick=()=>load(true);query.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();load();}};
  for(const el of [type,min,max])el.oninput=draw;
  update();return root;
}};
})();
