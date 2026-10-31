/* YouTube connection and per-link analysis. User content is always DOM text. */
(() => {
  let csrf='', session=null, running=false, stopping=false, pending=[], timer=null;
  let panel, storage, checked, enabled, settingsMessage, checkButton, checkUrl;
  const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
  const showError=error=>{settingsMessage.textContent=error.message;report(error.message,true);};
  const button=(text,fn)=>{const e=node('button',text,'nb-button');e.type='button';e.onclick=()=>Promise.resolve().then(fn).catch(showError);return e;};
  const date=value=>value?new Date(value*1000).toLocaleString('vi-VN'):'chưa có';
  async function api(path='',method='GET',body){
    if(method!=='GET'&&!csrf)await refresh();
    const headers=method==='GET'?{}:{'X-Nambeo-CSRF':csrf};
    if(body!==undefined)headers['Content-Type']=body instanceof File?'application/octet-stream':'application/json';
    let response;
    try{response=await fetch('/api/youtube/session'+path,{method,headers,body:body===undefined?undefined:body instanceof File?body:JSON.stringify(body)});}catch(_){throw Error('Không kết nối được Nambeo. Mở lại trang do run.bat khởi động.');}
    const data=await response.json().catch(()=>({detail:'Phản hồi máy chủ không hợp lệ.'}));
    if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:data.detail?.message||'Không thực hiện được thao tác.');
    return data;
  }
  function paint(data){
    session=data;csrf=data.csrf_token||csrf;
    storage.textContent=data.storage_error||(data.stored?`Đã lưu phiên · ${date(data.imported_at)}`:'Chưa có phiên');
    enabled.checked=data.enabled;enabled.disabled=!data.stored||!!data.storage_error;
    const last=data.last_check;
    checked.textContent=last?`Kiểm tra gần nhất (${date(last.checked_at)}): ${last.message}`:'Kiểm tra gần nhất: chưa kiểm tra.';
    panel.querySelector('[data-resume]').hidden=!data.paused;
  }
  async function refresh(){const data=await api();paint(data);return data;}
  function openSettings(){window.switchTab('settings');panel.scrollIntoView({block:'nearest'});refresh().catch(e=>settingsMessage.textContent=e.message);}
  async function resume(){
    paint(await api('','PUT',{resume:true}));
    settingsMessage.textContent='Đã tiếp tục hàng đợi. Các lượt tạm dừng sẽ được thử lại khi bạn chọn.';
    const response=await fetch('/api/tasks');
    if(!response.ok)throw Error('Không đọc được danh sách tải; hãy thử lại.');
    const tasks=await response.json();
    for(const [id,task] of Object.entries(tasks))if(task.status==='paused'&&String(task.error_code||'').startsWith('youtube_'))await window.downloadSingle(id);
    window.switchTab('new');
    if(pending.length&&!running){const next=[...pending];pending=[];await run(next);}
  }
  async function checkConnection(){
    if(timer)return;
    const url=checkUrl.value.trim()||document.getElementById('urlInput').value.split('\n').map(s=>s.trim()).find(isYoutube)||'';
    if(!url)throw Error('Nhập một link YouTube để kiểm tra.');
    checkButton.disabled=true;
    settingsMessage.textContent='Đang chờ lượt kiểm tra metadata; không tải video…';
    try{
      const job=await api('/check','POST',{url});
      const poll=async()=>{
        try{
          const result=await api('/check/'+job.id);
          if(result.status==='completed'){
            settingsMessage.textContent=result.result.message+' Kết quả chỉ phản ánh video/lần truy cập vừa kiểm tra.';
            timer=null;checkButton.disabled=false;await refresh();return;
          }
          settingsMessage.textContent=result.status==='waiting'?'Đang chờ lượt YouTube…':'Đang đọc metadata video…';
          timer=setTimeout(poll,1000);
        }catch(error){timer=null;checkButton.disabled=false;settingsMessage.textContent=error.message;}
      };
      timer=setTimeout(poll,200);
    }catch(error){checkButton.disabled=false;throw error;}
  }
  function isYoutube(text){try{const u=new URL(text);return ['http:','https:'].includes(u.protocol)&&(/(^|\.)youtube\.com$/.test(u.hostname)||u.hostname==='youtu.be');}catch(_){return false;}}
  let details,cancel,continueButton,settingsLink;
  const report=(text,error=false)=>{const status=document.getElementById('parseStatus');status.hidden=false;status.textContent=text;status.style.color=error?'#fda4af':'#cbd5e1';};
  async function run(lines){
    if(running)return;
    if(!lines.length){report('Vui lòng nhập link video trước khi phân tích.',true);return;}
    running=true;stopping=false;pending=[];details.replaceChildren();continueButton.hidden=true;settingsLink.hidden=true;
    const parseButton=document.getElementById('btnParse');parseButton.disabled=true;cancel.hidden=false;
    let succeeded=0,failed=0,completed=0,blocked=false,unconfirmed=false;
    for(let index=0;index<lines.length;index++){
      const url=lines[index];
      if(stopping){pending.push(...lines.slice(index));break;}
      const row=node('li');row.append(node('span',`${index+1}. ${url}`),node('p','Đang phân tích…'));details.append(row);
      const result=row.querySelector('p');
      if(blocked&&isYoutube(url)){pending.push(url);result.textContent='Tạm dừng: cần kiểm tra phiên YouTube.';continue;}
      report(`Đã xử lý ${completed}/${lines.length} link · ${succeeded} thành công · ${failed} lỗi`);
      const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),120000);
      try{
        const response=await fetch('/api/parse',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls:[url]}),signal:controller.signal});
        const data=await response.json().catch(()=>{throw Error(`Máy chủ trả phản hồi không hợp lệ (HTTP ${response.status}).`);});
        if(response.ok&&data.videos?.length){
          data.videos.forEach(video=>{if(!videosData.some(old=>old.id===video.id))videosData.push(video);});
          saveParsedVideos();renderVideoList();succeeded++;result.textContent=`Đã phân tích ${data.videos.length} video.`;
        }else{
          failed++;result.textContent=typeof data.detail==='string'?data.detail:'Không tìm thấy video từ link này.';result.className='youtube-error';
          if(['youtube_auth_required','youtube_session_invalid','youtube_paused'].includes(data.code)){
            blocked=true;pending.push(url);settingsLink.hidden=false;
          }
        }
      }catch(error){
        failed++;stopping=true;pending.push(url);
        result.className='youtube-error';
        result.textContent=error.name==='AbortError'?'Quá 2 phút chưa nhận được kết quả. Tác vụ phía máy chủ có thể vẫn đang chạy.':error instanceof TypeError?'Không kết nối được Nambeo. Mở trang do run.bat tự khởi động rồi thử lại.':error.message;
        unconfirmed=error.name==='AbortError';
      }finally{clearTimeout(timeout);completed++;}
    }
    running=false;parseButton.disabled=false;cancel.hidden=true;
    continueButton.hidden=!pending.length;continueButton.textContent=`Tiếp tục ${pending.length} link chưa hoàn tất`;
    report(`${succeeded} thành công · ${failed} lỗi · ${pending.length} link cần thử lại/chưa xử lý.${unconfirmed?' Tác vụ đang chạy chưa được xác nhận đã dừng.':stopping?' Đã ngừng gửi link tiếp theo.':''}`,!!failed);
    if(blocked)refresh().catch(()=>{});
  }
  function init(){
    const settings=document.getElementById('tabSettingsSection');if(!settings)return;
    panel=node('section',undefined,'nb-panel youtube-settings');panel.id='youtubeConnection';
    panel.append(node('h2','Kết nối YouTube'),node('p','Ứng dụng ưu tiên tải độ phân giải cao nhất không cần cookie hay API key. Chỉ dùng cookie đã bật nếu YouTube yêu cầu đăng nhập. Nút Kiểm tra kết nối kiểm tra phiên đã lưu; cookie được mã hóa trên máy này.'));
    const docs=node('a','Hướng dẫn xuất cookie chính thức của yt-dlp');docs.href='https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies';docs.target='_blank';docs.rel='noopener noreferrer';panel.append(docs);
    storage=node('p','Đang đọc trạng thái lưu trữ…');checked=node('p','Kiểm tra gần nhất: chưa kiểm tra.');
    const toggle=node('label');enabled=node('input');enabled.type='checkbox';enabled.disabled=true;toggle.append(enabled,document.createTextNode(' Dùng phiên đã nhập'));
    enabled.onchange=async()=>{try{paint(await api('','PUT',{enabled:enabled.checked}));settingsMessage.textContent='Thay đổi chỉ áp dụng cho lượt mới; lượt đang chạy giữ phiên đã nhận.';}catch(error){enabled.checked=!!session?.enabled;settingsMessage.textContent=error.message;}};
    const input=node('input');input.type='file';input.accept='.txt,text/plain';input.hidden=true;input.setAttribute('aria-label','Chọn tệp cookie YouTube Netscape');
    input.onchange=async()=>{const file=input.files[0];if(!file)return;try{if(file.size>2*1024*1024)throw Error('Tệp cookie tối đa 2 MB.');paint(await api('','POST',file));settingsMessage.textContent='Đã lưu phiên, chưa xác nhận kết nối. Bấm Kiểm tra kết nối rồi chủ động thử lại link.';}catch(error){settingsMessage.textContent=error.message;}finally{input.value='';}};
    const actions=node('div',undefined,'youtube-actions');
    checkUrl=node('input');checkUrl.type='url';checkUrl.placeholder='Link YouTube cần kiểm tra (hoặc dùng link ở trang tải)';checkUrl.setAttribute('aria-label','Link YouTube kiểm tra kết nối');
    checkButton=button('Kiểm tra kết nối',checkConnection);
    const resumeButton=button('Tiếp tục YouTube',resume);resumeButton.dataset.resume='';resumeButton.hidden=true;
    actions.append(button('Nhập tệp cookie',()=>input.click()),checkButton,button('Xóa phiên',async()=>{paint(await api('','DELETE'));settingsMessage.textContent='Đã xóa phiên đã lưu. Lượt đang chạy giữ snapshot đến khi kết thúc; lượt mới không dùng phiên này.';}),resumeButton);
    settingsMessage=node('p');settingsMessage.setAttribute('role','status');
    panel.append(storage,checked,toggle,checkUrl,input,actions,settingsMessage,node('p','Cookie có thể giúp truy cập nhưng không bảo đảm vượt mọi hạn chế của YouTube. Không gửi tệp cookie cho người khác.'));
    settings.append(panel);
    const status=document.getElementById('parseStatus');const tools=node('div',undefined,'youtube-actions');
    cancel=button('Ngừng sau link đang xử lý',()=>{stopping=true;report('Đã yêu cầu ngừng gửi link tiếp theo; đang chờ link hiện tại kết thúc.');});cancel.hidden=true;
    continueButton=button('Tiếp tục link chưa hoàn tất',async()=>{if(running)return;paint(await api('','PUT',{resume:true}));const next=[...pending];pending=[];await run(next);});continueButton.hidden=true;
    settingsLink=button('Mở cài đặt YouTube',openSettings);settingsLink.hidden=true;
    tools.append(cancel,continueButton,settingsLink);details=node('ol',undefined,'youtube-link-results');status.after(tools,details);
    panel.append(button('Quay lại link đang xử lý',()=>window.switchTab('new')));
    window.youtubeParse=()=>run(document.getElementById('urlInput').value.split('\n').map(s=>s.trim()).filter(Boolean));
    window.openYouTubeSettings=openSettings;
    window.youtubeDownloadError=(ids,message)=>{
      report(message,true);
      for(const id of ids){
        document.getElementById('progressBox-'+id)?.classList.remove('hidden');
        const detail=document.getElementById('speed-'+id),percent=document.getElementById('percentText-'+id),download=document.getElementById('btnDl-'+id);
        if(detail)detail.textContent=message;if(percent)percent.textContent='Lỗi';
        if(download){download.disabled=false;download.textContent='Thử lại';download.onclick=()=>window.downloadSingle(id);}
      }
    };
    document.querySelector('[data-page="settings"]')?.addEventListener('click',()=>refresh().catch(e=>settingsMessage.textContent=e.message));
    refresh().catch(error=>settingsMessage.textContent=error.message);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
