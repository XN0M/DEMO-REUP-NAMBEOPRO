// Folder controls use DOM text nodes for all user-supplied names and errors.
let folderDialog = null;
let folderBusy = false;
let recentlyMoved = new Set();
const folderButtonStyle = 'rounded-xl border border-slate-600 px-4 py-2 text-sm hover:bg-slate-700 disabled:opacity-40';
function folderNode(tag, text, className = '') {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  node.className = className;
  return node;
}
function folderButton(text, action) {
  const button = folderNode('button', text, folderButtonStyle);
  button.type = 'button'; button.onclick = action;
  return button;
}
function folderStorageKey() { return 'nambeo.folder:' + document.getElementById('folderInput').value.trim(); }
function selectDownloadFolder(name) {
  currentSelectedCollection = collections.includes(name) ? name : 'Chung';
  try { localStorage.setItem(folderStorageKey(), currentSelectedCollection); } catch (_) {}
  renderCollectionDropdown();
}
function renderCollectionDropdown() {
  const name = document.getElementById('downloadFolderName');
  if (name) name.textContent = currentSelectedCollection;
  updateDownloadLabels();
}
function updateDownloadLabels() {
  const count = document.querySelectorAll('.video-checkbox:checked').length;
  const bulk = document.getElementById('downloadSelectedLabel');
  if (bulk) bulk.textContent = `Tải ${count} video vào ${currentSelectedCollection}`;
  document.querySelectorAll('[data-download-label]').forEach(el => { el.textContent = `Tải vào ${currentSelectedCollection}`; });
}
function folderTile(name, count, selected, action) {
  const button = folderButton('', action);
  button.className = 'min-w-0 text-left rounded-2xl border-2 p-4 space-y-2 ' + (selected ? 'border-amber-400 bg-amber-500/15 text-amber-200' : 'border-slate-700 bg-slate-900 hover:border-slate-500');
  button.dataset.folderTile = '';
  button.setAttribute('aria-pressed', String(selected));
  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  icon.setAttribute('viewBox', '0 0 24 24'); icon.classList.add('nb-folder-icon'); icon.setAttribute('aria-hidden', 'true');
  const shape = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  shape.setAttribute('d', 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10H3Z');shape.setAttribute('stroke-linejoin','round');icon.append(shape);
  const check = folderNode('span', selected ? '✓' : '', 'nb-folder-check');check.setAttribute('aria-hidden','true');
  button.append(icon, folderNode('span', name, 'nb-folder-name'), folderNode('span', `${count} video`, 'nb-folder-count'), check);
  return button;
}
function renderCollectionFilterPills() {
  const bar = document.getElementById('collectionFilterBar');
  bar.replaceChildren();
  const entries = [['ALL', 'Tất cả'], ...collections.map(c => [c, c])];
  entries.forEach(([key, title]) => bar.append(folderTile(title, key === 'ALL' ? historyVideos.length : historyVideos.filter(v => v.collection === key).length, activeHistoryFilter === key, () => {
    activeHistoryFilter = key; recentlyMoved.clear(); renderCollectionFilterPills(); renderHistoryCards();
  })));
}
function downloadIntoFolder(name = activeHistoryFilter) {
  selectDownloadFolder(name === 'ALL' ? 'Chung' : name); switchTab('new');
}
function renderFolderEmpty(container) {
  container.replaceChildren();
  const panel = folderNode('div', undefined, 'col-span-full py-12 text-center space-y-4');
  panel.append(folderNode('p', activeHistoryFilter === 'ALL' ? 'Chưa có video nào.' : `Thư mục “${activeHistoryFilter}” chưa có video.`));
  const actions = folderNode('div', undefined, 'flex flex-wrap justify-center gap-3');
  actions.append(folderButton('Tải video vào đây', () => downloadIntoFolder()), folderButton('Chọn video từ lịch sử', () => openFolderPicker('import', [], activeHistoryFilter === 'ALL' ? 'Chung' : activeHistoryFilter)));
  panel.append(actions); container.append(panel);
}
function openMoveFolder(paths) {
  const selected = paths || Array.from(document.querySelectorAll('.history-checkbox:checked'), cb => decodeURIComponent(cb.dataset.relPath));
  if (!selected.length) return showToast('Chọn video cần chuyển', true);
  openFolderPicker('move', selected);
}
function openFolderPicker(mode = 'download', paths = [], target = '') {
  folderDialog = {mode, paths: new Set(paths), target: target || (mode === 'download' ? currentSelectedCollection : ''), query: ''};
  document.getElementById('folderPicker').classList.remove('hidden');
  document.getElementById('folderSearch').value = '';
  document.getElementById('folderResult').replaceChildren();
  renderFolderPicker(); document.getElementById('folderSearch').focus();
}
function closeFolderPicker() {
  if (folderBusy) return;
  folderDialog = null; document.getElementById('folderPicker').classList.add('hidden');
}
function renderFolderPicker() {
  const state = folderDialog;
  if (!state) return;
  const importing = state.mode === 'import';
  document.getElementById('folderPickerTitle').textContent = importing ? `Thêm video vào ${state.target}` : state.mode === 'download' ? 'Chọn thư mục lưu video' : `Chuyển ${state.paths.size} video`;
  const current = [...new Set(historyVideos.filter(v => state.paths.has(v.rel_path)).map(v => v.collection))];
  document.getElementById('folderPickerInfo').textContent = current.length ? `Thư mục hiện tại: ${current.join(', ')}` : 'Chọn đích lưu video của bạn';
  const grid = document.getElementById('folderPickerGrid'); grid.replaceChildren();
  const query = state.query.toLocaleLowerCase();
  if (importing) {
    historyVideos.filter(v => v.collection !== state.target && `${v.title} ${v.collection}`.toLocaleLowerCase().includes(query)).forEach(v => {
      const label = folderNode('label', undefined, 'flex items-start gap-3 rounded-xl border border-slate-700 p-3 cursor-pointer');
      const check = document.createElement('input'); check.type = 'checkbox'; check.checked = state.paths.has(v.rel_path); check.disabled = folderBusy;
      check.onchange = () => { check.checked ? state.paths.add(v.rel_path) : state.paths.delete(v.rel_path); renderFolderPicker(); };
      label.append(check, folderNode('span', `${v.title || v.filename || v.rel_path} — Thư mục: ${v.collection}`, 'text-sm break-words')); grid.append(label);
    });
  } else {
    collections.filter(c => c.toLocaleLowerCase().includes(query)).forEach(c => {
      const tile = folderTile(c, historyVideos.filter(v => v.collection === c).length, c === state.target, () => { state.target = c; renderFolderPicker(); });
      tile.disabled = folderBusy; grid.append(tile);
    });
  }
  if (!grid.children.length) grid.append(folderNode('p', 'Không có kết quả phù hợp.', 'text-slate-400'));
  const submit = document.getElementById('folderSubmit');
  submit.disabled = folderBusy || !state.target || (state.mode !== 'download' && !state.paths.size);
  submit.textContent = folderBusy ? 'Đang chuyển…' : state.mode === 'download' ? `Chọn ${state.target}` : `Chuyển ${state.paths.size} video vào ${state.target || '…'}`;
  document.getElementById('folderCreate').disabled = folderBusy || importing;
}
async function submitFolderPicker() {
  const state = folderDialog;
  if (!state || folderBusy) return;
  if (state.mode === 'download') { selectDownloadFolder(state.target); closeFolderPicker(); return; }
  folderBusy = true; renderFolderPicker();
  const resultPanel = document.getElementById('folderResult'); resultPanel.replaceChildren();
  try {
    const res = await fetch('/api/history/move', {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({filepaths:[...state.paths], collection:state.target})});
    const data = await res.json(); if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Yêu cầu chuyển không hợp lệ');
    recentlyMoved = new Set(data.moved.map(v => v.new_filepath));
    activeHistoryFilter = state.target;
    await loadHistory(); await loadCollections();
    resultPanel.append(folderNode('p', `Đã chuyển ${data.moved.length} video; ${data.skipped.length} video đã ở đích; ${data.failed.length} lỗi.`, 'text-emerald-300'));
    data.failed.forEach(f => resultPanel.append(folderNode('p', `${f.filepath}: ${f.error}`, 'text-rose-300 break-words')));
    state.paths = new Set(data.failed.map(f => f.filepath));
    if (!data.failed.length) { folderBusy = false; closeFolderPicker(); showToast(`Đã chuyển ${data.moved.length} video vào ${state.target}`); }
  } catch (error) {
    resultPanel.append(folderNode('p', `Không xác nhận được kết quả: ${error.message}. Hãy làm mới lịch sử trước khi thử lại.`, 'text-rose-300'));
  } finally { folderBusy = false; renderFolderPicker(); }
}
function initFolderUI() {
  const panel = folderNode('section', undefined, 'rounded-2xl border border-amber-500/40 bg-slate-900 p-5 space-y-3');
  panel.append(folderNode('h2', 'Thư mục lưu video', 'text-sm text-slate-400'));
  const name = folderNode('p', 'Chung', 'text-xl font-semibold text-amber-300 break-words'); name.id = 'downloadFolderName'; panel.append(name);
  const actions = folderNode('div', undefined, 'flex flex-wrap gap-2');
  actions.append(folderButton('Đổi thư mục', () => openFolderPicker()), folderButton('Tạo thư mục', () => openCreateCollectionModal())); panel.append(actions);
  document.getElementById('tabNewSection').prepend(panel);
  document.getElementById('collectionFilterBar').className = 'grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3';
  document.getElementById('folderSearch').oninput = e => { folderDialog.query = e.target.value; renderFolderPicker(); };
  document.addEventListener('change', e => { if (e.target.matches('.video-checkbox,#checkAll')) updateDownloadLabels(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && folderDialog && document.getElementById('collectionModal').classList.contains('hidden')) { e.stopImmediatePropagation(); closeFolderPicker(); } }, true);
}
