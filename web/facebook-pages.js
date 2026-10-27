/* Multi-Page settings and explicit per-upload destination. */
(() => {
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const button=(text,fn)=>{const b=el('button',text);b.className='nb-button';b.type='button';b.onclick=fn;return b;};
  let pages=[], busy=false, selectedFile='', panel, rows, id, name, token, selector, message, saveButton;
  async function api(path='',method='GET',body,signal){
    const response=await fetch('/api/fb/pages'+path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal});
    const data=await response.json();
    if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Không thực hiện được thao tác Facebook.');
    return data;
  }
  function report(error){message.textContent=error.message||String(error);showToast(message.textContent,true);}
  function clear(){id.value='';id.readOnly=false;name.value='';token.value='';token.placeholder='Token của Page mới';}
  async function refresh(){
    pages=(await api()).pages;
    rows.replaceChildren();
    if(!pages.length)rows.append(el('p','Chưa có Page. Nhập thông tin bên dưới để thêm Page đầu tiên.'));
    for(const page of pages){
      const row=el('div');row.style.cssText='display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0';
      const label=el('strong',`${page.page_name||'Page chưa đặt tên'} · ${page.page_id}`);row.append(label);
      row.append(button('Sửa',()=>{id.value=page.page_id;id.readOnly=true;name.value=page.page_name;token.value='';token.placeholder='Để trống để giữ token đã lưu';name.focus();}));
      const result=el('p');result.dataset.pageCheck=page.page_id;result.setAttribute('role','status');result.setAttribute('aria-live','polite');
      result.style.cssText='flex-basis:100%;margin:0;white-space:normal';
      const test=button('Kiểm tra kết nối',async()=>{
        const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),20000);
        test.disabled=true;test.textContent='Đang kiểm tra…';test.setAttribute('aria-busy','true');
        result.style.color='#93c5fd';result.textContent='Đang kết nối Facebook để kiểm tra Page…';
        try{
          const data=await api('/'+encodeURIComponent(page.page_id)+'/test','POST',undefined,controller.signal);
          if(data.ok!==true||data.page_id!==page.page_id)throw Error('Máy chủ chưa xác nhận đúng Page. Hãy thử lại.');
          result.style.color='#86efac';result.textContent=`Đã kết nối: ${data.page_name} · ${data.page_id}. Chưa kiểm tra quyền đăng video.`;
          showToast(`Kết nối thành công: ${data.page_name} · ${data.page_id}`);
        }catch(e){
          const detail=e.name==='AbortError'?'Quá 20 giây chưa nhận được kết quả. Kiểm tra mạng và thử lại.':e instanceof TypeError?'Không kết nối được máy chủ Nambeo. Hãy tải lại trang và thử lại.':e.message;
          result.style.color='#fca5a5';result.textContent='Kiểm tra thất bại: '+detail;showToast(result.textContent,true);
        }finally{clearTimeout(timer);test.disabled=false;test.textContent='Kiểm tra kết nối';test.removeAttribute('aria-busy');}
      });
      row.append(test,button('Xóa khỏi tool',async()=>{if(!confirm(`Xóa kết nối ${page.page_name||page.page_id} khỏi tool? Bài trên Facebook vẫn được giữ.`))return;try{await api('/'+encodeURIComponent(page.page_id),'DELETE');if(id.value===page.page_id)clear();await refresh();}catch(e){report(e);}}));
      row.append(result);rows.append(row);
    }
  }
  function field(label,input){const wrap=el('label',label);wrap.style.cssText='display:block;margin:12px 0';input.style.cssText='display:block;width:100%;padding:9px;margin-top:5px;background:#101317;border:1px solid #3b4452;border-radius:7px;color:#eee';wrap.append(input);return wrap;}
  function init(){
    panel=el('section');panel.className='nb-panel';panel.id='facebookPagesPanel';
    panel.append(el('h2','Facebook · Danh sách Page'),el('p','Lưu nhiều Page và chọn Page khi đăng video. Mỗi Page cần token riêng.'));
    rows=el('div');panel.append(rows);
    id=el('input');id.id='fbMultiPageId';id.inputMode='numeric';name=el('input');name.id='fbMultiPageName';token=el('input');token.id='fbMultiPageToken';token.type='password';token.autocomplete='new-password';
    panel.append(field('Page ID',id),field('Tên Page',name),field('Page Access Token',token));
    saveButton=button('Lưu Page',async()=>{
      saveButton.disabled=true;
      try{await api('','POST',{page_id:id.value.trim(),page_name:name.value.trim(),page_access_token:token.value.trim()});clear();await refresh();message.textContent='Đã lưu Page. Bạn có thể chọn Page này khi đăng video.';}catch(e){report(e);}finally{saveButton.disabled=false;}
    });
    panel.append(saveButton,button('Thêm Page khác',clear));message=el('p');message.setAttribute('role','status');panel.append(message);
    const settings=document.getElementById('tabSettingsSection');
    const previous=[...settings.querySelectorAll('section')].find(s=>s.querySelector('h2')?.textContent==='Cấu hình Facebook Fanpage');
    if(previous)previous.replaceWith(panel);else settings.append(panel);
    const old=document.getElementById('fbPageIdInput')?.closest('.nb-panel');
    if(old){old.style.display='none';const link=button('Quản lý các Page Facebook',()=>{switchTab('settings');panel.scrollIntoView({block:'start'});});old.parentNode.insertBefore(link,old);}
    selector=el('select');selector.id='fbPostPage';
    document.getElementById('fbPostVideoPath').parentElement.before(field('Đăng lên Page nào?',selector));
    window.openFbPostModal=async(videoPath,caption)=>{
      if(busy){showToast('Đang gửi video. Hãy chờ lượt hiện tại hoàn tất.',true);return;}
      try{await refresh();}catch(e){report(e);return;}
      if(!pages.length){showToast('Thêm Page trong Cài đặt → Facebook trước khi đăng.',true);switchTab('settings');return;}
      selector.replaceChildren(new Option('Chọn Page để đăng…',''));
      for(const p of pages)selector.add(new Option(`${p.page_name||'Page'} · ${p.page_id}`,p.page_id));
      document.getElementById('btnSubmitFbPost').disabled=false;
      selectedFile=videoPath;document.getElementById('fbPostVideoPath').value=videoPath;
      document.getElementById('fbPostCaption').value=caption||'';
      document.getElementById('fbPostStatus').classList.add('hidden');
      document.getElementById('fbPostModal').classList.remove('hidden');selector.focus();
    };
    window.closeFbPostModal=()=>{if(busy){showToast('Đang gửi video; vui lòng chờ kết quả.',true);return;}document.getElementById('fbPostModal').classList.add('hidden');};
    window.submitFbPost=async()=>{
      if(busy)return;
      const pageId=selector.value, page=pages.find(p=>p.page_id===pageId);
      if(!page){showToast('Hãy chọn Page trước khi đăng.',true);selector.focus();return;}
      const caption=document.getElementById('fbPostCaption').value;
      const status=document.getElementById('fbPostStatus'),submit=document.getElementById('btnSubmitFbPost');
      let completed=false;
      busy=true;submit.disabled=true;selector.disabled=true;status.classList.remove('hidden');
      status.textContent=`Đang gửi video đến ${page.page_name||pageId} · ${pageId}…`;
      try{
        const result=await api('/'+encodeURIComponent(pageId)+'/post','POST',{mp4_path:selectedFile,caption});
        if(!result.success||!result.video_id||result.page_id!==pageId)throw Error('Chưa xác nhận được kết quả. Kiểm tra Page trước khi đăng lại.');
        status.textContent=`Facebook đã nhận video ${result.video_id} cho ${page.page_name||pageId}. Mở Page để kiểm tra xử lý và hiển thị.`;
        completed=true;showToast(status.textContent);
      }catch(e){status.textContent=e.message;showToast(e.message,true);}
      finally{busy=false;submit.disabled=completed;selector.disabled=false;}
    };
    window.approveDownloadAndPostFb=async uuid=>{await approveDownloadPending(uuid);showToast('Sau khi tải xong, bấm Đăng FB để chọn Page và duyệt nội dung.');};
    clear();refresh().catch(report);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
