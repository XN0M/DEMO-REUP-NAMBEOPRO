/* Shared voice catalog and personal voice manager for all dubbing workflows. */
(() => {
  let catalog = [], dialog, timer, player, playerUrl, loading;
  const model = {edge:'edge', openai:'tts-1', omnivoice:'omnivoice', vieneu:'v3turbo'};
  const states = {not_installed:'Chưa cài', stopped:'Chưa nạp model', starting:'Đang khởi động', idle:'Chờ tạo giọng', installing:'Đang cài / tải model', loading:'Đang nạp / tải model', generating:'Đang tạo giọng', ready:'Sẵn sàng', error:'Có lỗi'};
  const node = (tag, text) => {const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
  async function api(path, options={}) {const response=await fetch('/api/tts'+path,options);const body=await response.json();if(!response.ok)throw Error(body.detail||'Không xử lý được yêu cầu');return body;}
  function report(error) {if(dialog)dialog.querySelector('[data-message]').textContent=error.message||String(error);}
  function button(text, action) {const b=node('button',text);b.type='button';b.onclick=async()=>{b.disabled=true;try{await action();}catch(error){report(error);}finally{b.disabled=false;}};return b;}
  function config(voice) {return {provider:voice.provider,model:voice.model||model[voice.provider],voice:voice.id};}
  function populate(select) {
    if(!select||!catalog.length)return;
    const old=select.value, first=!select.dataset.voiceCatalog;
    select.replaceChildren();
    for(const voice of catalog){const opt=node('option',`${voice.provider} · ${voice.name}${voice.kind==='custom'?' · Cá nhân':''}${voice.note?' · 📝 '+voice.note.slice(0,45):''}`);opt.value=voice.id;opt.title=voice.note||'';select.append(opt);}
    let saved;try{saved=localStorage.getItem('nambeo.defaultVoice');}catch(_){}
    const choice=first&&saved&&catalog.some(v=>v.id===saved)?saved:old;
    if(catalog.some(v=>v.id===choice))select.value=choice;
    else if(choice){const missing=node('option','Thiếu giọng · '+choice);missing.value=choice;select.append(missing);select.value=choice;}
    select.dataset.voiceCatalog='true';
  }
  function controls() {
    if(dialog?.open)populate(dialog.querySelector('[data-default-voice]'));
    for(const select of document.querySelectorAll('#dubVoiceSelect, #batchVoiceSelect, #batchVoice, .dub-char-select'))populate(select);
    for(const select of document.querySelectorAll('#dubVoiceSelect, #batchVoiceSelect, #batchVoice')){
      if(select.parentElement.querySelector('[data-voice-library]'))continue;
      const b=button('Giọng cá nhân / Cài VieNeu',open);b.dataset.voiceLibrary='';b.style.cssText='font-size:12px;color:#a5b4fc;padding:6px';select.after(b);
    }
  }
  async function refresh() {
    if(loading)return loading;
    loading=(async()=>{const response=await fetch('/api/dub/voices');if(!response.ok)throw Error('Không tải được danh mục giọng');catalog=(await response.json()).voices;controls();window.dispatchEvent(new CustomEvent('nambeo-voices-changed',{detail:catalog}));return catalog;})();
    try{return await loading;}finally{loading=null;}
  }
  async function play(url, options) {
    if(player){player.pause();player=null;}if(playerUrl){URL.revokeObjectURL(playerUrl);playerUrl=null;}
    const response=await fetch(url,options);if(!response.ok){const error=await response.json();throw Error(error.detail||'Không nghe được giọng');}
    playerUrl=URL.createObjectURL(await response.blob());player=new Audio(playerUrl);await player.play();
  }
  async function list() {
    const result=await api('/voices/custom'), target=dialog.querySelector('[data-list]');target.replaceChildren();
    if(!result.voices.length)target.append(node('p','Chưa có giọng cá nhân. Tải mẫu một người nói rõ tiếng, ít nhạc nền.'));
    for(const voice of result.voices){
      const card=node('div');card.className='voiceNoteCard';
      const row=node('div');row.className='voiceLibraryRow';
      const name=node('input');name.value=voice.name;name.setAttribute('aria-label','Tên giọng '+voice.name);name.maxLength=80;
      row.append(name,node('span',`${voice.duration}s`),button('Nghe mẫu',()=>play(`/api/tts/voices/custom/${voice.id}/sample`)),
        button('Lưu tên',async()=>{await api('/voices/custom/'+voice.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name.value})});await refresh();await list();}),
        button('Thử giọng',()=>play('/api/dub/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({voice:'vieneu:custom:'+voice.id,provider:'vieneu',text:'Xin chào, đây là giọng đọc của tôi trong Nambeo.'})})),
        button('Xóa',async()=>{if(!confirm('Xóa giọng '+voice.name+'? Dự án đang dùng giọng này sẽ cần chọn lại.'))return;await api('/voices/custom/'+voice.id,{method:'DELETE'});await refresh();await list();}));card.append(row);card.append(noteEditor(catalog.find(item=>item.id==='vieneu:custom:'+voice.id)||{id:'vieneu:custom:'+voice.id}));target.append(card);
    }
    const presets=dialog.querySelector('[data-presets]');presets.replaceChildren();
    const query=dialog.querySelector('[data-note-search]').value.trim().toLocaleLowerCase('vi');
    for(const voice of catalog.filter(v=>v.kind!=='custom'&&(!query||`${v.provider} ${v.name} ${v.note||''}`.toLocaleLowerCase('vi').includes(query)))){
      const card=node('div');card.className='voiceNoteCard';card.append(node('strong',`${voice.provider} · ${voice.name}`),noteEditor(voice));presets.append(card);
    }
    if(!presets.children.length)presets.append(node('p','Không tìm thấy giọng phù hợp.'));
  }
  function noteEditor(voice){
    const row=node('div');row.className='voiceNoteEditor';const note=node('textarea');note.value=voice.note||'';note.maxLength=500;note.rows=2;note.placeholder='Ví dụ: giọng trầm, hợp phim cổ trang hoặc video kể chuyện';note.setAttribute('aria-label','Ghi chú cho giọng '+voice.name);
    row.append(note,button('Lưu ghi chú',async()=>{const result=await api('/voices/note/'+encodeURIComponent(voice.id),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({note:note.value})});voice.note=result.note;await refresh();report('Đã lưu ghi chú cho giọng '+voice.name+'.');}));return row;
  }
  async function status() {
    if(!dialog?.open)return;
    try {const value=await api('/status');dialog.querySelector('[data-engine]').textContent=`VieNeu v3 Turbo · ${states[value.state]||value.state} · ${value.device.toUpperCase()}${value.message?' — '+value.message:''}`;
      const device=dialog.querySelector('[data-device]');if(document.activeElement!==device)device.value=value.device;
      if(value.state==='installing')dialog.dataset.installing='true';
      else if(dialog.dataset.installing){delete dialog.dataset.installing;await refresh();}
    }catch(error){report(error);}finally{if(dialog?.open)timer=setTimeout(status,1500);}
  }
  async function open() {
    if(dialog?.open){dialog.focus();return;}
    if(!dialog){
      dialog=node('dialog');dialog.className='voiceLibrary';dialog.setAttribute('aria-label','Giọng cá nhân và VieNeu');
      const heading=node('div');heading.className='voiceLibraryRow';heading.append(node('h2','Giọng cá nhân & VieNeu'),button('Đóng',()=>dialog.close()));
      const engine=node('p');engine.dataset.engine='';const device=node('select');device.dataset.device='';device.setAttribute('aria-label','Thiết bị tạo giọng');
      for(const [value,label] of [['cuda','RTX / CUDA'],['cpu','CPU']]){const option=node('option',label);option.value=value;device.append(option);}
      device.onchange=async()=>{device.disabled=true;try{await api('/device',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({device:device.value})});}catch(error){report(error);}finally{device.disabled=false;}};
      const actions=node('div');actions.className='voiceLibraryRow';actions.append(device,button('Cài / sửa VieNeu',async()=>{await api('/install',{method:'POST'});dialog.dataset.installing='true';}),button('Làm mới giọng',refresh));
      for(const [kind,label] of [['install','Nhật ký cài đặt'],['worker','Nhật ký tạo giọng']]){const link=node('a',label);link.href='/api/tts/log?kind='+kind;link.target='_blank';link.rel='noopener';actions.append(link);}
      const form=node('form'), name=node('input'), file=node('input'), consent=node('input'), consentLabel=node('label');
      name.placeholder='Tên giọng';name.required=true;name.maxLength=80;name.setAttribute('aria-label','Tên giọng mới');file.type='file';file.accept='.wav,.mp3,.m4a,.flac';file.required=true;file.setAttribute('aria-label','Audio mẫu dài 3–8 giây');consent.type='checkbox';consent.required=true;consentLabel.append(consent,document.createTextNode(' Tôi có quyền sử dụng giọng mẫu này'));
      const upload=node('button','Lưu giọng mẫu');upload.type='submit';form.append(node('h3','Thêm giọng từ audio'),node('p','WAV, MP3, M4A hoặc FLAC · 3–8 giây · tối đa 20 MB'),name,file,consentLabel,upload);
      form.onsubmit=async event=>{event.preventDefault();if(!form.reportValidity())return;upload.disabled=true;try{const sample=file.files[0];if(sample.size>20*1024*1024)throw Error('File mẫu không quá 20 MB');await api('/voices/custom?'+new URLSearchParams({name:name.value,filename:sample.name,consent:String(consent.checked)}),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:sample});form.reset();await refresh();await list();report('Đã lưu giọng. Chọn giọng này trong danh sách để sử dụng.');}catch(error){report(error);}finally{upload.disabled=false;}};
      const message=node('p');message.dataset.message='';message.setAttribute('role','status');const voices=node('div');voices.dataset.list='';
      const search=node('input');search.dataset.noteSearch='';search.type='search';search.placeholder='Tìm tên giọng hoặc ghi chú';search.setAttribute('aria-label','Tìm giọng để ghi chú');search.oninput=()=>list().catch(report);
      const presets=node('div');presets.dataset.presets='';
      const defaultSelect=node('select');defaultSelect.dataset.defaultVoice='';defaultSelect.setAttribute('aria-label','Giọng mặc định cho lồng tiếng nhanh và batch');
      const defaults=node('div');defaults.className='voiceLibraryRow';defaults.append(defaultSelect,button('Mặc định cho nhanh / batch',()=>{localStorage.setItem('nambeo.defaultVoice',defaultSelect.value);for(const select of document.querySelectorAll('#dubVoiceSelect,#batchVoiceSelect'))select.value=defaultSelect.value;report('Đã lưu giọng mặc định cho tác vụ mới.');}));
      dialog.append(heading,engine,actions,message,defaults,form,node('h3','Giọng cá nhân'),voices,node('h3','Ghi chú các giọng có sẵn'),search,presets);document.body.append(dialog);
      dialog.addEventListener('close',()=>{clearTimeout(timer);if(player)player.pause();if(playerUrl)URL.revokeObjectURL(playerUrl);playerUrl=null;});
    }
    dialog.showModal();clearTimeout(timer);status();
    try{await refresh();populate(dialog.querySelector('[data-default-voice]'));await list();}catch(error){report(error);}
  }
  window.NambeoVoices={open,refresh,config,get catalog(){return catalog;}};
  document.addEventListener('DOMContentLoaded',()=>{refresh().catch(()=>{});let pending=false;new MutationObserver(()=>{if(pending)return;pending=true;queueMicrotask(()=>{pending=false;for(const s of document.querySelectorAll('#dubVoiceSelect, #batchVoiceSelect, #batchVoice, .dub-char-select'))if(!s.dataset.voiceCatalog)populate(s);});}).observe(document.body,{childList:true,subtree:true});});
})();
