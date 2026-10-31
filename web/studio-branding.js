/* Studio branding editor. Geometry and PNG glyphs come from the render backend. */
(() => {
  const copy=v=>JSON.parse(JSON.stringify(v));
  window.NambeoBranding=class {
    constructor(ctx){this.c=ctx;this.layers=[];this.nodes=[];this.sequence=0;this.key='';this.timer=null;this.dead=false;this.presets=[];this.still=null;this.dragging=false;this.status='';this.refresh();}
    config(){return this.c.state().project.studio_config.branding;}
    url(key){return '/api/studio/branding/image/'+key+'?'+new URLSearchParams({filepath:this.c.state().filepath});}
    note(text){this.status=text;this.c.root.querySelectorAll('[data-brand-status]').forEach(n=>n.textContent=text);}
    refresh(){
      if(this.dead)return;
      const state=this.c.state(),body={filepath:state.filepath,branding:copy(this.config()),aspect:state.project.studio_config.render.aspect},key=JSON.stringify(body);
      if(this.still)this.loadStillFrame();this.draw();if(this.dragging){this.key='';return;}if(key===this.key)return;this.key=key;
      const seq=++this.sequence;clearTimeout(this.timer);this.note('Đang cập nhật xem thử');
      this.timer=setTimeout(async()=>{try{const response=await this.c.api('/branding/preview','POST',body);if(this.dead||seq!==this.sequence)return;
        this.layers=response.layers;this.appliedConfig=body.branding;this.install();this.note('');this.draw();
      }catch(error){if(this.dead||seq!==this.sequence)return;this.layers=[];this.install();this.note(error.message);}},180);
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
    loadStillFrame(force=false){
      const view=this.still;if(!view)return;
      const state=this.c.state(),aspect=state.project.studio_config.render.aspect,key=JSON.stringify([aspect,view.at]);
      if(!force&&view.key===key)return;view.key=key;const seq=++view.sequence;
      const ratio=aspect==='original'?state.media.width/state.media.height:Number(aspect.split(':')[0])/Number(aspect.split(':')[1]);
      view.stage.style.setProperty('--still-ratio',ratio);view.status.textContent='Đang lấy ảnh xem trước…';view.img.hidden=true;
      const next=new Image();next.className='brandingStillBackground';next.alt='Khung hình làm nền để đặt logo';next.draggable=false;
      next.onload=()=>{if(this.still!==view||view.sequence!==seq)return;view.img.replaceWith(next);view.img=next;view.status.textContent='Kéo logo/chữ để đặt vị trí; kéo góc ↘ để đổi kích thước.';};
      next.onerror=()=>{if(this.still===view&&view.sequence===seq)view.status.textContent='Không lấy được ảnh. Chọn mốc khác và bấm Lấy ảnh; bạn vẫn có thể đặt logo trên nền trống.';};
      next.src='/api/studio/branding/frame?'+new URLSearchParams({filepath:state.filepath,at:view.at,aspect});
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
      const upload=el('input');upload.type='file';upload.accept='.png,.webp';upload.setAttribute('aria-label','Chọn logo cho ảnh preview');upload.onchange=async()=>{try{const file=upload.files[0];if(!file)return;if(file.size>5*1024*1024)throw Error('Logo tối đa 5 MB');const response=await fetch('/api/studio/branding/logo?'+new URLSearchParams({filepath:state.filepath,filename:file.name}),{method:'POST',body:file});const result=await response.json();if(!response.ok)throw Error(result.detail);if(this.dead)return;c.modify(()=>{const cfg=this.config().logo;cfg.image=result.image;cfg.enabled=true;});c.renderProperties();}catch(e){if(this.still)this.still.status.textContent=e.message;}};tools.append(upload);
      const stage=el('div',undefined,'brandingStillStage'),img=el('img');img.className='brandingStillBackground';img.hidden=true;stage.append(img);
      const status=el('p','Đang lấy ảnh xem trước…');status.setAttribute('role','status');
      box.append(tools,stage,status,button('Lưu vị trí',async()=>{const saved=await c.save();if(this.still)this.still.status.textContent=saved?'Đã lưu vị trí logo/watermark.':'Còn thay đổi mới chưa lưu.';},'primary'));
      overlay.append(box);c.root.append(overlay);this.still={overlay,stage,img,status,at:Number(time.value),nodes:[],sequence:0,key:null};this.install();this.draw();this.loadStillFrame();close.focus();
    }
    closeStill(){if(!this.still)return;this.still.overlay.remove();this.still=null;this.dragging=false;this.refresh();this.c.root.querySelector('.brandStillOpen')?.focus();}
    editor(){
      const c=this.c,{el,button,field,section}=c,panel=el('div'),state=c.state(),config=this.config();
      panel.append(el('h3','Thương hiệu'),el('p','Mở ảnh preview để kéo và đặt logo/chữ, không cần phát video. Phụ đề luôn nằm trên thương hiệu.','muted'));
      panel.append(button('Mở ảnh preview để đặt logo',()=>this.openStill(),'primary full brandStillOpen'));
      const status=el('p',this.status);status.dataset.brandStatus='';status.setAttribute('role','status');const warning=el('p','','danger');warning.dataset.brandWarning='';panel.append(status,warning);
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
          const input=el('input');input.type='file';input.accept='.png,.webp';input.setAttribute('aria-label','Tải logo PNG hoặc WebP');input.onchange=async()=>{try{const file=input.files[0];if(!file)return;if(file.size>5*1024*1024)throw Error('Logo tối đa 5 MB');const response=await fetch('/api/studio/branding/logo?'+new URLSearchParams({filepath:state.filepath,filename:file.name}),{method:'POST',body:file});const result=await response.json();if(!response.ok)throw Error(result.detail);if(this.dead)return;c.modify(()=>{layer.image=result.image;layer.enabled=true;});c.renderProperties();}catch(e){this.note(e.message);}};box.append(input);
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
