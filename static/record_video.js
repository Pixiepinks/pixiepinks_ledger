(() => {
  'use strict';
  const root=document.getElementById('inbox'), dialog=document.getElementById('record-video-dialog');
  if(!root || !dialog)return;
  const byId=id=>document.getElementById('record-video-'+id), live=byId('live'), playback=byId('playback'), status=byId('status');
  let stream, recorder, chunks=[], recorded, url, timer, started, bytes=0, target, generation=0, discard=false, limits;
  const say=text=>status.textContent=text;
  const current=()=>Number(root.dataset.activeConversation || root.dataset.initial) || null;
  function tracksOff(){stream?.getTracks().forEach(track=>track.stop());stream=null;live.srcObject=null;clearInterval(timer);timer=null;}
  function cleanup(){
    generation++; discard=true;
    if(recorder && recorder.state!=='inactive')recorder.stop();
    recorder=null;tracksOff();
    if(url)URL.revokeObjectURL(url);url=null;recorded=null;chunks=[];bytes=0;
    playback.pause();playback.removeAttribute('src');playback.load();
  }
  function buttons(mode){
    byId('start').hidden=mode!=='ready';byId('stop').hidden=mode!=='recording';
    byId('retake').hidden=mode!=='preview';byId('use').hidden=mode!=='preview';
    live.hidden=mode==='preview';playback.hidden=mode!=='preview';
  }
  function stop(){if(recorder?.state==='recording'){say('Preparing your recording…');recorder.stop();}tracksOff();}
  function reset(){cleanup();discard=false;buttons('ready');say('Tap Start recording to allow camera and microphone access.');}
  async function begin(){
    if(recorder?.state==='recording')return;
    const ticket=++generation; discard=false;buttons('waiting');say('Waiting for camera and microphone permission…');
    try{
      if(!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder)throw new Error('Use current Android Chrome over HTTPS to record video.');
      const candidates=['video/mp4;codecs=avc1.42E01E,mp4a.40.2','video/mp4','video/webm;codecs=vp8,opus','video/webm'];
      const mime=candidates.find(value=>MediaRecorder.isTypeSupported(value));
      if(!mime)throw new Error('Video recording is unsupported here. Use Videos to choose a smaller saved clip.');
      const captured=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1280,max:1280},height:{ideal:720,max:720},frameRate:{ideal:24,max:30}},audio:true});
      if(ticket!==generation || !dialog.open || current()!==target){captured.getTracks().forEach(t=>t.stop());return;}
      stream=captured;live.srcObject=stream;live.play().catch(()=>{});
      chunks=[];bytes=0;
      const local=new MediaRecorder(stream,{mimeType:mime,videoBitsPerSecond:limits.video_bps,audioBitsPerSecond:limits.audio_bps});recorder=local;
      local.ondataavailable=event=>{
        if(ticket!==generation || discard || !event.data.size)return;
        bytes+=event.data.size;chunks.push(event.data);
        // Encoder bitrates are advisory; reserve room for the final chunk/container.
        if(bytes>=limits.max_bytes*.85 && local.state==='recording')stop();
      };
      local.onerror=()=>{if(ticket===generation){cleanup();buttons('ready');say('Recording failed. Please try a shorter clip.');}};
      local.onstop=()=>{
        if(ticket!==generation)return;
        tracksOff();if(discard || !dialog.open || current()!==target)return;
        const blob=new Blob(chunks,{type:local.mimeType || mime});chunks=[];recorder=null;
        if(!blob.size || blob.size>limits.max_bytes){buttons('ready');say('Recording exceeded the size limit. Nothing was uploaded. Try a shorter clip.');return;}
        const extension=blob.type.includes('mp4')?'mp4':'webm';
        recorded=new File([blob],`recorded-video.${extension}`,{type:blob.type});
        url=URL.createObjectURL(recorded);playback.src=url;buttons('preview');
        say(`Ready to preview · ${(blob.size/1048576).toFixed(2)} MB. Nothing has been uploaded or sent.`);
      };
      local.start(250);started=performance.now();buttons('recording');
      timer=setInterval(()=>{
        const elapsed=(performance.now()-started)/1000;
        say(`Recording ${Math.floor(elapsed)}/${limits.max_seconds}s · ${(bytes/1048576).toFixed(2)} MB. Stops automatically.`);
        if(elapsed>=limits.max_seconds)stop();
      },100);
    }catch(error){if(ticket===generation){cleanup();buttons('ready');say(error.name==='NotAllowedError'?'Allow camera and microphone access in Chrome site settings, then try again.':error.name==='NotFoundError'?'No camera or microphone was found.':error.message || 'Camera unavailable. Try again.');}}
  }
  document.getElementById('record-video-open').onclick=async()=>{
    if(!current() || window.inboxMediaBusy)return;
    reset();limits=null;byId('start').disabled=true;target=current();document.getElementById('attachment-menu').hidden=true;
    byId('recipient').textContent='Customer: '+document.getElementById('chat-name').textContent;
    dialog.showModal();const ticket=generation;
    try{
      const response=await fetch('/crm/whatsapp/api/media-policy',{credentials:'same-origin',cache:'no-store',redirect:'error'});
      if(!response.ok)throw new Error('Sign in again and check your Inbox access.');
      const policy=await response.json();if(ticket!==generation || !dialog.open)return;
      limits=policy.video_recording;
      if(!limits)throw new Error('Reload the CRM to update video recording support.');
      say(`Ready · up to ${limits.max_seconds} seconds and ${(limits.max_bytes/1048576).toFixed(1)} MB. Tap Start recording.`);
      byId('start').disabled=false;
    }catch(error){if(ticket===generation && dialog.open){say(error.message);byId('start').disabled=true;}}
  };
  byId('start').onclick=()=>{if(limits)begin();};byId('stop').onclick=stop;
  byId('retake').onclick=reset;
  byId('use').onclick=()=>{
    if(!recorded || current()!==target)return;
    const detail={file:recorded,conversation:target,accepted:false};
    root.dispatchEvent(new CustomEvent('inbox-recorded-video',{detail}));
    if(detail.accepted)dialog.close();else say('The attachment composer is busy. Cancel and try again after the current attachment finishes.');
  };
  function closeRecorder(){cleanup();if(dialog.open)dialog.close();}
  byId('close').onclick=byId('cancel').onclick=closeRecorder;
  dialog.addEventListener('close',cleanup);dialog.addEventListener('cancel',cleanup);
  root.addEventListener('inbox-conversation',()=>{if(dialog.open)closeRecorder();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden && dialog.open){closeRecorder();}});
  window.addEventListener('pagehide',cleanup);
})();
