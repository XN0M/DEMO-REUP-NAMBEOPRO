/* Multi-marker cuts. All times refer to the source video, never a rendered preview. */
let cutSession = 0, cutSource = '', cutMarks = [], cutSelected = null;
let cutInfo = null, cutOutputFolder = '', cutPreviewEnd = null, cutBusy = false;
let cutDrag = null, cutLoaded = false;
const cutEl = id => document.getElementById(id);
const cutFrame = () => 1 / (cutInfo?.fps || 30);
const cutTimes = () => [trimState.start, ...cutMarks, trimState.end];
const cutSegments = () => cutTimes().slice(0, -1).map((start, i) => ({start, end: cutTimes()[i + 1]}));
function cutClock(value) {
  let ms = Math.round(Math.max(0, value) * 1000);
  const h = Math.floor(ms / 3600000); ms %= 3600000;
  const m = Math.floor(ms / 60000); ms %= 60000;
  const s = Math.floor(ms / 1000); ms %= 1000;
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}.${String(ms).padStart(3,'0')}`;
}
function resetCutsForSource(path) {
  ++cutSession; cutSource = path; cutInfo = null; cutLoaded = false;
  cutMarks = []; cutSelected = null; cutPreviewEnd = null; cutBusy = false; cutDrag = null;
  trimState = {active:true, videoDuration:0, start:0, end:0};
  cropState = {active:false, dragging:false, startX:0,startY:0,endX:1,endY:1,committed:false};
  cropCanvasImg = null; cutOutputFolder = ''; cropTrimTaskId = null;
  clearTimeout(cropTrimPollTimer);
  const player = cutEl('cutSourcePlayer'); player.pause(); player.removeAttribute('src'); player.load();
  cutEl('cropTrimPreviewVideo').pause(); cutEl('cropTrimPreviewVideo').removeAttribute('src');
  cutEl('cropTrimResultCard').classList.add('hidden');
  cutEl('cropTrimProgressBox').classList.add('hidden');
  cutEl('cropToggle').checked = false; cutEl('cropControls').classList.add('hidden');
  cutEl('trimToggle').checked = true; cutEl('trimControls').classList.remove('hidden');
  cutEl('losslessTrimCheck').checked = false;
  cutEl('cropCanvas').getContext('2d').clearRect(0,0,cutEl('cropCanvas').width,cutEl('cropCanvas').height);
  cutEl('cropCanvasPlaceholder').style.display = '';
  renderCutTimeline();
}
async function initCropEditor() {
  if (cutSource !== currentTranscriptRelPath) resetCutsForSource(currentTranscriptRelPath);
  if (cutLoaded || !cutSource) return;
  cutLoaded = true;
  const session = cutSession, path = cutSource, player = cutEl('cutSourcePlayer');
  player.onloadeddata = () => {
    if (session !== cutSession) return;
    _drawFrameToCanvas(cutEl('cropCanvas'), player, cutEl('cropCanvasPlaceholder'), cutEl('cropCanvasInfo'));
  };
  player.onseeked = player.onloadeddata;
  player.ontimeupdate = () => {
    if (cutPreviewEnd !== null && player.currentTime >= cutPreviewEnd) { player.pause(); cutPreviewEnd = null; }
    const head = cutEl('cutPlayhead');
    if (head) head.style.left = `${100 * player.currentTime / (trimState.videoDuration || 1)}%`;
  };
  player.onerror = () => { if (session === cutSession) showToast('Không phát được video nguồn', true); };
  player.src = '/api/history/stream/' + path.split('/').map(encodeURIComponent).join('/');
  player.load();
  try {
    const response = await fetch('/api/video/crop-trim/media?filepath=' + encodeURIComponent(path));
    const info = await response.json();
    if (session !== cutSession) return;
    if (!response.ok) throw Error(info.detail || 'Không đọc được video');
    cutInfo = info; trimState.videoDuration = info.duration; trimState.end = info.duration;
    renderCutTimeline();
  } catch (error) { if (session === cutSession) { cutLoaded = false; showToast(error.message, true); } }
}
function onTrimToggle() {
  trimState.active = cutEl('trimToggle').checked;
  cutEl('trimControls').classList.toggle('hidden', !trimState.active);
  renderCutTimeline();
}
function setCutTime(index, value) {
  if (!cutInfo || cutBusy || !Number.isFinite(value)) return;
  const times = cutTimes(), frame = cutFrame();
  const low = index === 0 ? 0 : times[index-1] + frame;
  const high = index === times.length-1 ? cutInfo.duration : times[index+1] - frame;
  value = Math.max(low, Math.min(high, value));
  if (index === 0) trimState.start = value;
  else if (index === times.length-1) trimState.end = value;
  else cutMarks[index-1] = value;
  renderCutTimeline();
}
function onTrimInputChange() {
  const start=Number(cutEl('cutStartInput').value), end=Number(cutEl('cutEndInput').value);
  setCutTime(0, start);
  setCutTime(cutTimes().length-1, end);
  cutEl('cutStartInput').value=Number(trimState.start.toFixed(6));
  cutEl('cutEndInput').value=Number(trimState.end.toFixed(6));
  renderCutTimeline();
}
function editSelectedCut(value) {
  if(cutSelected===null)return;
  setCutTime(cutSelected, Number(value));
  cutEl('cutSelectedInput').value=Number(cutTimes()[cutSelected].toFixed(6));
}
function addCutMarker() {
  if (!cutInfo || cutBusy) return;
  const t = cutEl('cutSourcePlayer').currentTime, frame = cutFrame(), times = cutTimes();
  if (t <= trimState.start || t >= trimState.end || times.some(v => Math.abs(v-t) < frame-1e-6)) {
    showToast('Đặt đầu phát trong A→B, cách mỗi mốc ít nhất một khung hình', true); return;
  }
  cutMarks.push(t); cutMarks.sort((a,b) => a-b); cutSelected = cutMarks.indexOf(t)+1;
  renderCutTimeline();
}
function deleteCutMarker() {
  if (cutBusy) return;
  if (cutSelected === null || cutSelected === 0 || cutSelected === cutTimes().length-1) {
    showToast('Chọn một mốc ở giữa A và B để xóa', true); return;
  }
  cutMarks.splice(cutSelected-1, 1); cutSelected = null; renderCutTimeline();
}
function resetCutMarkers() {
  if (cutBusy) return;
  cutMarks = []; cutSelected = null; trimState.start=0; trimState.end=cutInfo?.duration || 0;
  renderCutTimeline();
}
function previewCut(index) {
  const segment = cutSegments()[index], player = cutEl('cutSourcePlayer');
  if (!segment) return;
  player.currentTime = segment.start; cutPreviewEnd = segment.end;
  player.play().catch(() => showToast('Bấm phát trên video để xem thử', true));
}
function renderCutTimeline() {
  const track = cutEl('trimTimelineTrack'), labels = cutEl('cutMarkerLabels');
  if (!track) return;
  const focused = document.activeElement?.dataset?.cutIndex;
  track.replaceChildren(); labels.replaceChildren();
  const times = cutTimes(), duration=trimState.videoDuration || 1, width=track.clientWidth || 300;
  const range=document.createElement('div');
  range.style.cssText=`position:absolute;top:0;bottom:0;background:#4f46e566;left:${times[0]/duration*100}%;width:${(times.at(-1)-times[0])/duration*100}%;pointer-events:none`;
  track.append(range);
  const rows=[];
  times.forEach((t,index) => {
    const name=index===0?'A':index===times.length-1?'B':`M${index}`;
    const marker=document.createElement('button');
    marker.type='button'; marker.dataset.cutIndex=index;
    marker.textContent=name; marker.title=`${name}: ${cutClock(t)}`;
    marker.setAttribute('aria-label',marker.title); marker.setAttribute('aria-pressed',String(cutSelected===index));
    marker.disabled=!cutInfo || cutBusy;
    marker.style.cssText=`position:absolute;left:${t/duration*100}%;top:0;transform:translateX(-50%);height:32px;min-height:32px;transition:none;min-width:20px;background:${cutSelected===index?'#f59e0b':'#6366f1'};border:1px solid white;border-radius:4px;color:white;font-size:10px;touch-action:none;z-index:2`;
    marker.onpointerdown=e => { if(cutBusy)return; e.preventDefault(); cutSelected=index; cutDrag=index; renderCutTimeline(); };
    marker.onclick=() => {cutSelected=index;renderCutTimeline();track.querySelector(`[data-cut-index="${index}"]`)?.focus();};
    marker.onkeydown=e => {
      if (e.key==='ArrowLeft'||e.key==='ArrowRight') {
        e.preventDefault(); cutSelected=index;setCutTime(index,t+(e.key==='ArrowLeft'?-1:1)*cutFrame()*(e.shiftKey?10:1));
        track.querySelector(`[data-cut-index="${index}"]`)?.focus();
      } else if(e.key==='Delete'||e.key==='Backspace') {e.preventDefault();cutSelected=index;deleteCutMarker();}
    };
    track.append(marker);
    const labelWidth=Math.min(140,width), x=t/duration*width;
    const left=Math.max(0,Math.min(width-labelWidth,x-labelWidth/2));
    let row=rows.findIndex(right=>right+4<=left); if(row<0)row=rows.length;
    rows[row]=left+labelWidth;
    const line=document.createElement('div');
    line.style.cssText=`position:absolute;left:${Math.min(width-1,x)}px;top:0;height:${row*40+6}px;border-left:1px solid #818cf8;pointer-events:none`;
    const label=document.createElement('button');label.textContent=`${name} ${cutClock(t)}`;
    label.style.cssText=`position:absolute;left:${left}px;top:${row*40+6}px;width:${labelWidth}px;box-sizing:border-box;max-width:100%;height:34px;transition:none;font:11px monospace;color:#c7d2fe;background:#0f172a;border:1px solid #475569;border-radius:3px`;
    label.onclick=()=>{cutSelected=index;renderCutTimeline();cutEl('cutSelectedInput').focus();};
    labels.append(line,label);
  });
  labels.style.height=`${rows.length*40+8}px`;
  const head=document.createElement('div');head.id='cutPlayhead';
  head.style.cssText=`position:absolute;top:0;bottom:0;border-left:2px solid #fb7185;pointer-events:none;z-index:3;left:${cutEl('cutSourcePlayer').currentTime/duration*100}%`;track.append(head);
  if(focused!==undefined)track.querySelector(`[data-cut-index="${focused}"]`)?.focus();
  for(const [id,value] of [['cutStartInput',trimState.start],['cutEndInput',trimState.end],['cutSelectedInput',cutSelected===null?'':times[cutSelected]]]) {
    const input=cutEl(id);if(document.activeElement!==input)input.value=value===''?'':Number(value.toFixed(6));
    input.disabled=cutBusy||!cutInfo||(id==='cutSelectedInput'&&cutSelected===null);
  }
  const list=cutEl('cutSegmentsList');list.replaceChildren();
  if(cutInfo)cutSegments().forEach((segment,index)=>{
    const row=document.createElement('div');row.style.cssText='display:flex;gap:8px;flex-wrap:wrap;align-items:center';
    const text=document.createElement('span');text.textContent=`Đoạn ${index+1}: ${cutClock(segment.start)} → ${cutClock(segment.end)} · ${(segment.end-segment.start).toFixed(3)}s`;
    const play=document.createElement('button');play.textContent='Phát thử';play.style.color='#a5b4fc';play.onclick=()=>previewCut(index);row.append(text,play);list.append(row);
  });
  cutEl('trimDurationLabel').textContent=cutInfo?`${cutSegments().length} đoạn · Tổng ${(trimState.end-trimState.start).toFixed(3)} giây`: 'Đang chờ thông tin video';
  const btn=cutEl('btnStartCropTrim');btn.disabled=cutBusy||!cutInfo;
  btn.textContent=cutBusy?'Đang xuất…':`Xuất ${trimState.active?cutSegments().length:1} đoạn`;
}
cutEl('trimTimelineTrack').addEventListener('click', e => {
  if(e.target.closest('button')||!cutInfo)return;
  const rect=e.currentTarget.getBoundingClientRect();cutPreviewEnd=null;
  cutEl('cutSourcePlayer').currentTime=Math.max(0,Math.min(cutInfo.duration,(e.clientX-rect.left)/rect.width*cutInfo.duration));
});
document.addEventListener('pointermove',e=>{
  if(cutDrag===null||!cutInfo)return;
  e.preventDefault();const rect=cutEl('trimTimelineTrack').getBoundingClientRect();
  setCutTime(cutDrag,(e.clientX-rect.left)/rect.width*cutInfo.duration);
},{passive:false});
for(const event of ['pointerup','pointercancel'])document.addEventListener(event,()=>{cutDrag=null;});
new ResizeObserver(()=>renderCutTimeline()).observe(cutEl('trimTimelineTrack'));
async function startCropTrimProcess() {
  if(cutBusy||!cutInfo)return;
  let crop=null;
  if(cropState.active) {
    if(!cropState.committed||!cropCanvasImg){showToast('Chọn vùng crop trước khi xuất',true);return;}
    const canvas=cutEl('cropCanvas');
    const x=Math.round(Math.min(cropState.startX,cropState.endX)*canvas.width);
    const y=Math.round(Math.min(cropState.startY,cropState.endY)*canvas.height);
    const w=Math.round(Math.max(cropState.startX,cropState.endX)*canvas.width)-x;
    const h=Math.round(Math.max(cropState.startY,cropState.endY)*canvas.height)-y;
    if(w<4||h<4){showToast('Vùng crop quá nhỏ',true);return;}crop={x,y,w,h};
  }
  if(!trimState.active&&!crop){showToast('Bật cắt thời gian hoặc crop trước khi xuất',true);return;}
  const session=cutSession, path=cutSource;
  cutBusy=true;renderCutTimeline();cutEl('cropTrimProgressBox').classList.remove('hidden');cutEl('cropTrimResultCard').classList.add('hidden');
  cutEl('cropTrimProgressBar').style.width='0%';cutEl('cropTrimProgressPct').textContent='0%';
  cutEl('cropTrimProgressText').textContent='Đang chuẩn bị…';
  try {
    const response=await fetch('/api/video/crop-trim',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filepath:path,crop,segments:trimState.active?cutSegments():[{start:0,end:cutInfo.duration}],lossless_trim:cutEl('losslessTrimCheck').checked})});
    const data=await response.json();if(session!==cutSession)return;
    if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Thông số cắt không hợp lệ');
    cropTrimTaskId=data.task_id;pollCropTrimProgress(data.task_id,session);
  }catch(error){if(session===cutSession){cutBusy=false;renderCutTimeline();cutEl('cropTrimProgressBox').classList.add('hidden');showToast(error.message,true);}}
}
function pollCropTrimProgress(taskId,session=cutSession) {
  clearTimeout(cropTrimPollTimer);
  const poll=async()=>{
    if(session!==cutSession||taskId!==cropTrimTaskId)return;
    try {
      const response=await fetch(`/api/video/crop-trim/tasks/${taskId}`);const data=await response.json();
      if(session!==cutSession||taskId!==cropTrimTaskId)return;
      if(!response.ok)throw Error(data.detail||'Không đọc được trạng thái xuất');
      cutEl('cropTrimProgressBar').style.width=`${data.percent||0}%`;
      cutEl('cropTrimProgressPct').textContent=`${data.percent||0}%`;
      cutEl('cropTrimProgressText').textContent=data.progress_text||'Đang xuất…';
      if(data.status==='completed'||data.status==='error') {
        cutBusy=false;renderCutTimeline();cutEl('cropTrimProgressBox').classList.add('hidden');showCutOutputs(data);
        showToast(data.status==='error'?data.error:`Đã xuất ${data.outputs.length} đoạn`,data.status==='error');return;
      }
    }catch(error){if(session!==cutSession)return;cutEl('cropTrimProgressText').textContent='Mất kết nối, đang thử lại…';}
    cropTrimPollTimer=setTimeout(poll,1000);
  };poll();
}
function showCutOutputs(data) {
  cutOutputFolder=data.output_folder||'';
  const list=cutEl('cutOutputList');list.replaceChildren();
  cutEl('cropTrimResultCard').classList.toggle('hidden',!data.outputs?.length);
  cutEl('cropTrimResultCard').querySelector('span').textContent=data.status==='error'?'Các đoạn đã hoàn tất (tác vụ có lỗi)':`Đã xuất ${data.outputs?.length||0} đoạn`;
  const video=cutEl('cropTrimPreviewVideo');
  for(const output of data.outputs||[]) {
    const row=document.createElement('div'),text=document.createElement('span'),play=document.createElement('button'),link=document.createElement('a');
    row.style.cssText='display:flex;gap:12px;flex-wrap:wrap';text.textContent=`Đoạn ${output.index} · ${cutClock(output.start)} → ${cutClock(output.end)}`;
    play.textContent='Phát thử';play.onclick=()=>{video.src=output.url;video.play().catch(()=>{});};
    link.textContent='Tải đoạn';link.href=output.download_url;link.download=`part_${String(output.index).padStart(3,'0')}.mp4`;
    row.append(text,play,link);list.append(row);
  }
  if(data.outputs?.length)video.src=data.outputs[0].url;
}
async function openCutOutputFolder() {
  if(!cutOutputFolder)return;
  try{const response=await fetch('/api/open-folder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder_path:cutOutputFolder})});if(!response.ok)throw Error('Không mở được thư mục');}catch(error){showToast(error.message,true);}
}
