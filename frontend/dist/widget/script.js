const $=id=>document.getElementById(id);
const widget=$('widget'),cover=$('cover');
let state={progress_ms:0,duration_ms:0,playing:false},received=performance.now(),image='';
const time=ms=>{const s=Math.floor(Math.max(0,ms)/1000);return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`};
function scrollText(){document.querySelectorAll('.scroll-text').forEach(el=>{const distance=Math.max(0,el.scrollWidth-el.parentElement.clientWidth);el.classList.toggle('scrolling',distance>1);el.style.setProperty('--scroll-distance',`${distance}px`);el.style.setProperty('--scroll-duration',`${Math.max(5,Math.min(18,distance/30))}s`);});}
function palette(rgb){
 const max=Math.max(...rgb); const normalized=max<45?rgb.map(c=>c+45-max):rgb;
 const color=factor=>`rgb(${normalized.map(c=>Math.round(c*factor)).join(',')})`;
 widget.style.setProperty('--cover-start',color(.62));widget.style.setProperty('--cover-end',color(.28));
 widget.style.setProperty('--cover-accent',`rgb(${normalized.map(c=>Math.round(c*.45+140)).join(',')})`);
}
function fallback(){palette([160,75,220]);}
function colors(url){const sample=new Image();sample.crossOrigin='anonymous';sample.onload=()=>{if(url!==image)return;try{
 const canvas=document.createElement('canvas');canvas.width=canvas.height=40;const ctx=canvas.getContext('2d',{willReadFrequently:true});
 const size=Math.min(sample.naturalWidth,sample.naturalHeight);ctx.drawImage(sample,(sample.naturalWidth-size)/2,(sample.naturalHeight-size)/2,size,size,0,0,40,40);
 const pixels=ctx.getImageData(0,0,40,40).data,bins=new Map();
 for(let i=0;i<pixels.length;i+=4){if(pixels[i+3]<128)continue;const rgb=[pixels[i],pixels[i+1],pixels[i+2]],max=Math.max(...rgb),min=Math.min(...rgb);if(max<25||min>235)continue;
 const key=rgb.map(c=>c>>5).join(',');const bin=bins.get(key)||{sum:[0,0,0],weight:0,count:0};const weight=1+(max-min)/128;bin.weight+=weight;bin.count++;rgb.forEach((c,k)=>bin.sum[k]+=c);bins.set(key,bin);}
 const best=[...bins.values()].sort((a,b)=>b.weight-a.weight)[0];if(best)palette(best.sum.map(c=>c/best.count));else fallback();
 }catch{fallback();}};sample.onerror=()=>{if(url===image)fallback()};sample.src=url;}
function apply(data){state=data;received=performance.now();$('title').textContent=data.title||'Kass';$('artist').textContent=(data.artist||'Artista sconosciuto')+(data.track_id&&!data.playing?' · In pausa':'');const next=data.image||'/kass.svg';if(image!==next){image=next;cover.src=next;if(next==='/kass.svg')fallback();else colors(next);}cover.style.display='block';scrollText();update();}
cover.onerror=()=>{if(cover.getAttribute('src')!=='/kass.svg'){image='/kass.svg';cover.src=image;}fallback();};
function update(){const elapsed=state.playing?performance.now()-received:0;const progress=Math.min(state.duration_ms||0,(state.progress_ms||0)+elapsed);$('currentTime').textContent=time(progress);$('totalTime').textContent=time(state.duration_ms||0);$('progress-fill').style.width=`${state.duration_ms?Math.min(100,progress/state.duration_ms*100):0}%`;}
async function poll(){try{const response=await fetch('/api/now-playing',{cache:'no-store'});if(!response.ok)throw Error();apply(await response.json());}catch{apply({title:'Kass non disponibile',artist:'Apri Kass per mostrare la tua musica',image:'/kass.svg',playing:false,progress_ms:0,duration_ms:0});}finally{setTimeout(poll,1500);}}
window.addEventListener('resize',scrollText);if(window.ResizeObserver)new ResizeObserver(scrollText).observe(widget);
setInterval(update,250);fallback();poll();
