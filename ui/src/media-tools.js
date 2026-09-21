let auditionUrl = null;
let audioInspectGeneration = 0;
let auditionGeneration = 0;
function resetAudioInspection() {
  audioInspectGeneration++; auditionGeneration++;
  const player = document.getElementById('source-track-audition');
  player?.pause(); player?.removeAttribute('src'); player?.classList.add('hidden');
  if (auditionUrl) URL.revokeObjectURL(auditionUrl);
  auditionUrl = null;
  document.getElementById('source-track-list')?.replaceChildren();
}
async function inspectSourceTracks() {
  const source = selectedVideo, generation = ++audioInspectGeneration;
  const root = document.getElementById('source-track-list');
  if (!source) { root.textContent = 'Choose a video first.'; return; }
  root.textContent = 'Reading audio track names…';
  try {
    const res = await fetch(`${serverUrl}/media/audio-tracks`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:source})});
    const data = await res.json(); if (!res.ok) throw new Error(data.detail);
    if (generation !== audioInspectGeneration || source !== selectedVideo) return;
    root.replaceChildren();
    const useSpeech = selection => { document.getElementById('analysis-audio-tracks').value=selection; saveCurrentProjectSilently(); };
    const speechTracks = data.tracks.filter(track => /mic|chat|voice|commentary/i.test(track.name));
    if (speechTracks.length) {
      const recommended=document.createElement('button');recommended.className='btn btn-small';
      recommended.textContent='Use '+speechTracks.map(track=>track.name).join(' + ')+' for speech analysis';
      recommended.onclick=()=>{if(source===selectedVideo) useSpeech(speechTracks.map(track=>track.number).join(','));};root.append(recommended);
    }
    if (data.tracks.length > 1) {
      const exportAll = document.createElement('button'); exportAll.className='btn btn-small';
      exportAll.textContent='Export all named tracks (equal mix)';
      exportAll.onclick=()=>{if(source!==selectedVideo)return;document.getElementById('source-audio-tracks').value='all';document.getElementById('export-audio-gains').value='';saveCurrentProjectSilently();};
      root.append(exportAll);
    }
    if (!data.tracks.length) root.textContent='No audio streams found.';
    for (const track of data.tracks) {
      const row=document.createElement('div');row.className='audio-track-row';
      const label=document.createElement('span');label.textContent=`${track.number}: ${track.name} · ${track.channels || '?'} channels${track.default?' · default':''}`;row.append(label);
      const choose=document.createElement('button');choose.className='btn btn-small';choose.textContent='Use for speech';choose.onclick=()=>{if(source===selectedVideo)useSpeech(String(track.number));};row.append(choose);
      const preview=document.createElement('button');preview.className='btn btn-small';preview.textContent='Listen (12s)';
      preview.onclick=async()=>{
        const start=Number(document.getElementById('audio-preview-start').value);
        if (!Number.isFinite(start) || start<0) { showToast('Choose a valid preview time.','error');return; }
        const audition = ++auditionGeneration;
        preview.disabled=true;
        try {
          const response=await fetch(`${serverUrl}/media/audio-preview`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:source,track:track.number,start})});
          if(!response.ok)throw new Error((await response.json()).detail);
          const blob=await response.blob();
          if(generation!==audioInspectGeneration || audition!==auditionGeneration || source!==selectedVideo)return;
          if(auditionUrl)URL.revokeObjectURL(auditionUrl);
          auditionUrl=URL.createObjectURL(blob);const player=document.getElementById('source-track-audition');player.src=auditionUrl;player.classList.remove('hidden');await player.play();
        }catch(error){showToast(error.message,'error');}finally{preview.disabled=false;}
      };row.append(preview);root.append(row);
    }
  }catch(error){if(generation===audioInspectGeneration)root.textContent=error.message;}
}
async function refreshMediaCache(clear=false) {
  const label=document.getElementById('media-cache-status');
  const button=document.getElementById('media-cache-clear');button.disabled=true;
  try {
    const res=await fetch(`${serverUrl}/media/cache${clear?'/clear':''}`,clear?{method:'POST'}:{});
    const data=await res.json();if(!res.ok)throw new Error(data.detail);
    label.textContent=`${data.files} cache files · ${(data.bytes/1048576).toFixed(1)} MiB · ${data.path}${clear?' · Removed '+data.deleted+', busy/skipped '+data.skipped:''}`;
  }catch(error){label.textContent=error.message;}finally{button.disabled=false;}
}
document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('inspect-audio-tracks')?.addEventListener('click',inspectSourceTracks);
  document.getElementById('media-cache-refresh')?.addEventListener('click',()=>refreshMediaCache());
  document.getElementById('media-cache-clear')?.addEventListener('click',()=>refreshMediaCache(true));
  document.querySelector('.media-cache-panel')?.addEventListener('toggle',event=>{if(event.target.open)refreshMediaCache();});
});
