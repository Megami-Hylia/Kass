// Optional OBS audio: the browser source owns this MP3 playback.
(()=>{
 // Audio enabled by default for the sole Kass widget.
 const audio=document.createElement('audio');audio.preload='auto';audio.id='kass-obs-audio';document.body.append(audio);
 let latest=null,track=null,request=0,busy=false;
 const status=document.createElement('span');status.style.cssText='position:fixed;bottom:5px;left:5px;color:#fff;background:#491f65;padding:4px;font:12px Arial;display:none';document.body.append(status);
 function error(message){status.textContent=message;status.style.display='block'}
 function sync(){if(!latest||audio.readyState<1)return;audio.volume=latest.volume??1;const target=Math.min(latest.duration_ms/1000,latest.progress_ms/1000+(latest.playing?(performance.now()-latest.received)/1000:0));if(Number.isFinite(target)&&Math.abs(audio.currentTime-target)>.6)audio.currentTime=target;
 if(latest.playing){audio.play().then(()=>status.style.display='none').catch(()=>error('Audio OBS bloccato: Interagisci → clicca per abilitare'));}else audio.pause();}
 audio.addEventListener('loadedmetadata',sync);audio.addEventListener('error',()=>error('Audio non disponibile: riapri Kass'));
 document.addEventListener('click',sync);
 async function poll(){if(busy)return;busy=true;const id=++request;try{const response=await fetch('/api/now-playing',{cache:'no-store',signal:AbortSignal.timeout(3000)});if(!response.ok)throw Error();const data=await response.json();if(id!==request)return;latest={...data,received:performance.now()};if(!data.track_id){track=null;audio.pause();audio.removeAttribute('src');audio.load();status.style.display='none';return}if(track!==data.track_id){track=data.track_id;audio.pause();audio.src=`/api/tracks/${encodeURIComponent(track)}/audio?normalize=true`;audio.load()}sync();}catch{latest=null;audio.pause()}finally{busy=false}}
 window.addEventListener('pagehide',()=>audio.pause());poll();setInterval(poll,500);
})();
