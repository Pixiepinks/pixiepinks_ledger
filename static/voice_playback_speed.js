(() => {
  'use strict';
  const messages=document.getElementById('messages');if(!messages)return;
  const speeds=[1,1.5,2,3],key='pixiepinks.inbox.voicePlaybackSpeed';
  const controls=new WeakMap();let speed=1;
  try{const saved=Number(localStorage.getItem(key));if(speeds.includes(saved))speed=saved;}catch(_){}
  const label=value=>`${value}×`;
  function apply(audio){
    audio.defaultPlaybackRate=speed;audio.playbackRate=speed;
    if('preservesPitch' in audio)audio.preservesPitch=true;
    const button=controls.get(audio);if(!button)return;
    button.textContent=label(speed);
    const next=speeds[(speeds.indexOf(speed)+1)%speeds.length];
    button.setAttribute('aria-label',`Playback speed ${label(speed)}. Change to ${label(next)}`);
    button.title=`Playback speed: ${label(speed)}. Click for ${label(next)}`;
  }
  function enhance(audio){
    if(controls.has(audio)||!messages.contains(audio))return;
    const row=document.createElement('div');row.className='voice-playback-row';
    const button=document.createElement('button');button.type='button';button.className='voice-playback-speed';
    controls.set(audio,button);audio.before(row);row.append(audio,button);apply(audio);
    audio.addEventListener('loadedmetadata',()=>apply(audio));
    button.addEventListener('click',()=>{
      speed=speeds[(speeds.indexOf(speed)+1)%speeds.length];
      try{localStorage.setItem(key,String(speed));}catch(_){}
      messages.querySelectorAll('audio').forEach(apply);
    });
  }
  messages.querySelectorAll('audio').forEach(enhance);
  new MutationObserver(records=>{
    for(const record of records)for(const node of record.addedNodes){
      if(node.nodeType!==Node.ELEMENT_NODE)continue;
      if(node.matches('audio'))enhance(node);
      node.querySelectorAll('audio').forEach(enhance);
    }
  }).observe(messages,{childList:true,subtree:true});
})();
