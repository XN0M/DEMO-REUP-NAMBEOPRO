/* Shared presentation layer. Media processing remains in the existing handlers. */
(() => {
  const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
  const action=(text,fn,cls='nb-button')=>{const e=node('button',text,cls);e.type='button';e.onclick=fn;return e;};
  function init(){
    document.title='Nambeo · Video Studio';
    const main=document.querySelector('body>main'),oldHeader=document.querySelector('body>header');
    const sidebar=node('aside',undefined,'nb-sidebar');sidebar.setAttribute('aria-label','Điều hướng chính');
    const brand=node('div',undefined,'nb-brand');brand.append(node('span','N','nb-logo'),node('span','Nambeo'));sidebar.append(brand);
    const nav=node('nav');const navButtons={};
    const badge=document.getElementById('historyCountBadge');
    for(const [key,label,glyph] of [['new','Tải video','↓'],['history','Thư viện','▦'],['settings','Cài đặt','⚙']]){const b=action('',()=>navigate(key),'nb-nav');b.append(node('span',glyph),node('span',label));b.dataset.page=key;if(key==='history')b.append(badge);navButtons[key]=b;nav.append(b);}
    sidebar.append(nav,node('div','Không gian làm việc video của bạn','nb-sidebar-foot'));document.body.prepend(sidebar);
    document.getElementById('toast').setAttribute('role','status');document.getElementById('toast').setAttribute('aria-live','polite');
    const pageHeader=node('div',undefined,'nb-page-header'),heading=node('div');heading.append(node('div','WORKSPACE','nb-kicker'),node('h1','Tải video'),node('p','Chọn thư mục, thêm đường dẫn và bắt đầu tải.'));pageHeader.append(heading,node('span','Lưu trên máy của bạn','nb-chip'));main.prepend(pageHeader);
    const folderPanel=document.getElementById('folderInput').closest('main>div');
    const settings=node('section',undefined,'nb-settings hidden');settings.id='tabSettingsSection';
    const storage=node('section',undefined,'nb-panel');storage.append(node('h2','Thư mục tải gốc'),node('p','Các thư mục video và tài nguyên Studio được lưu tại vị trí này.'),folderPanel);settings.append(storage);
    const ai=node('section',undefined,'nb-panel');ai.append(node('h2','Dịch thuật và giọng đọc'),node('p','Quản lý nhà cung cấp AI, model và khóa API được dùng trong ứng dụng.'));
    const aiButton=document.getElementById('btnAiSettingsHeader');aiButton.className='nb-button';aiButton.querySelectorAll('.hidden').forEach(e=>e.classList.remove('hidden'));ai.append(aiButton);settings.append(ai);main.append(settings);
    oldHeader.setAttribute('aria-hidden','true');
    const originalSwitch=window.switchTab;
    window.switchTab=tab=>navigate(tab);
    function navigate(page){settings.classList.toggle('hidden',page!=='settings');if(page==='settings'){document.getElementById('tabNewSection').classList.add('hidden');document.getElementById('tabHistorySection').classList.add('hidden');}else if(page!=='history'||document.getElementById('tabHistorySection').classList.contains('hidden'))originalSwitch(page);for(const [key,b] of Object.entries(navButtons)){b.classList.toggle('active',key===page);if(key===page)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');}heading.querySelector('h1').textContent={new:'Tải video',history:'Thư viện',settings:'Cài đặt'}[page];heading.querySelector('p').textContent={new:'Chọn thư mục, thêm đường dẫn và bắt đầu tải.',history:'Sắp xếp video và tiếp tục công việc trong Studio.',settings:'Thiết lập nơi lưu và dịch vụ cho không gian làm việc.'}[page];}
    const history=document.getElementById('tabHistorySection'),grid=document.getElementById('historyGrid');
    const tools=node('div',undefined,'nb-library-tools');const search=node('input');search.id='historySearch';search.type='search';search.placeholder='Tìm tiêu đề hoặc tên file…';search.setAttribute('aria-label','Tìm video trong thư mục');const sort=node('select');sort.id='historySort';sort.setAttribute('aria-label','Sắp xếp video');for(const [value,text] of [['newest','Mới nhất'],['oldest','Cũ nhất'],['name','Tên A–Z']]){const option=node('option',text);option.value=value;sort.append(option);}const all=document.getElementById('checkAllHistory').closest('label');tools.append(search,sort,all);history.insertBefore(tools,document.getElementById('historyBulkActionBar'));search.oninput=()=>renderHistoryCards();sort.onchange=()=>renderHistoryCards();
    // Hide bulk actions when nothing is selected, preserving the standalone select-all control.
    const update=window.updateHistorySelectedCount;window.updateHistorySelectedCount=()=>{update();const count=document.querySelectorAll('.history-checkbox:checked').length;document.getElementById('historyBulkActionBar').classList.toggle('hidden',!count);grid.querySelectorAll('[data-history-card]').forEach(card=>card.classList.toggle('nb-selected',!!card.querySelector('input:checked')));};
    document.getElementById('historyBulkActionBar').classList.add('hidden');
    const download=document.getElementById('downloadFolderName');if(download)download.closest('section').id='downloadFolderPanel';
    const downloadSummary=node('section',undefined,'nb-panel hidden');downloadSummary.id='downloadSummary';downloadSummary.setAttribute('aria-live','polite');document.getElementById('resultsSection').append(downloadSummary);
    window.updateDownloadSummary=tasks=>{const shown=Object.entries(tasks).filter(([id])=>document.getElementById(`progressBox-${id}`)).map(([,task])=>task);if(!shown.length)return;const success=shown.filter(t=>t.status==='completed').length,failed=shown.filter(t=>t.status==='failed').length,remaining=shown.length-success-failed;downloadSummary.classList.remove('hidden');downloadSummary.replaceChildren(node('h2',remaining?'Đang tải video':failed?(success?'Hoàn tất một phần':'Tải video thất bại'):'Đã tải xong'),node('p',`${success} thành công · ${failed} thất bại${remaining?` · ${remaining} đang xử lý`:''}`));};
    const originalLoad=window.loadHistory;window.loadHistory=async()=>{const summary=document.getElementById('historySummary');summary.textContent='Đang tải thư viện…';grid.setAttribute('aria-busy','true');try{await originalLoad();}finally{grid.removeAttribute('aria-busy');}};
    navigate(currentTab==='history'?'history':'new');
    // Trap focus in the topmost visible dialog, including nested Studio popups.
    const openers=new WeakMap();let lastActive=document.activeElement,lastOutside=lastActive;
    document.addEventListener('focusin',()=>{lastActive=document.activeElement;if(!lastActive.closest('.fixed.inset-0,.ns'))lastOutside=lastActive;});
    const dialogLevel=e=>(parseInt(getComputedStyle(e).zIndex)||0)+(e.classList.contains('popup')?80:0);
    const dialogs=()=>[...document.querySelectorAll('body>div.fixed.inset-0,.ns,.ns .popup')].filter(e=>!e.classList.contains('hidden')&&e.getClientRects().length).sort((a,b)=>dialogLevel(a)-dialogLevel(b));
    const visible=new Set();
    const sync=()=>{const current=dialogs();for(const old of visible)if(!current.includes(old)){visible.delete(old);const opener=openers.get(old);if(opener?.isConnected)opener.focus();}for(const d of current)if(!visible.has(d)){visible.add(d);openers.set(d,d.contains(lastActive)?lastOutside:lastActive);d.setAttribute('role','dialog');d.setAttribute('aria-modal','true');d.querySelectorAll('button').forEach(b=>{if(!b.textContent.trim()&&/^close/.test(b.getAttribute('onclick')||''))b.setAttribute('aria-label','Đóng');});const title=d.querySelector('h2,h3');if(title){title.id||='nb-dialog-'+Math.random().toString(36).slice(2);d.setAttribute('aria-labelledby',title.id);}if(d!==document.activeElement&&!d.contains(document.activeElement)){const first=d.querySelector('input:not([type=hidden]),button,select,textarea,[href],summary');first?.focus();}}};
    new MutationObserver(sync).observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class']});
    document.addEventListener('keydown',e=>{const stack=dialogs(),dialog=stack.at(-1);if(!dialog)return;if(e.key==='Tab'){const items=[...dialog.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex="0"]')].filter(el=>!el.disabled&&el.tabIndex>=0&&el.getClientRects().length);if(!items.length){e.preventDefault();return;}const first=items[0],last=items.at(-1);if(e.shiftKey&&(document.activeElement===first||!dialog.contains(document.activeElement))){e.preventDefault();last.focus();}else if(!e.shiftKey&&(document.activeElement===last||!dialog.contains(document.activeElement))){e.preventDefault();first.focus();}}if(e.key==='Escape'){const close=[...dialog.querySelectorAll('button')].find(b=>/^(Đóng|Quay lại thư viện)/.test(b.textContent.trim())||/Đóng/.test(b.title)||/^close/.test(b.getAttribute('onclick')||''));if(close){e.preventDefault();e.stopImmediatePropagation();close.click();}}},true);
    sync();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
