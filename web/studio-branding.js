/* Studio branding editor. Geometry and PNG glyphs come from the render backend. */
(() => {
  const copy=v=>JSON.parse(JSON.stringify(v));
  window.NambeoBranding=class {
    constructor(ctx){this.c=ctx;this.layers=[];this.nodes=[];this.sequence=0;this.key='';this.timer=null;this.dead=false;this.presets=[];this.status='';this.refresh();}
    config(){return this.c.state().project.studio_config.branding;}
    url(key){return '/api/studio/branding/image/'+key+'?'+new URLSearchParams({filepath:this.c.state().filepath});}
    note(text){this.status=text;this.c.root.querySelectorAll('[data-brand-status]').forEach(n=>n.textContent=text);}
    refresh(){
      if(this.dead)return;
      const state=this.c.state(),body={filepath:state.filepath,branding:this.config(),aspect:state.project.studio_config.render.aspect},key=JSON.stringify(body);
      this.draw();if(key===this.key)return;this.key=key;
      const seq=++this.sequence;clearTimeout(this.timer);this.note('Đang cập nhật xem thử');
      this.timer=setTimeout(async()=>{try{const response=await this.c.api('/branding/preview','POST',body);if(this.dead||seq!==this.sequence)return;
        this.layers=response.layers;this.appliedConfig=body.branding;this.install();this.note('');this.draw();
      }catch(error){if(this.dead||seq!==this.sequence)return;this.layers=[];this.install();this.note(error.message);}},180);
    }
    install(){
      this.nodes.forEach(n=>n.remove());this.nodes=[];
      for(const layer of this.layers){
        const box=document.createElement('div');box.className='brandingOverlay';box.dataset.brandKind=layer.kind;box.setAttribute('aria-label',layer.kind==='logo'?'Di chuyển logo':'Di chuyển watermark chữ');
        const img=document.createElement('img');img.src=this.url(layer.image);img.draggable=false;img.alt=layer.kind==='logo'?'Logo':'Watermark chữ';
        img.onerror=()=>this.note('Không tải được ảnh thương hiệu. Hãy cập nhật xem thử.');
        const handle=document.createElement('span');handle.className='brandingResize';handle.textContent='↘';box.append(img,handle);this.c.stage.append(box);this.nodes.push(box);
        box.onpointerdown=e=>{if(!this.c.editing())return;e.preventDefault();e.stopPropagation();this.c.remember();box.setPointerCapture(e.pointerId);
          const cfg=this.config()[layer.kind],rect=this.c.stage.getBoundingClientRect(),initial={px:e.clientX,py:e.clientY,x:cfg.x,y:cfg.y,width:cfg.width},resize=e.target===handle;
          box.onpointermove=ev=>{const dx=(ev.clientX-initial.px)/rect.width,dy=(ev.clientY-initial.py)/rect.height;
            if(resize)cfg.width=Math.max(.02,Math.min(1-cfg.x,initial.width+dx));else{cfg.x=Math.max(0,Math.min(1-layer.width,initial.x+dx));cfg.y=Math.max(0,Math.min(1-layer.height,initial.y+dy));}
            this.c.changed();this.draw();};
          const finish=()=>{box.onpointermove=null;box.onpointerup=null;box.onpointercancel=null;this.c.renderProperties();};box.onpointerup=finish;box.onpointercancel=finish;
        };
      }
    }
    draw(){
      const now=this.c.video.currentTime;let overlaps=false;
      this.layers.forEach((layer,i)=>{const box=this.nodes[i];if(!box)return;const cfg=this.config()[layer.kind];
        const show=cfg.enabled&&(cfg.start===null||(now>=cfg.start&&now<cfg.end));box.hidden=!show;
        const factor=layer.kind==='logo'?Math.min(cfg.width/(this.appliedConfig?.logo.width||cfg.width),1/layer.width,1/layer.height):1,w=Math.min(1,layer.width*factor),h=Math.min(1,layer.height*factor);
        const x=Math.max(0,Math.min(1-w,cfg.x)),y=Math.max(0,Math.min(1-h,cfg.y));
        Object.assign(box.style,{left:x*100+'%',top:y*100+'%',width:w*100+'%',height:h*100+'%',opacity:cfg.opacity,pointerEvents:this.c.editing()?'auto':'none'});
        box.classList.toggle('editable',this.c.editing());
        if(show&&this.c.caption.textContent){const a=box.getBoundingClientRect(),b=this.c.caption.getBoundingClientRect();overlaps ||= a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;}
      });
      this.c.root.querySelectorAll('[data-brand-warning]').forEach(n=>n.textContent=overlaps?'Logo/watermark đang chạm vùng phụ đề. Điều chỉnh vị trí nếu cần.':'');
    }
    editor(){
      const c=this.c,{el,button,field,section}=c,panel=el('div'),state=c.state(),config=this.config();
      panel.append(el('h3','Thương hiệu'),el('p','Kéo logo/chữ trên video để đổi vị trí; kéo góc ↘ để đổi chiều rộng. Phụ đề luôn nằm trên thương hiệu.','muted'));
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
    close(){this.dead=true;++this.sequence;clearTimeout(this.timer);this.nodes.forEach(n=>n.remove());}
  };
})();
