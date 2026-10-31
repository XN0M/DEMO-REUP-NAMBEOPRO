/* Studio branding editor. Geometry and PNG glyphs come from the render backend. */
(() => {
  const copy=v=>JSON.parse(JSON.stringify(v));
  window.NambeoBranding=class {
    constructor(ctx){this.c=ctx;this.layers=[];this.nodes=[];this.sequence=0;this.key='';this.timer=null;this.dead=false;this.presets=[];this.still=null;this.dragging=false;this.status='';this.uploadSequence=0;this.refresh();}
    config(){return this.c.state().project.studio_config.branding;}
    url(key){return '/api/studio/branding/image/'+key+'?'+new URLSearchParams({filepath:this.c.state().filepath});}
    note(text,error=false){this.status=text;this.statusError=error;this.c.root.querySelectorAll('[data-brand-status]').forEach(n=>{n.textContent=text;n.classList.toggle('danger',error);});if(this.still){this.still.positionStatus.textContent=text;this.still.positionStatus.classList.toggle('danger',error);}}
    refresh(){
      if(this.dead)return;
      const state=this.c.state(),body={filepath:state.filepath,branding:copy(this.config()),aspect:state.project.studio_config.render.aspect},key=JSON.stringify(body);
      if(this.still)this.loadStillFrame();this.draw();if(this.dragging){this.key='';return;}if(key===this.key)return;this.key=key;
      const seq=++this.sequence;clearTimeout(this.timer);this.note('Đang cập nhật xem thử');
      this.timer=setTimeout(async()=>{try{const response=await this.c.api('/branding/preview','POST',body);if(this.dead||seq!==this.sequence)return;
        this.layers=response.layers;this.appliedConfig=body.branding;this.install();this.note('');this.draw();
      }catch(error){if(this.dead||seq!==this.sequence)return;this.layers=[];this.install();this.note(error.message,true);}},180);
    }
    geometry(layer){
      const cfg=this.config()[layer.kind],factor=Math.min(cfg.width/(this.appliedConfig?.[layer.kind].width||cfg.width),1/layer.width,1/layer.height),w=Math.min(1,layer.width*factor),h=Math.min(1,layer.height*factor);
      return {x:Math.max(0,Math.min(1-w,cfg.x)),y:Math.max(0,Math.min(1-h,cfg.y)),width:w,height:h};
    }
    createNode(layer,stage,editable){
      const box=document.createElement('div');box.className='brandingOverlay';box.dataset.brandKind=layer.kind;box.setAttribute('aria-label',layer.kind==='logo'?'Di chuyển logo':'Di chuyển watermark chữ');
      const img=document.createElement('img');img.src=this.url(layer.image);img.draggable=false;img.alt=layer.kind==='logo'?'Logo':'Watermark chữ';
      img.onerror=()=>this.note('Không tải được ảnh thương hiệu. Hãy cập nhật xem thử.');
      const handle=document.createElement('span');handle.className='brandingResize';handle.textContent='↘';box.append(img,handle);stage.append(box);
      box.onpointerdown=e=>{if(!editable()||e.button!==0||e.isPrimary===false)return;e.preventDefault();e.stopPropagation();this.c.remember();this.dragging=true;++this.sequence;clearTimeout(this.timer);box.setPointerCapture(e.pointerId);
        const cfg=this.config()[layer.kind],rect=stage.getBoundingClientRect(),g=this.geometry(layer),initial={px:e.clientX,py:e.clientY,x:g.x,y:g.y,width:cfg.width},resize=e.target===handle;
        box.onpointermove=ev=>{if(ev.pointerId!==e.pointerId)return;const dx=(ev.clientX-initial.px)/rect.width,dy=(ev.clientY-initial.py)/rect.height;
          if(resize)cfg.width=Math.max(.02,Math.min(1-initial.x,initial.width+dx));else{cfg.x=Math.max(0,Math.min(1-g.width,initial.x+dx));cfg.y=Math.max(0,Math.min(1-g.height,initial.y+dy));}
          this.c.changed();this.draw();};
        const finish=()=>{if(!this.dragging)return;this.dragging=false;box.onpointermove=null;box.onpointerup=null;box.onpointercancel=null;box.onlostpointercapture=null;this.key='';this.refresh();this.c.renderProperties();};
        box.onpointerup=finish;box.onpointercancel=finish;box.onlostpointercapture=finish;
      };
      return box;
    }
    install(){
      this.nodes.forEach(n=>n.remove());this.nodes=this.layers.map(l=>this.createNode(l,this.c.stage,()=>this.c.editing()));
      if(this.still){this.still.nodes.forEach(n=>n.remove());this.still.nodes=this.layers.map(l=>this.createNode(l,this.still.stage,()=>true));}
    }
    draw(){
      let overlaps=false;
      const surfaces=[{nodes:this.nodes,editing:this.c.editing(),at:this.c.video.currentTime}];if(this.still)surfaces.push({nodes:this.still.nodes,editing:true,all:true});
      for(const surface of surfaces)this.layers.forEach((layer,i)=>{const box=surface.nodes[i];if(!box)return;const cfg=this.config()[layer.kind],g=this.geometry(layer);
        const show=cfg.enabled&&(surface.all||cfg.start===null||(surface.at>=cfg.start&&surface.at<cfg.end));box.hidden=!show;
        Object.assign(box.style,{left:g.x*100+'%',top:g.y*100+'%',width:g.width*100+'%',height:g.height*100+'%',opacity:cfg.opacity,pointerEvents:surface.editing?'auto':'none'});
        box.classList.toggle('editable',surface.editing);
        if(!surface.all&&show&&this.c.caption.textContent){const a=box.getBoundingClientRect(),b=this.c.caption.getBoundingClientRect();overlaps ||= a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;}
      });
      this.c.root.querySelectorAll('[data-brand-warning]').forEach(n=>n.textContent=overlaps?'Logo/watermark đang chạm vùng phụ đề. Điều chỉnh vị trí nếu cần.':'');
    }
    async responseError(response,label){
      let text='';try{const data=await response.json();if(typeof data.detail==='string')text=data.detail;}catch(_){}
      return text||`${label} (HTTP ${response.status}).`;
    }
    canvasBlob(canvas,type='image/png'){
      return new Promise((resolve,reject)=>{try{canvas.toBlob(blob=>blob?resolve(blob):reject(Error('Không chuyển đổi được ảnh.')),type,.9);}catch(_){reject(Error('Không đọc được dữ liệu ảnh.'));}});
    }
    async uploadLogo(file,report=(text,error)=>this.note(text,error)){
      if(!file)return;const seq=++this.uploadSequence;
      const update=(text,error=false)=>{if(!this.dead&&seq===this.uploadSequence)report(text,error);};
      try{
        if(!/\.(png|webp|jpe?g)$/i.test(file.name))throw Error('Chọn ảnh PNG, WebP, JPG hoặc JPEG.');
        if(!file.size||file.size>5*1024*1024)throw Error('Logo cần có dữ liệu và không quá 5 MB.');
        update('Đang tải logo…');
        const post=(content,name)=>fetch('/api/studio/branding/logo?'+new URLSearchParams({filepath:this.c.state().filepath,filename:name}),{method:'POST',body:content});
        let response=await post(file,file.name),error=response.ok?'':await this.responseError(response,'Không tải được logo');
        // Older running servers accept only PNG/WebP. Keep the open session usable.
        if(!response.ok&&response.status===400&&/\.jpe?g$/i.test(file.name)&&error.includes('PNG/WebP')){
          let image;try{image=await createImageBitmap(file);}catch(_){throw Error('Không đọc được ảnh JPG. Hãy chọn một tệp ảnh hợp lệ.');}
          try{if(image.width>4096||image.height>4096)throw Error('Logo không được quá 4096×4096 pixel.');
            const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;canvas.getContext('2d').drawImage(image,0,0);
            const png=await this.canvasBlob(canvas);if(png.size>5*1024*1024)throw Error('Máy chủ đang dùng bản cũ. Hãy khởi động lại ứng dụng để tải ảnh JPG này.');
            response=await post(png,'logo.png');error=response.ok?'':await this.responseError(response,'Không tải được logo');
          }finally{image.close();}
        }
        if(!response.ok)throw Error(response.status===404?'Máy chủ chưa có chức năng tải logo. Hãy khởi động lại ứng dụng.':error);
        const result=await response.json();if(!result.image)throw Error('Ứng dụng chưa trả ảnh logo hợp lệ.');if(this.dead||seq!==this.uploadSequence)return;
        this.c.modify(()=>{const cfg=this.config().logo;cfg.image=result.image;cfg.enabled=true;});
        update('Đã tải logo. Kéo logo trong khung để đặt vị trí.');this.c.renderProperties();
      }catch(error){update(error instanceof TypeError?'Không kết nối được ứng dụng. Kiểm tra ứng dụng đang chạy rồi chọn lại ảnh.':error.message,true);}
    }
    async browserFrame(at,aspect,signal){
      const main=this.c.video;let source=main,detached=false;
      try{
        if(!(main.readyState>=2&&!main.seeking&&Math.abs(main.currentTime-at)<.1)){
          source=document.createElement('video');detached=true;source.preload='metadata';source.muted=true;source.playsInline=true;
          await new Promise((resolve,reject)=>{
            const target=Math.min(at,Math.max(0,this.c.state().media.duration-.05));
            const finish=error=>{clearTimeout(timer);source.removeEventListener('loadedmetadata',seek);for(const name of ['loadeddata','seeked','canplay'])source.removeEventListener(name,ready);source.removeEventListener('error',failed);signal.removeEventListener('abort',abort);error?reject(error):resolve();};
            const ready=()=>{if(source.readyState>=2&&!source.seeking&&Math.abs(source.currentTime-target)<.15)finish();};
            const seek=()=>{source.currentTime=target;ready();},failed=()=>finish(Error('Trình duyệt không đọc được khung hình tại mốc này.')),abort=()=>finish(new DOMException('Đã hủy','AbortError'));
            const timer=setTimeout(()=>finish(Error('Lấy ảnh quá lâu. Chọn mốc khác rồi bấm Lấy ảnh.')),8000);
            source.addEventListener('loadedmetadata',seek);for(const name of ['loadeddata','seeked','canplay'])source.addEventListener(name,ready);source.addEventListener('error',failed);signal.addEventListener('abort',abort,{once:true});
            if(signal.aborted){abort();return;}
            source.src=main.currentSrc||main.src||'/api/history/stream/'+this.c.state().filepath.split('/').map(encodeURIComponent).join('/');source.load();
          });
        }
        if(signal.aborted)throw new DOMException('Đã hủy','AbortError');
        const width=source.videoWidth,height=source.videoHeight;if(!width||!height)throw Error('Chưa đọc được hình ảnh trong video.');
        const ratio=aspect==='original'?width/height:Number(aspect.split(':')[0])/Number(aspect.split(':')[1]);
        const w=Math.max(2,Math.floor(Math.min(width,height*ratio)/2)*2),h=Math.max(2,Math.floor(Math.min(height,width/ratio)/2)*2),scale=Math.min(1,1280/Math.max(w,h));
        const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(w*scale));canvas.height=Math.max(1,Math.round(h*scale));
        canvas.getContext('2d').drawImage(source,Math.floor((width-w)/2),Math.floor((height-h)/2),w,h,0,0,canvas.width,canvas.height);
        return await this.canvasBlob(canvas,'image/jpeg');
      }finally{if(detached){source.pause();source.removeAttribute('src');source.load();}}
    }
    async loadStillFrame(force=false){
      const view=this.still;if(!view)return;
      const state=this.c.state(),aspect=state.project.studio_config.render.aspect,key=JSON.stringify([aspect,view.at]);
      if(!force&&view.key===key)return;view.key=key;const seq=++view.sequence;
      view.abort?.abort();view.abort=new AbortController();
      const ratio=aspect==='original'?state.media.width/state.media.height:Number(aspect.split(':')[0])/Number(aspect.split(':')[1]);
      view.stage.style.setProperty('--still-ratio',ratio);view.status.textContent='Đang lấy ảnh xem trước…';view.status.classList.remove('danger');view.img.hidden=true;
      let fallback=false,reason='';
      try{
        let blob;
        try{const response=await fetch('/api/studio/branding/frame?'+new URLSearchParams({filepath:state.filepath,at:view.at,aspect}),{cache:'no-store',signal:view.abort.signal});
          if(!response.ok)throw Error(await this.responseError(response,response.status===404?'Ứng dụng đang chạy bản cũ, chưa có chức năng lấy ảnh':'Không lấy được ảnh nền'));
          if(!response.headers.get('content-type')?.startsWith('image/'))throw Error('Máy chủ chưa trả về ảnh xem trước hợp lệ.');blob=await response.blob();
        }catch(error){if(error.name==='AbortError')throw error;reason=error.message;fallback=true;blob=await this.browserFrame(view.at,aspect,view.abort.signal);}
        if(this.still!==view||view.sequence!==seq)return;
        const next=new Image(),url=URL.createObjectURL(blob);next.className='brandingStillBackground';next.alt='Khung hình làm nền để đặt logo';next.draggable=false;
        next.onload=()=>{URL.revokeObjectURL(url);if(this.still!==view||view.sequence!==seq)return;view.img.replaceWith(next);view.img=next;view.status.textContent=fallback?'Đã lấy ảnh trong trình duyệt. Bạn có thể kéo logo trên ảnh.':'Đã lấy ảnh nền. Kéo logo/chữ để đặt vị trí.';};
        next.onerror=()=>{URL.revokeObjectURL(url);if(this.still===view&&view.sequence===seq){view.status.textContent='Không đọc được ảnh nền. Chọn mốc khác rồi bấm Lấy ảnh.';view.status.classList.add('danger');}};next.src=url;
      }catch(error){if(this.still!==view||view.sequence!==seq||error.name==='AbortError')return;
        view.status.textContent=(reason?reason+' · ':'')+error.message+' Logo vẫn đặt được trên nền trống.';view.status.classList.add('danger');
      }
    }
    openStill(){
      if(this.still)return;this.c.pausePlayback?.();this.c.video.pause();const c=this.c,{el,button}=c,state=c.state();
      const overlay=el('div',undefined,'popup brandingStillDialog'),box=el('section'),header=el('div',undefined,'toolbar'),title=el('h3','Ảnh preview · Đặt logo / watermark');
      overlay.setAttribute('role','dialog');overlay.setAttribute('aria-modal','true');overlay.setAttribute('aria-label','Ảnh preview đặt logo');
      const close=button('Đóng',()=>this.closeStill());header.append(title,close);box.append(header,el('p','Không cần phát video. Chọn ảnh nền rồi kéo logo/chữ trực tiếp. Khung này hiển thị tất cả thành phần đã bật để chỉnh vị trí; thời gian xuất hiện vẫn giữ theo thiết lập.','muted'));
      const tools=el('div',undefined,'toolbar'),time=el('input');time.type='number';time.min=0;time.max=state.media.duration;time.step=.1;time.value=String(c.video.currentTime||0);time.setAttribute('aria-label','Mốc lấy ảnh (giây)');
      const timeLabel=el('label','Mốc ảnh (giây) ');timeLabel.append(time);
      const aspect=el('select');aspect.setAttribute('aria-label','Tỷ lệ khung hình khi xuất');for(const value of ['original','9:16','16:9','1:1']){const opt=el('option',value==='original'?'Tỷ lệ gốc':value);opt.value=value;aspect.append(opt);}aspect.value=state.project.studio_config.render.aspect;
      aspect.onchange=()=>c.modify(()=>c.state().project.studio_config.render.aspect=aspect.value);
      tools.append(timeLabel,button('Lấy ảnh',()=>{const at=Number(time.value);if(!Number.isFinite(at)||at<0||at>state.media.duration)throw Error('Mốc ảnh phải nằm trong thời lượng video');this.still.at=at;this.loadStillFrame(true);}),aspect);
      const upload=el('input');upload.type='file';upload.accept='.png,.webp,.jpg,.jpeg';upload.setAttribute('aria-label','Chọn logo cho ảnh preview');
      upload.onchange=()=>{const file=upload.files[0];upload.value='';this.uploadLogo(file,(text,error)=>{if(this.still){this.still.uploadStatus.textContent=text;this.still.uploadStatus.classList.toggle('danger',error);}else this.note(text,error);});};tools.append(upload);
      const stage=el('div',undefined,'brandingStillStage'),img=el('img');img.className='brandingStillBackground';img.hidden=true;stage.append(img);
      const status=el('p','Đang lấy ảnh xem trước…'),uploadStatus=el('p'),positionStatus=el('p');for(const item of [status,uploadStatus,positionStatus])item.setAttribute('role','status');status.dataset.stillStatus='frame';uploadStatus.dataset.stillStatus='upload';positionStatus.dataset.stillStatus='position';
      box.append(tools,uploadStatus,stage,status,positionStatus,button('Lưu vị trí',async()=>{const saved=await c.save();if(this.still)this.still.positionStatus.textContent=saved?'Đã lưu vị trí logo/watermark.':'Còn thay đổi mới chưa lưu.';},'primary'));
      overlay.append(box);c.root.append(overlay);this.still={overlay,stage,img,status,uploadStatus,positionStatus,at:Number(time.value),nodes:[],sequence:0,key:null};this.install();this.draw();this.loadStillFrame();close.focus();
    }
    closeStill(){if(!this.still)return;this.still.abort?.abort();this.still.overlay.remove();this.still=null;this.dragging=false;this.refresh();this.c.root.querySelector('.brandStillOpen')?.focus();}
    editor(){
      const c=this.c,{el,button,field,section}=c,panel=el('div'),state=c.state(),config=this.config();
      panel.append(el('h3','Thương hiệu'),el('p','Mở ảnh preview để kéo và đặt logo/chữ, không cần phát video. Phụ đề luôn nằm trên thương hiệu.','muted'));
      panel.append(button('Mở ảnh preview để đặt logo',()=>this.openStill(),'primary full brandStillOpen'));
      const status=el('p',this.status);status.dataset.brandStatus='';status.classList.toggle('danger',!!this.statusError);status.setAttribute('role','status');const warning=el('p','','danger');warning.dataset.brandWarning='';panel.append(status,warning);
      const presetPanel=section('Bộ thương hiệu'),select=el('select');select.setAttribute('aria-label','Bộ thương hiệu đã lưu');select.append(el('option','Chọn bộ thương hiệu'));select.options[0].value='';
      for(const preset of this.presets){const option=el('option',preset.name);option.value=preset.id;select.append(option);}
      const name=el('input');name.placeholder='Tên bộ thương hiệu';name.maxLength=80;name.setAttribute('aria-label','Tên bộ thương hiệu');
      const reload=async()=>{this.presets=(await c.api('/branding/presets')).presets;c.renderProperties();};
      presetPanel.append(select,name,button('Áp dụng bộ',async()=>{if(!select.value)throw Error('Chọn bộ thương hiệu');await c.save();c.remember();const result=await c.api('/branding/presets/'+select.value+'/apply','POST',{filepath:state.filepath,expected_revision:c.state().project.revision});c.accept(result);c.renderProperties();this.refresh();}),button('Lưu bộ mới',async()=>{await c.api('/branding/presets','POST',{filepath:state.filepath,name:name.value,branding:config});await reload();}),button('Cập nhật bộ',async()=>{if(!select.value)throw Error('Chọn bộ thương hiệu');await c.api('/branding/presets/'+select.value,'PUT',{filepath:state.filepath,name:name.value||select.selectedOptions[0].textContent,branding:config});await reload();}),button('Đổi tên bộ',async()=>{if(!select.value)throw Error('Chọn bộ thương hiệu');await c.api('/branding/presets/'+select.value,'PATCH',{name:name.value});await reload();}),button('Xóa bộ',async()=>{if(!select.value)throw Error('Chọn bộ thương hiệu');if(confirm('Xóa bộ này? Logo trong các dự án đã áp dụng vẫn được giữ.')){await c.api('/branding/presets/'+select.value,'DELETE');await reload();}}));
      panel.append(presetPanel);
      if(!this.loaded){this.loaded=true;c.api('/branding/presets').then(result=>{if(this.dead)return;this.presets=result.presets;c.renderProperties();}).catch(e=>this.note(e.message));}
      for(const [kind,title] of [['logo','Logo ảnh'],['text','Watermark chữ']]){
        const layer=config[kind],box=section(title),change=(key,value)=>{layer[key]=value;};
        box.append(field('Bật '+title,layer.enabled,'checkbox',v=>{if(kind==='logo'&&v&&!layer.image)throw Error('Tải logo trước');if(kind==='text'&&v&&!layer.content)layer.content='Tên thương hiệu';layer.enabled=v;}));
        if(kind==='logo'){
          const input=el('input');input.type='file';input.accept='.png,.webp,.jpg,.jpeg';input.setAttribute('aria-label','Tải logo PNG, WebP hoặc JPG');input.onchange=()=>{const file=input.files[0];input.value='';this.uploadLogo(file);};box.append(input,el('small','PNG, WebP, JPG/JPEG · tối đa 5 MB','muted'));
        }else{
          const textarea=el('textarea');textarea.value=layer.content;textarea.maxLength=200;textarea.setAttribute('aria-label','Nội dung watermark');textarea.onchange=()=>c.modify(()=>layer.content=textarea.value);box.append(textarea);
          box.append(field('Chữ đậm',layer.bold,'checkbox',v=>change('bold',v)),field('Màu watermark',layer.color,'color',v=>change('color',v)),field('Màu viền watermark',layer.outline_color,'color',v=>change('outline_color',v)),field('Cỡ watermark',layer.size,'range',v=>change('size',v),{min:12,max:120,step:1}),field('Độ dày viền watermark',layer.outline,'range',v=>change('outline',v),{min:0,max:10,step:.5}));
        }
        for(const [key,label,min,max,step] of [['width','Chiều rộng',.02,1,.01],['opacity','Độ mờ',0,1,.05],['x','Vị trí ngang',0,1,.01],['y','Vị trí dọc',0,1,.01]])box.append(field(title+' · '+label,layer[key],'range',v=>change(key,v),{min,max,step}));
        const corners=el('div',undefined,'toolbar');for(const [label,right,bottom] of [['Trên trái',false,false],['Trên phải',true,false],['Dưới trái',false,true],['Dưới phải',true,true]])corners.append(button(label,()=>{const layout=this.layers.find(l=>l.kind===kind);c.modify(()=>{layer.x=right?Math.max(0,.97-(layout?.width??layer.width)):.03;layer.y=bottom?Math.max(0,.97-(layout?.height??.1)):.03;});c.renderProperties();}));box.append(corners);
        box.append(field(title+' · Hiện suốt video',layer.start===null,'checkbox',v=>{layer.start=v?null:0;layer.end=v?null:state.media.duration;c.renderProperties();}));
        if(layer.start!==null)for(const key of ['start','end'])box.append(field(title+(key==='start'?' · Bắt đầu':' · Kết thúc'),layer[key],'number',v=>change(key,v),{min:0,max:state.media.duration,step:.1}));
        panel.append(box);
      }
      return panel;
    }
    close(){this.closeStill();this.dead=true;++this.sequence;clearTimeout(this.timer);this.nodes.forEach(n=>n.remove());}
  };
})();
