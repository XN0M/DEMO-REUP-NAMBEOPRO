/**
 * monitor.js — Quản lý Kênh theo dõi & Đăng bài Facebook cho Nambeo Pro
 * Hỗ trợ Master-Detail Workbench, Đa thư mục (Collections), 3 Tag trạng thái video.
 */

let monitorChannels = [];
let monitorVideos = [];
let pendingVideos = []; // Backward compatibility
let availableCollections = ['Chung'];
let selectedChannelId = 'all'; // 'all' hoặc channel.id
let statusFilter = 'all'; // 'all', 'pending', 'downloaded'
let fbConfig = { page_id: '', page_name: '', token_set: false, token_hint: '' };
let monitorSse = null;
let monitorDownloadPollTimer = null;
let selectedMonitorVideoIds = new Set();
let monitorListDeleteBusy = false;
let monitorVideoLoadSequence = 0;
let monitorUseLegacyDelete = false;

// Khởi tạo Monitor Module
async function initMonitor() {
  await Promise.all([
    loadMonitorChannels(),
    loadCollectionsList(),
    loadMonitorVideos(),
    loadFbConfig()
  ]);
  setupMonitorSSE();
}

// ─────────────────────────────────────────────────────────────
// SSE Real-time Updates
// ─────────────────────────────────────────────────────────────
function setupMonitorSSE() {
  if (monitorSse) {
    try { monitorSse.close(); } catch (e) {}
  }
  try {
    monitorSse = new EventSource('/api/channels/events');
    monitorSse.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'new_videos' || data.type === 'channel_status') {
          if (data.data?.length) showToast(`Phát hiện ${data.data.length} video mới trên kênh đang theo dõi!`);
          loadMonitorVideos();
          loadMonitorChannels();
        }
      } catch (err) {}
    };
    monitorSse.onerror = () => {
      // Reconnect handled automatically by EventSource
    };
  } catch (e) {
    console.warn('Không thể khởi tạo SSE Monitor:', e);
  }
}

// ─────────────────────────────────────────────────────────────
// Danh sách Thư mục (Collections)
// ─────────────────────────────────────────────────────────────
async function loadCollectionsList() {
  try {
    const res = await fetch('/api/collections');
    if (res.ok) {
      const data = await res.json();
      const list = Array.isArray(data) ? data : (data.collections || ['Chung']);
      availableCollections = list.map(c => typeof c === 'string' ? c : (c.name || 'Chung'));
      if (!availableCollections.includes('Chung')) {
        availableCollections.unshift('Chung');
      }
    }
  } catch (e) {
    console.warn('Lỗi nạp danh sách collections:', e);
    availableCollections = ['Chung'];
  }
}

// ─────────────────────────────────────────────────────────────
// Kênh theo dõi (Channels Master List)
// ─────────────────────────────────────────────────────────────
async function loadMonitorChannels() {
  try {
    const res = await fetch('/api/channels');
    if (!res.ok) return;
    const data = await res.json();
    monitorChannels = data.channels || [];
    renderMonitorChannels();
    updateChannelCounterBadge();
  } catch (e) {
    console.error('Lỗi nạp danh sách kênh:', e);
  }
}

function updateChannelCounterBadge() {
  const countEl = document.getElementById('totalChannelsCount');
  if (countEl) {
    countEl.innerText = `${monitorChannels.length} kênh`;
  }
}

function renderMonitorChannels() {
  const container = document.getElementById('monitorChannelsList');
  if (!container) return;

  if (monitorChannels.length === 0) {
    container.innerHTML = `
      <div class="py-8 text-center text-slate-500 border border-dashed border-slate-800 rounded-xl">
        <i data-lucide="rss" class="w-8 h-8 mx-auto mb-2 opacity-40"></i>
        <p class="text-xs">Chưa có kênh nào được theo dõi.</p>
        <button onclick="openAddChannelModal()" class="mt-2 text-xs text-rose-400 hover:text-rose-300 underline font-medium">
          + Thêm kênh đầu tiên
        </button>
      </div>
    `;
    refreshIcons();
    return;
  }

  container.innerHTML = monitorChannels.map(ch => {
    let platformBadge = '';
    let platformIcon = '';
    if (ch.platform === 'youtube') {
      platformBadge = '<span class="nb-platform nb-badge-yt">YouTube</span>';
      platformIcon = '<i data-lucide="youtube" class="w-4 h-4 text-red-500 shrink-0"></i>';
    } else if (ch.platform === 'douyin') {
      platformBadge = '<span class="nb-platform nb-badge-dy">Douyin</span>';
      platformIcon = '<i data-lucide="video" class="w-4 h-4 text-cyan-400 shrink-0"></i>';
    } else if (ch.platform === 'facebook') {
      platformBadge = '<span class="nb-platform nb-badge-fb">Facebook</span>';
      platformIcon = '<i data-lucide="facebook" class="w-4 h-4 text-blue-400 shrink-0"></i>';
    }

    const lastAttempt = ch.last_attempt || ch.last_checked;
    const lastChecked = lastAttempt
      ? new Date(lastAttempt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', month: 'numeric', day: 'numeric' })
      : 'Chưa kiểm tra';

    const activeColor = ch.active ? 'color:#10b981;' : 'color:#64748b;';
    const isSelected = selectedChannelId === ch.id;
    const stateText = ch.checking ? 'Đang kiểm tra…' : ch.last_error?.message ||
      (ch.last_status === 'partial' ? (ch.last_message || 'Kiểm tra chưa đầy đủ') :
       ch.last_success ? 'Lần quét gần nhất thành công' : 'Chưa kiểm tra thành công');
    const successText = ch.last_success ? new Date(ch.last_success).toLocaleString() : 'Chưa có';

    return `
      <div class="nb-channel-item ${isSelected ? 'active' : ''}" data-channel-id="${ch.id}" onclick="selectChannelView('${ch.id}')">
        <div class="flex items-center space-x-3 min-w-0 flex-1">
          <div class="nb-icon-box">
            ${platformIcon}
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="font-medium text-xs text-slate-200 truncate max-w-[190px]" title="${escapeAttr(ch.name || ch.url)}">${escapeAttr(ch.name || ch.url)}</span>
              ${platformBadge}
              <span class="text-[10px] bg-[#141922] text-slate-300 px-1.5 py-0.5 rounded border border-[#252f3d] flex items-center gap-1">
                <i data-lucide="folder" class="w-2.5 h-2.5 text-amber-400"></i> ${escapeAttr(ch.collection || 'Chung')}
              </span>
            </div>
            ${ch.note ? `
            <div class="flex items-center gap-1 text-[11px] text-amber-300/80 mt-0.5 truncate max-w-[240px]" title="${escapeAttr(ch.note)}">
              <i data-lucide="file-text" class="w-2.5 h-2.5 text-amber-400/80 shrink-0"></i>
              <span class="truncate">${escapeAttr(ch.note)}</span>
            </div>` : ''}
            <div class="flex items-center gap-3 text-[11px] text-slate-400 mt-1">
              <button onclick="event.stopPropagation(); changeChannelInterval('${ch.id}', ${ch.check_interval_minutes || 15})" class="hover:text-rose-300 underline decoration-dotted flex items-center gap-1 transition" title="Bấm để tự cài đặt lại chu kỳ">
                <span>Chu kỳ: ${formatInterval(ch.check_interval_minutes)}</span>
                <i data-lucide="edit-3" class="w-2.5 h-2.5 opacity-60"></i>
              </button>
              <span>•</span>
              <button onclick="event.stopPropagation(); editChannelNote('${ch.id}')" class="hover:text-amber-300 underline decoration-dotted flex items-center gap-1 transition" title="Thêm/sửa ghi chú cho kênh">
                <span>${ch.note ? 'Sửa note' : '+ Note'}</span>
                <i data-lucide="file-edit" class="w-2.5 h-2.5 opacity-60"></i>
              </button>
              <span>•</span>
              <span>Lần thử: ${lastChecked}</span>
            </div>
            <div class="text-[11px] mt-1 ${ch.last_error ? 'text-rose-400' : ch.last_status === 'partial' ? 'text-amber-300' : 'text-slate-400'}"
                 title="Thành công gần nhất: ${escapeAttr(successText)}">${escapeAttr(stateText)}</div>
          </div>
        </div>

        <div class="flex items-center space-x-1 shrink-0">
          <button onclick="event.stopPropagation(); checkChannelNow('${ch.id}', this)" ${ch.checking ? 'disabled' : ''} class="nb-button" style="padding:4px 6px;" title="Kiểm tra ngay kênh này">
            <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>
          </button>
          <button onclick="event.stopPropagation(); toggleChannelActive('${ch.id}', ${!ch.active})" class="nb-button" style="padding:4px 6px; ${activeColor}" title="${ch.active ? 'Tắt theo dõi' : 'Bật theo dõi'}">
            <i data-lucide="${ch.active ? 'toggle-right' : 'toggle-left'}" class="w-4 h-4"></i>
          </button>
          <button onclick="event.stopPropagation(); deleteMonitorChannel('${ch.id}')" class="nb-button" style="padding:4px 6px;color:#f87171;" title="Xóa kênh">
            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
          </button>
        </div>
      </div>
    `;
  }).join('');

  refreshIcons();
}

// ─────────────────────────────────────────────────────────────
// Master-Detail Navigation (Chuyển kênh / Bảng tin tổng hợp)
// ─────────────────────────────────────────────────────────────
function selectChannelView(channelId) {
  if (selectedChannelId !== (channelId || 'all')) selectedMonitorVideoIds.clear();
  selectedChannelId = channelId || 'all';

  // Cập nhật trạng thái active ở cột trái
  const btnAll = document.getElementById('btnAllChannelsFeed');
  if (btnAll) {
    if (selectedChannelId === 'all') {
      btnAll.classList.add('active');
    } else {
      btnAll.classList.remove('active');
    }
  }

  const items = document.querySelectorAll('.nb-channel-item');
  items.forEach(el => {
    if (el.dataset.channelId === selectedChannelId) {
      el.classList.add('active');
    } else {
      el.classList.remove('active');
    }
  });

  // Cập nhật Dynamic Header ở cột phải
  const titleEl = document.getElementById('monitorViewTitle');
  const iconEl = document.getElementById('monitorViewIcon');
  const backBtn = document.getElementById('btnBackToAllFeed');
  const noteEl = document.getElementById('monitorViewNote');

  if (selectedChannelId === 'all') {
    if (titleEl) titleEl.innerText = 'Bảng cập nhật tổng hợp';
    if (iconEl) iconEl.setAttribute('data-lucide', 'layout-list');
    if (backBtn) backBtn.classList.add('hidden');
    if (noteEl) {
      noteEl.classList.add('hidden');
      noteEl.classList.remove('flex');
    }
  } else {
    const ch = monitorChannels.find(c => c.id === selectedChannelId);
    if (titleEl) titleEl.innerText = ch ? (ch.name || ch.url) : 'Kênh theo dõi';
    if (iconEl) {
      const p = ch?.platform;
      iconEl.setAttribute('data-lucide', p === 'youtube' ? 'youtube' : (p === 'facebook' ? 'facebook' : 'video'));
    }
    if (backBtn) backBtn.classList.remove('hidden');
    if (noteEl) {
      if (ch && ch.note) {
        noteEl.innerHTML = `<i data-lucide="file-text" class="w-3 h-3 text-amber-400 shrink-0"></i><span class="truncate">${escapeAttr(ch.note)}</span>`;
        noteEl.title = `Ghi chú: ${ch.note}`;
        noteEl.classList.remove('hidden');
        noteEl.classList.add('flex');
      } else {
        noteEl.classList.add('hidden');
        noteEl.classList.remove('flex');
      }
    }
  }

  renderMonitorVideos();
  refreshIcons();
}

function setMonitorStatusFilter(filter) {
  if (statusFilter !== filter) selectedMonitorVideoIds.clear();
  statusFilter = filter;
  ['all', 'pending', 'downloaded'].forEach(f => {
    const el = document.getElementById('subFilter' + f.charAt(0).toUpperCase() + f.slice(1));
    if (el) {
      if (f === filter) {
        el.classList.add('active');
      } else {
        el.classList.remove('active');
      }
    }
  });
  renderMonitorVideos();
}

// ─────────────────────────────────────────────────────────────
// Tác vụ Kênh (Check, Toggle, Delete, Interval)
// ─────────────────────────────────────────────────────────────
async function checkChannelNow(channelId, btnElem) {
  if (btnElem) {
    btnElem.disabled = true;
    btnElem.classList.add('animate-spin');
  }
  showToast('Đang kiểm tra kênh...');
  try {
    const res = await fetch(`/api/channels/${channelId}/check`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      if (!data.complete) {
        showToast(`${data.message || 'Kiểm tra chưa đầy đủ'}. Tìm thấy ${data.new_videos_found || 0} video mới.`, true);
      } else if (data.new_videos_found > 0) {
        showToast(`Tìm thấy ${data.new_videos_found} video mới!`);
      } else {
        showToast('Kênh chưa có video mới.');
      }
    } else {
      showToast(data.detail || 'Lỗi khi kiểm tra kênh', true);
    }
  } catch (e) {
    showToast('Lỗi kết nối khi kiểm tra kênh', true);
  } finally {
    await Promise.all([loadMonitorVideos(), loadMonitorChannels()]);
    if (btnElem) {
      btnElem.disabled = false;
      btnElem.classList.remove('animate-spin');
      refreshIcons();
    }
  }
}

async function checkActiveChannelNow() {
  const btn = document.getElementById('btnCheckActiveChannel');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i data-lucide="refresh-cw" class="w-3 h-3 animate-spin"></i><span>Đang quét...</span>';
    refreshIcons();
  }

  try {
    if (selectedChannelId === 'all') {
      showToast('Đang quét video mới trên tất cả kênh theo dõi...');
      const res = await fetch('/api/channels/check-all', { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        const errors = (data.results || []).filter(r => r.status === 'error' || r.status === 'busy').length;
        const partial = (data.results || []).filter(r => r.status === 'partial').length;
        showToast(`Tìm thấy ${data.new_videos_found || 0} video mới. ${errors} kênh lỗi/đang bận, ${partial} kênh chưa đọc đầy đủ.`, errors > 0 || partial > 0);
        await loadMonitorVideos();
        await loadMonitorChannels();
      } else {
        showToast(data.detail || 'Lỗi khi quét các kênh', true);
      }
    } else {
      await checkChannelNow(selectedChannelId, null);
    }
  } catch (e) {
    showToast('Lỗi kết nối máy chủ khi quét video', true);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<i data-lucide="refresh-cw" class="w-3 h-3"></i><span>Quét video</span>';
      refreshIcons();
    }
  }
}

async function toggleChannelActive(channelId, newActive) {
  try {
    const res = await fetch(`/api/channels/${channelId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active: newActive })
    });
    if (res.ok) {
      showToast(newActive ? 'Đã bật theo dõi kênh' : 'Đã tạm dừng theo dõi kênh');
      await loadMonitorChannels();
    }
  } catch (e) {
    showToast('Lỗi khi cập nhật trạng thái', true);
  }
}

async function deleteMonitorChannel(channelId) {
  if (!confirm('Bạn có chắc muốn xóa kênh này khỏi danh sách theo dõi?')) return;
  try {
    const res = await fetch(`/api/channels/${channelId}`, { method: 'DELETE' });
    if (res.ok) {
      showToast('Đã xóa kênh');
      if (selectedChannelId === channelId) {
        selectChannelView('all');
      }
      await loadMonitorChannels();
      await loadMonitorVideos();
    } else {
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || 'Lỗi khi xóa kênh từ máy chủ', true);
    }
  } catch (e) {
    showToast('Lỗi khi xóa kênh', true);
  }
}

function formatInterval(minutes) {
  if (!minutes || minutes < 1) minutes = 15;
  if (minutes >= 60 && minutes % 60 === 0) return `${minutes / 60} giờ`;
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}p`;
  return `${minutes} phút`;
}

async function changeChannelInterval(channelId, currentMinutes) {
  const input = prompt(
    'Tự cài đặt chu kỳ kiểm tra tự động cho kênh này (tính bằng số PHÚT):\nVí dụ: 10, 30, 60, 120, 360 (6 tiếng), 1440 (1 ngày)...',
    currentMinutes || 15
  );
  if (input === null) return;
  const mins = parseInt(input.trim());
  if (isNaN(mins) || mins < 1) {
    showToast('Vui lòng nhập số phút hợp lệ (tối thiểu 1 phút)', true);
    return;
  }
  try {
    const res = await fetch(`/api/channels/${channelId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ check_interval_minutes: mins })
    });
    if (res.ok) {
      showToast(`Đã đổi chu kỳ kiểm tra thành ${formatInterval(mins)}/lần!`);
      await loadMonitorChannels();
    } else {
      showToast('Lỗi khi cập nhật chu kỳ', true);
    }
  } catch (e) {
    showToast('Lỗi kết nối máy chủ', true);
  }
}

// Chỉnh sửa Ghi chú (Note) cho kênh
async function editChannelNote(channelId) {
  const ch = monitorChannels.find(c => c.id === channelId);
  const currentNote = ch ? (ch.note || '') : '';
  const input = prompt(
    'Nhập ghi chú cho kênh / page này (ví dụ: Thể loại phim, giờ lên video, lưu ý...):',
    currentNote
  );
  if (input === null) return; // Bấm Cancel
  const newNote = input.trim();

  try {
    const res = await fetch(`/api/channels/${channelId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note: newNote })
    });
    if (res.ok) {
      showToast(newNote ? 'Đã lưu ghi chú kênh!' : 'Đã xóa ghi chú kênh!');
      if (ch) ch.note = newNote;
      renderMonitorChannels();
      if (selectedChannelId === channelId) {
        selectChannelView(channelId);
      }
    } else {
      showToast('Lỗi khi cập nhật ghi chú', true);
    }
  } catch (e) {
    showToast('Lỗi kết nối máy chủ', true);
  }
}

function setIntervalPreset(val, unit) {
  const valElem = document.getElementById('addChannelIntervalVal');
  const unitElem = document.getElementById('addChannelIntervalUnit');
  if (valElem) valElem.value = val;
  if (unitElem) unitElem.value = unit;
}

// ─────────────────────────────────────────────────────────────
// Modal Thêm Kênh
// ─────────────────────────────────────────────────────────────
async function openAddChannelModal() {
  const modal = document.getElementById('addChannelModal');
  if (!modal) return;
  document.getElementById('addChannelUrl').value = '';
  document.getElementById('addChannelName').value = '';
  const noteInput = document.getElementById('addChannelNote');
  if (noteInput) noteInput.value = '';
  const valElem = document.getElementById('addChannelIntervalVal');
  const unitElem = document.getElementById('addChannelIntervalUnit');
  if (valElem) valElem.value = '15';
  if (unitElem) unitElem.value = 'm';

  await loadCollectionsList();
  const sel = document.getElementById('addChannelCollection');
  if (sel) {
    sel.innerHTML = availableCollections.map(c => `<option value="${c}">${c}</option>`).join('');
  }

  modal.classList.remove('hidden');
  refreshIcons();
}

function closeAddChannelModal() {
  const modal = document.getElementById('addChannelModal');
  if (modal) modal.classList.add('hidden');
}

function onChannelUrlInput() {
  const url = document.getElementById('addChannelUrl').value.trim().toLowerCase();
  const badge = document.getElementById('detectedPlatformBadge');
  if (!badge) return;

  if (url.includes('youtube.com') || url.includes('youtu.be')) {
    badge.innerHTML = '<span class="text-red-400 bg-red-500/10 border border-red-500/20 px-2 py-0.5 rounded text-[11px] font-medium">Phát hiện: YouTube</span>';
  } else if (url.includes('douyin.com')) {
    badge.innerHTML = '<span class="text-cyan-400 bg-cyan-500/10 border border-cyan-500/20 px-2 py-0.5 rounded text-[11px] font-medium">Douyin · Phiên khách, không cần tài khoản · Chu kỳ tối thiểu 5 phút</span>';
  } else if (url.includes('tiktok.com')) {
    badge.innerHTML = '<span class="text-amber-300 text-[11px]">Theo dõi TikTok chưa được hỗ trợ. Hãy dùng link hồ sơ Douyin.</span>';
  } else if (url.includes('facebook.com') || url.includes('fb.com')) {
    badge.innerHTML = '<span class="text-blue-400 bg-blue-500/10 border border-blue-500/20 px-2 py-0.5 rounded text-[11px] font-medium">Phát hiện: Facebook Page</span>';
  } else {
    badge.innerHTML = '';
  }
}

async function submitAddChannel() {
  const url = document.getElementById('addChannelUrl').value.trim();
  const name = document.getElementById('addChannelName').value.trim();
  const note = document.getElementById('addChannelNote')?.value.trim() || '';
  const collection = document.getElementById('addChannelCollection')?.value || 'Chung';
  
  const intervalVal = parseInt(document.getElementById('addChannelIntervalVal')?.value) || 15;
  const intervalUnit = document.getElementById('addChannelIntervalUnit')?.value || 'm';
  const interval = intervalUnit === 'h' ? intervalVal * 60 : intervalVal;

  if (!url) {
    showToast('Vui lòng nhập URL kênh', true);
    return;
  }

  let platform = 'youtube';
  const urlLower = url.toLowerCase();
  if (urlLower.includes('tiktok.com')) {
    showToast('Theo dõi TikTok chưa được hỗ trợ; cần link hồ sơ Douyin.', true);
    return;
  }
  if (urlLower.includes('douyin.com')) {
    platform = 'douyin';
  } else if (urlLower.includes('facebook.com') || urlLower.includes('fb.com')) {
    platform = 'facebook';
  }

  const submitBtn = document.getElementById('btnSubmitAddChannel');
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerText = 'Đang nhận diện...';
  }

  try {
    const res = await fetch('/api/channels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        platform,
        url,
        name: name || null,
        note: note || '',
        collection: collection || 'Chung',
        check_interval_minutes: interval
      })
    });
    const data = await res.json();
    if (res.ok) {
      showToast('Thêm kênh theo dõi thành công!');
      closeAddChannelModal();
      await loadMonitorChannels();
      if (data.channel?.id) {
        checkChannelNow(data.channel.id);
      }
    } else {
      showToast(data.detail || 'Lỗi khi thêm kênh', true);
    }
  } catch (e) {
    showToast('Lỗi kết nối máy chủ', true);
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.innerText = 'Thêm kênh';
    }
  }
}

// ─────────────────────────────────────────────────────────────
// Video theo dõi & Detail View Render
// ─────────────────────────────────────────────────────────────
async function loadMonitorVideos() {
  const sequence = ++monitorVideoLoadSequence;
  try {
    const res = await fetch('/api/channels/videos');
    if (!res.ok) return;
    const data = await res.json();
    if (sequence !== monitorVideoLoadSequence) return;
    monitorVideos = (data.videos || []).filter(v => v.status !== 'dismissed')
      .map(v => ({ ...v, uuid: v.uuid || v.video_id }));
    pendingVideos = monitorVideos.filter(v => (v.status === 'pending' || !v.status));
    updateMonitorBadges();
    renderMonitorVideos();
    checkMonitorDownloadTasks();
  } catch (e) {
    console.error('Lỗi nạp monitor videos:', e);
  }
}

// Giữ lại loadPendingVideos cho tính tương thích ngược
async function loadPendingVideos() {
  return loadMonitorVideos();
}

function updateMonitorBadges() {
  const pendingCount = monitorVideos.filter(v => (v.status === 'pending' || !v.status)).length;

  const topBadge = document.getElementById('monitorPendingBadge');
  if (topBadge) {
    if (pendingCount > 0) {
      topBadge.innerText = pendingCount;
      topBadge.classList.remove('hidden');
    } else {
      topBadge.classList.add('hidden');
    }
  }

  const allFeedBadge = document.getElementById('allChannelsFeedBadge');
  if (allFeedBadge) {
    allFeedBadge.innerText = pendingCount;
  }
}

function getVisibleMonitorVideos() {
  // Lọc theo kênh (selectedChannelId)
  let filtered = monitorVideos.filter(v => v.status !== 'dismissed');
  if (selectedChannelId !== 'all') {
    filtered = filtered.filter(v => v.channel_uuid === selectedChannelId || v.channel_id === selectedChannelId);
  }

  // Lọc theo trạng thái (statusFilter)
  if (statusFilter === 'pending') {
    filtered = filtered.filter(v => v.status === 'pending' || !v.status);
  } else if (statusFilter === 'downloaded') {
    filtered = filtered.filter(v => v.status === 'downloaded');
  }

  return filtered;
}

function updateMonitorDeleteControls() {
  const visible = getVisibleMonitorVideos();
  const selectable = visible.filter(v => v.status !== 'downloading');
  const allowed = new Set(selectable.map(v => v.uuid));
  selectedMonitorVideoIds = new Set([...selectedMonitorVideoIds].filter(id => allowed.has(id)));
  const count = selectedMonitorVideoIds.size;
  const selectAll = document.getElementById('monitorSelectAll');
  if (selectAll) {
    selectAll.checked = selectable.length > 0 && count === selectable.length;
    selectAll.indeterminate = count > 0 && count < selectable.length;
    selectAll.disabled = monitorListDeleteBusy || !selectable.length;
  }
  const label = document.getElementById('monitorSelectionCount');
  if (label) label.textContent = `Đã chọn ${count} / ${selectable.length}`;
  for (const [id, enabled] of [
    ['btnDeleteSelectedMonitor', count > 0],
    ['btnDeleteDownloadedMonitor', selectable.some(v => v.status === 'downloaded')],
    ['btnClearMonitorView', selectable.length > 0]
  ]) {
    const button = document.getElementById(id);
    if (button) button.disabled = monitorListDeleteBusy || !enabled;
  }
}

function toggleMonitorVideoSelection(uuid, checked) {
  if (monitorListDeleteBusy) return;
  if (checked) selectedMonitorVideoIds.add(uuid);
  else selectedMonitorVideoIds.delete(uuid);
  document.getElementById(`videoCard-${uuid}`)?.classList.toggle('nb-feed-selected', checked);
  updateMonitorDeleteControls();
}

function selectAllMonitorVideos(checked) {
  if (monitorListDeleteBusy) return;
  selectedMonitorVideoIds = new Set(checked ? getVisibleMonitorVideos()
    .filter(v => v.status !== 'downloading').map(v => v.uuid) : []);
  renderMonitorVideos();
}

async function deleteMonitorVideo(uuid) {
  return deleteMonitorList('single', uuid);
}

function monitorDeleteError(response, data) {
  if (response.status === 404 || response.status === 405) {
    return new Error('Máy chủ đang chạy bản cũ. Nhấn Ctrl+C trong cửa sổ chạy tool, mở lại run.bat rồi tải lại trang.');
  }
  return new Error(data.detail || 'Không thể xóa danh sách. Hãy thử lại.');
}

async function deleteMonitorRows(ids, onResult) {
  if (!monitorUseLegacyDelete) {
    const response = await fetch('/api/channels/videos/delete-batch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_uuids: ids })
    });
    const data = await response.json().catch(() => ({}));
    if (response.ok) {
      onResult(data);
      return;
    }
    // A running older server can serve the new JS from disk but lacks this route.
    // Only fall back when the endpoint is absent; never retry a failed batch write.
    if (response.status !== 404 && response.status !== 405) throw monitorDeleteError(response, data);
    monitorUseLegacyDelete = true;
  }
  const tasksResponse = await fetch('/api/tasks');
  if (!tasksResponse.ok) throw new Error('Không kiểm tra được tác vụ đang tải. Hãy tải lại trang trước khi xóa.');
  const tasks = await tasksResponse.json();
  for (const id of ids) {
    const video = monitorVideos.find(v => v.uuid === id);
    if (video?.status === 'downloading' || ['downloading', 'pending', 'queued'].includes(tasks[video?.video_id]?.status)) {
      onResult({ removed_count: 0, skipped_ids: [id] });
      continue;
    }
    const response = await fetch(`/api/channels/pending/${encodeURIComponent(id)}`, { method: 'DELETE' });
    const data = await response.json().catch(() => ({}));
    if (response.ok && data.dismissed === true) {
      onResult({ removed_count: 1, removed_ids: [id] });
    } else if (response.status === 409) {
      onResult({ removed_count: 0, skipped_ids: [id] });
    } else if (response.status === 404) {
      // Distinguish a missing row from an older server missing even the legacy route.
      const current = await fetch('/api/channels/videos');
      if (!current.ok) throw monitorDeleteError(response, data);
      const rows = await current.json();
      if (!Array.isArray(rows.videos) || rows.videos.some(v => (v.uuid || v.video_id) === id && v.status !== 'dismissed')) {
        throw monitorDeleteError(response, data);
      }
      onResult({ removed_count: 0, missing_ids: [id] });
    } else {
      throw monitorDeleteError(response, data);
    }
  }
}

async function deleteMonitorList(mode, uuid = null) {
  if (monitorListDeleteBusy) return;
  let targets = getVisibleMonitorVideos().filter(v => v.status !== 'downloading');
  if (mode === 'single') targets = targets.filter(v => v.uuid === uuid);
  else if (mode === 'selected') targets = targets.filter(v => selectedMonitorVideoIds.has(v.uuid));
  else if (mode === 'downloaded') targets = targets.filter(v => v.status === 'downloaded');
  else if (mode !== 'filtered') return;
  if (!targets.length) return;
  const channel = monitorChannels.find(ch => ch.id === selectedChannelId);
  const scope = selectedChannelId === 'all' ? 'tất cả kênh' : (channel?.name || 'kênh đang xem');
  const filterLabel = mode === 'downloaded' ? 'Đã tải' :
    ({ all: 'Tất cả', pending: 'Chưa tải', downloaded: 'Đã tải' }[statusFilter] || 'Tất cả');
  if (!window.confirm(`Xóa ${targets.length} mục khỏi bảng cập nhật?\nPhạm vi: ${scope} · ${filterLabel}.\n\nVideo đã tải vẫn được giữ trong thư viện. Các mục đã xóa không tự xuất hiện lại khi quét. Mục đang tải được giữ lại.`)) return;
  const ids = targets.map(v => v.uuid);
  monitorListDeleteBusy = true;
  ++monitorVideoLoadSequence; // Ignore pre-deletion responses that are still in flight.
  renderMonitorVideos();
  let removed = 0, skipped = 0;
  try {
    for (let offset = 0; offset < ids.length; offset += 5000) {
      await deleteMonitorRows(ids.slice(offset, offset + 5000), data => {
        removed += data.removed_count || 0;
        skipped += (data.skipped_ids || []).length;
        for (const id of [...(data.removed_ids || []), ...(data.missing_ids || [])]) selectedMonitorVideoIds.delete(id);
      });
    }
    showToast(`Đã xóa ${removed} mục khỏi bảng.${skipped ? ` Giữ lại ${skipped} mục đang tải.` : ''}`, skipped > 0);
  } catch (error) {
    showToast(`${removed ? `Đã xóa ${removed} mục. ` : ''}${error.message || 'Lỗi kết nối khi xóa danh sách.'}`, true);
  } finally {
    await loadMonitorVideos();
    monitorListDeleteBusy = false;
    renderMonitorVideos();
  }
}

function renderMonitorVideos() {
  const container = document.getElementById('pendingVideosList');
  if (!container) return;
  const filtered = getVisibleMonitorVideos();
  updateMonitorDeleteControls();
  // Cập nhật số lượng hiển thị trên Dynamic View Header
  const countBadge = document.getElementById('monitorViewCountBadge');
  if (countBadge) {
    countBadge.innerText = filtered.length;
  }

  // Trường hợp trống video
  if (filtered.length === 0) {
    let emptyMsg = 'Không có video nào trong danh mục này';
    let emptySub = 'Khi kênh đăng tải video mới, hệ thống sẽ tự động quét và hiển thị ở đây.';
    if (statusFilter === 'pending') {
      emptyMsg = 'Không có video mới nào cần duyệt';
      emptySub = 'Tất cả video đã được tải về hoặc bỏ qua.';
    } else if (statusFilter === 'downloaded') {
      emptyMsg = 'Chưa có video nào được tải về từ kênh này';
      emptySub = 'Bấm "Tải về" ở danh sách Chưa tải để lưu video vào máy tính.';
    }

    container.innerHTML = `
      <div class="py-16 text-center text-slate-500 border border-slate-800/80 rounded-2xl bg-slate-950/40">
        <i data-lucide="check-circle-2" class="w-12 h-12 mx-auto mb-3 text-emerald-500/50"></i>
        <p class="text-sm font-medium text-slate-300">${emptyMsg}</p>
        <p class="text-xs text-slate-500 mt-1">${emptySub}</p>
      </div>
    `;
    refreshIcons();
    return;
  }

  container.innerHTML = filtered.map(v => {
    let platformBadge = '';
    if (v.platform === 'youtube') {
      platformBadge = '<span class="nb-platform nb-badge-yt">YouTube</span>';
    } else if (v.platform === 'douyin') {
      platformBadge = '<span class="nb-platform nb-badge-dy">Douyin</span>';
    } else if (v.platform === 'facebook') {
      platformBadge = '<span class="nb-platform nb-badge-fb">Facebook</span>';
    }

    // 3 Tag nhận diện trạng thái video với chấm đèn nhịp thở
    let statusTag = '';
    const st = v.status || 'pending';
    if (st === 'pending') {
      statusTag = '<span class="nb-tag nb-tag-new"><span class="nb-pulse-dot red"></span> Mới</span>';
    } else if (st === 'downloading') {
      const pct = v.download_percent ? ` ${v.download_percent}%` : '';
      statusTag = `<span class="nb-tag nb-tag-downloading"><i data-lucide="loader-2" class="w-3 h-3 animate-spin text-amber-400"></i> Đang tải<span id="pct-${v.uuid}">${pct}</span></span>`;
    } else if (st === 'downloaded') {
      statusTag = '<span class="nb-tag nb-tag-downloaded"><span class="nb-pulse-dot green"></span> Đã tải về</span>';
    }

    const pubDate = v.published
      ? new Date(v.published).toLocaleString([], { hour: '2-digit', minute: '2-digit', month: 'numeric', day: 'numeric', year: 'numeric' })
      : '';

    const thumbHtml = v.thumbnail
      ? `<img src="${v.thumbnail}" class="nb-thumb-box" onerror="this.src='/static/video-placeholder.svg'" />`
      : `<div class="nb-thumb-box bg-slate-900 flex items-center justify-center"><i data-lucide="video" class="w-6 h-6 text-slate-600"></i></div>`;

    // Dropdown chọn thư mục lưu (Collection)
    const currentCol = v.collection || 'Chung';
    const colOptions = availableCollections.map(c => `
      <option value="${c}" ${c === currentCol ? 'selected' : ''}>${c}</option>
    `).join('');

    // Nút hành động tương ứng với trạng thái
    let actionButtons = '';
    if (st === 'pending') {
      actionButtons = `
        <button onclick="approveDownloadPending('${v.uuid}')" class="nb-button primary">
          <i data-lucide="download" class="w-3.5 h-3.5"></i>
          <span>Tải về</span>
        </button>
        <button onclick="approveDownloadAndPostFb('${v.uuid}')" class="nb-button" style="border-color:#2a4569;background:#18283d;color:#93c5fd;" title="Tải về máy và tự động đăng lên Fanpage Facebook">
          <i data-lucide="share-2" class="w-3.5 h-3.5"></i>
          <span>Tải + Đăng FB</span>
        </button>
      `;
    } else if (st === 'downloading') {
      actionButtons = `
        <button class="nb-button" disabled style="opacity:0.8;cursor:not-allowed;">
          <i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i>
          <span>Đang xử lý tải...</span>
        </button>
      `;
    } else if (st === 'downloaded') {
      actionButtons = `
        <button onclick="openMonitorVideoStudio('${v.uuid}')" class="nb-button primary" title="Mở Studio phụ đề & lồng tiếng AI cho video này">
          <i data-lucide="sparkles" class="w-3.5 h-3.5 text-amber-300"></i>
          <span>Mở Studio</span>
        </button>
        <button onclick="openMonitorVideoPreview('${v.uuid}')" class="nb-button" title="Xem video đã tải về">
          <i data-lucide="eye" class="w-3.5 h-3.5 text-indigo-400"></i>
          <span>Xem file</span>
        </button>
        <button onclick="openFbPostModal('${v.file_path || ''}', '${escapeAttr(v.title || '')}')" class="nb-button" style="border-color:#2a4569;background:#18283d;color:#93c5fd;" title="Đăng video này lên Facebook Fanpage">
          <i data-lucide="facebook" class="w-3.5 h-3.5"></i>
          <span>Đăng FB</span>
        </button>
      `;
    }

    actionButtons += `
      <button data-monitor-remove="${escapeAttr(v.uuid)}" onclick="deleteMonitorVideo('${v.uuid}')"
              class="nb-button nb-feed-delete" ${st === 'downloading' || monitorListDeleteBusy ? 'disabled' : ''}
              aria-label="Xóa khỏi bảng: ${escapeAttr(v.title || v.video_id)}"
              title="${st === 'downloading' ? 'Đợi tải xong để xóa khỏi bảng' : 'Xóa khỏi bảng, giữ file đã tải'}">
        <i data-lucide="trash-2" class="w-4 h-4"></i><span>Xóa khỏi bảng</span>
      </button>`;

    return `
      <div class="nb-pending-card ${selectedMonitorVideoIds.has(v.uuid) ? 'nb-feed-selected' : ''}" id="videoCard-${v.uuid}">
        <label class="nb-feed-checkbox" title="${st === 'downloading' ? 'Mục đang tải được giữ lại' : 'Chọn mục này để xóa khỏi bảng'}">
          <input type="checkbox" data-monitor-select="${escapeAttr(v.uuid)}" aria-label="Chọn: ${escapeAttr(v.title || v.video_id)}"
                 ${selectedMonitorVideoIds.has(v.uuid) ? 'checked' : ''} ${st === 'downloading' || monitorListDeleteBusy ? 'disabled' : ''}
                 onchange="toggleMonitorVideoSelection('${v.uuid}', this.checked)" />
        </label>
        <div class="flex items-start space-x-3.5 min-w-0 flex-1">
          ${thumbHtml}
          <div class="min-w-0 flex-1 space-y-1.5">
            <div class="flex items-center gap-2 flex-wrap">
              ${statusTag}
              ${platformBadge}
              <span class="text-xs font-semibold text-rose-300 truncate max-w-[180px]">${v.channel_name || 'Kênh'}</span>
              
              <!-- Nơi lưu (Dropdown Collection) -->
              <div class="flex items-center gap-1.5 text-xs text-slate-400">
                <i data-lucide="folder" class="w-3 h-3 text-amber-400 shrink-0"></i>
                <span class="text-[11px]">Nơi lưu:</span>
                <select onchange="changeVideoCollection('${v.uuid}', this.value)" class="nb-col-select" ${st === 'downloading' ? 'disabled' : ''} title="Chọn thư mục lưu video khi tải">
                  ${colOptions}
                </select>
              </div>
            </div>

            <a href="${v.url}" target="_blank" class="block font-medium text-sm text-slate-100 hover:text-rose-400 transition line-clamp-2" title="${v.title}">
              ${v.title || v.url}
            </a>

            <div class="text-[11px] text-slate-500 flex items-center gap-2">
              <i data-lucide="clock" class="w-3 h-3"></i>
              <span>Xuất bản: ${pubDate || 'Mới'}</span>
            </div>
          </div>
        </div>

        <div class="flex items-center gap-2 w-full sm:w-auto shrink-0 justify-end pt-2 sm:pt-0 border-t sm:border-t-0 border-[#222730]">
          ${actionButtons}
        </div>
      </div>
    `;
  }).join('');

  refreshIcons();
}

// ─────────────────────────────────────────────────────────────
// Thao tác trên từng Video
// ─────────────────────────────────────────────────────────────

// Đổi thư mục lưu (Collection) cho 1 video
async function changeVideoCollection(uuid, newCol) {
  const item = monitorVideos.find(v => v.uuid === uuid);
  if (item) item.collection = newCol;

  try {
    const res = await fetch(`/api/channels/videos/${uuid}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ collection: newCol })
    });
    if (res.ok) {
      showToast(`Đã chuyển nơi lưu sang [${newCol}]`);
    } else {
      showToast('Không thể cập nhật thư mục lưu', true);
    }
  } catch (e) {
    showToast('Lỗi kết nối máy chủ', true);
  }
}

// Bấm Tải về
async function approveDownloadPending(uuid) {
  const item = monitorVideos.find(v => v.uuid === uuid);
  if (!item) return;

  // Lập tức đổi trạng thái sang downloading trên giao diện để có feedback ngay
  item.status = 'downloading';
  renderMonitorVideos();

  const targetDir = document.getElementById('folderInput')?.value.trim() || '';
  const videoObj = {
    id: item.video_id,
    url: item.url,
    title: item.title,
    platform: item.platform,
    thumbnail: item.thumbnail,
    author: item.channel_name,
    collection: item.collection || 'Chung'
  };

  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_ids: [item.video_id],
        target_dir: targetDir,
        collection: item.collection || 'Chung',
        videos: [videoObj]
      })
    });
    if (res.ok) {
      showToast(`Đã thêm vào hàng đợi tải [${item.collection || 'Chung'}]!`);
      if (typeof startTaskPolling === 'function') {
        startTaskPolling();
      }
      checkMonitorDownloadTasks();
    } else {
      item.status = 'pending';
      renderMonitorVideos();
      const data = await res.json().catch(() => ({}));
      showToast(data.detail || 'Không thể bắt đầu tải video', true);
    }
  } catch (e) {
    item.status = 'pending';
    renderMonitorVideos();
    showToast('Lỗi kết nối khi bắt đầu tải', true);
  }
}

// Bấm Tải về + Đăng FB
async function approveDownloadAndPostFb(uuid) {
  const item = monitorVideos.find(v => v.uuid === uuid);
  if (!item) return;

  if (!fbConfig.token_set) {
    showToast('Vui lòng cài đặt Page Token Facebook trước trong mục Cài đặt Facebook!', true);
    return;
  }

  await approveDownloadPending(uuid);
  showToast('Video sẽ được tự động tải về thư mục. Sau khi tải xong bạn có thể bấm "Đăng FB"!');
}

// Bỏ qua video
async function dismissPendingVideo(uuid) {
  return deleteMonitorVideo(uuid);
}

// Mở Studio phụ đề & lồng tiếng cho video đã tải
function openMonitorVideoStudio(uuid) {
  const item = monitorVideos.find(v => v.uuid === uuid);
  if (!item) return;

  let rel = item.rel_path;
  if (!rel && item.file_path) {
    const norm = item.file_path.replace(/\\/g, '/');
    if (norm.includes('/downloads/')) {
      rel = norm.split('/downloads/')[1];
    } else {
      rel = norm.split('/').pop();
    }
  }

  if (!rel && item.title) {
    const col = item.collection || 'Chung';
    rel = `${col}/${item.title}.mp4`;
  }

  if (typeof window.openSubtitleStudio === 'function' && rel) {
    window.openSubtitleStudio(rel);
  } else {
    showToast('Mở tab Lịch sử để vào Studio phụ đề của video này');
    if (typeof switchTab === 'function') switchTab('history');
  }
}

// Mở popup xem trước video đã tải
function openMonitorVideoPreview(uuid) {
  const item = monitorVideos.find(v => v.uuid === uuid);
  if (!item) return;

  let rel = item.rel_path;
  if (!rel && item.file_path) {
    const norm = item.file_path.replace(/\\/g, '/');
    if (norm.includes('/downloads/')) {
      rel = norm.split('/downloads/')[1];
    } else {
      rel = norm.split('/').pop();
    }
  }

  if (rel && typeof window.openPreviewModal === 'function') {
    window.openPreviewModal(encodeURIComponent(rel), true);
  } else if (item.url) {
    window.open(item.url, '_blank');
  }
}

// Theo dõi tiến độ tải ngầm của các video đang downloading
function checkMonitorDownloadTasks() {
  const hasDownloading = monitorVideos.some(v => v.status === 'downloading');
  if (!hasDownloading) {
    if (monitorDownloadPollTimer) {
      clearInterval(monitorDownloadPollTimer);
      monitorDownloadPollTimer = null;
    }
    return;
  }

  if (!monitorDownloadPollTimer) {
    monitorDownloadPollTimer = setInterval(async () => {
      try {
        const res = await fetch('/api/tasks');
        if (!res.ok) return;
        const tasks = await res.json();
        let anyCompleted = false;

        for (const v of monitorVideos) {
          if (v.status === 'downloading') {
            const t = tasks[v.video_id];
            if (t) {
              if (t.status === 'completed') {
                v.status = 'downloaded';
                if (t.output_path) v.file_path = t.output_path;
                anyCompleted = true;
              } else if (t.percent !== undefined) {
                v.download_percent = t.percent;
                const pctEl = document.getElementById(`pct-${v.uuid}`);
                if (pctEl) pctEl.innerText = ` ${t.percent}%`;
              }
            }
          }
        }

        if (anyCompleted) {
          await loadMonitorVideos();
          checkMonitorDownloadTasks();
        }
      } catch (e) {}
    }, 2000);
  }
}

// ─────────────────────────────────────────────────────────────
// Cấu hình Facebook Fanpage
// ─────────────────────────────────────────────────────────────
async function loadFbConfig() {
  try {
    const res = await fetch('/api/fb/config');
    if (!res.ok) return;
    fbConfig = await res.json();
    renderFbConfigUI();
  } catch (e) {
    console.error('Lỗi nạp FB config:', e);
  }
}

function renderFbConfigUI() {
  const pageIdInput = document.getElementById('fbPageIdInput');
  const tokenInput = document.getElementById('fbTokenInput');
  const pageNameInput = document.getElementById('fbPageNameInput');
  const statusBadge = document.getElementById('fbStatusBadge');

  if (pageIdInput) pageIdInput.value = fbConfig.page_id || '';
  if (pageNameInput) pageNameInput.value = fbConfig.page_name || '';
  if (tokenInput && fbConfig.token_set) {
    tokenInput.placeholder = `Đã lưu token (${fbConfig.token_hint || '••••••••'})`;
  }

  if (statusBadge) {
    if (fbConfig.token_set && fbConfig.page_id) {
      statusBadge.innerHTML = `<span class="bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-xs px-2 py-0.5 rounded-full flex items-center gap-1 font-medium"><i data-lucide="check-circle" class="w-3 h-3"></i> Đã cấu hình</span>`;
    } else {
      statusBadge.innerHTML = `<span class="bg-amber-500/10 text-amber-400 border border-amber-500/20 text-xs px-2 py-0.5 rounded-full flex items-center gap-1 font-medium"><i data-lucide="alert-circle" class="w-3 h-3"></i> Chưa cài đặt</span>`;
    }
    refreshIcons();
  }
}

async function saveFbConfig(silent = false) {
  const page_id = document.getElementById('fbPageIdInput')?.value.trim();
  const page_name = document.getElementById('fbPageNameInput')?.value.trim();
  const page_access_token = document.getElementById('fbTokenInput')?.value.trim();

  if (!page_id) {
    showToast('Vui lòng nhập Facebook Page ID', true);
    return false;
  }

  try {
    const res = await fetch('/api/fb/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page_id, page_name, page_access_token })
    });
    if (res.ok) {
      if (!silent) showToast('Đã lưu cấu hình Facebook!');
      document.getElementById('fbTokenInput').value = '';
      await loadFbConfig();
      return true;
    } else {
      const data = await res.json();
      showToast(data.detail || 'Lỗi khi lưu cấu hình Facebook', true);
    }
  } catch (e) {
    showToast('Lỗi kết nối máy chủ', true);
  }
  return false;
}

async function testFbConnection() {
  const btn = document.getElementById('btnTestFb');
  if (btn) {
    btn.disabled = true;
    btn.innerText = 'Đang kiểm tra...';
  }
  try {
    if (!await saveFbConfig(true)) return;
    const res = await fetch('/api/fb/test', { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      showToast(`Kết nối thành công! Fanpage: [${data.page_name}] - Lượt thích: ${data.fans || 0}`);
      document.getElementById('fbPageNameInput').value = data.page_name || '';
      await saveFbConfig(true);
    } else {
      showToast(`Lỗi xác thực Facebook: ${data.detail || 'Token không hợp lệ'}`, true);
    }
  } catch (e) {
    showToast('Lỗi kết nối API Facebook', true);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = 'Kiểm tra kết nối';
    }
  }
}

// ─────────────────────────────────────────────────────────────
// Modal Đăng Video lên Facebook (áp dụng cho mọi video đã tải)
// ─────────────────────────────────────────────────────────────
let selectedVideoForFbPost = null;

function openFbPostModal(videoPath, defaultCaption) {
  if (!fbConfig.token_set) {
    showToast('Chưa cấu hình Facebook Token. Hãy vào tab Kênh theo dõi để cấu hình.', true);
    return;
  }
  selectedVideoForFbPost = videoPath;
  const modal = document.getElementById('fbPostModal');
  const pathInput = document.getElementById('fbPostVideoPath');
  const captionInput = document.getElementById('fbPostCaption');

  if (pathInput) pathInput.value = videoPath || '';
  if (captionInput) captionInput.value = defaultCaption || '';
  if (modal) {
    modal.classList.remove('hidden');
    refreshIcons();
  }
}

function closeFbPostModal() {
  const modal = document.getElementById('fbPostModal');
  if (modal) modal.classList.add('hidden');
}

async function submitFbPost() {
  const mp4_path = document.getElementById('fbPostVideoPath')?.value.trim();
  const caption = document.getElementById('fbPostCaption')?.value.trim();

  if (!mp4_path) {
    showToast('Không tìm thấy đường dẫn video', true);
    return;
  }

  const btn = document.getElementById('btnSubmitFbPost');
  const statusElem = document.getElementById('fbPostStatus');

  if (btn) {
    btn.disabled = true;
    btn.innerText = 'Đang đăng video...';
  }
  if (statusElem) {
    statusElem.classList.remove('hidden');
    statusElem.innerText = 'Đang tải file video lên máy chủ Facebook (có thể mất 1-3 phút tùy kích thước)...';
  }

  try {
    const res = await fetch('/api/fb/post', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mp4_path, caption })
    });
    const data = await res.json();
    if (res.ok && data.success === true && data.video_id) {
      showToast(`Đăng video lên Facebook Fanpage thành công! (ID: ${data.video_id})`);
      closeFbPostModal();
    } else {
      showToast(data.detail || 'Lỗi khi đăng video lên Facebook', true);
      if (statusElem) statusElem.innerText = data.detail || 'Facebook chưa xác nhận đăng thành công. Kiểm tra Page trước khi đăng lại.';
    }
  } catch (e) {
    showToast('Lỗi khi gửi video lên Facebook', true);
    if (statusElem) statusElem.innerText = 'Lỗi kết nối';
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = 'Đăng lên Fanpage';
    }
  }
}

// Helper escape chuỗi cho attribute
function escapeAttr(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/'/g, '&#39;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// Tự động khởi tạo Monitor khi DOM sẵn sàng
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initMonitor();
  });
} else {
  initMonitor();
}
