// /downloads — first-class URL/playlist downloader page.
//
// Dedicated page for queueing yt-dlp / gallery-dl downloads from the browser:
// multi-URL paste, recent-folder shortcuts + folder-tree picker, downloader
// availability status, and a live jobs list (active/queued pinned, paginated
// finished history) with per-job cancel & retry and clear-history.
//
// Polls the lightweight worker status endpoint (active/queued/tools) every
// second and pages through the dedicated history endpoint for finished jobs.

import '../../../static/styles.css';
import { escapeHtml, showToast } from '../library/utils.js';
import {
  urlDownloadRowHtml,
  urlDownloadsActivityCount,
  urlDownloadsSummaryText,
} from '../library/url_downloads.js';
import { readRecentTargets, rememberRecentTarget } from '../library/recent_targets.js';

const HISTORY_PAGE_SIZE = 50;
const URL_RE = /^https?:\/\//i;

const els = {};

function byId(id) {
  return document.getElementById(id);
}

function cacheEls() {
  els.targetLabel = byId('dlTargetLabel');
  els.zipWarning = byId('dlZipWarning');
  els.targetClearBtn = byId('dlTargetClearBtn');
  els.recentChips = byId('dlRecentChips');
  els.browseBtn = byId('dlBrowseBtn');
  els.urlsInput = byId('dlUrlsInput');
  els.warningText = byId('dlWarningText');
  els.startBtn = byId('dlStartBtn');
  els.startBtnText = byId('dlStartBtnText');
  els.submitResult = byId('dlSubmitResult');
  els.toolsStatus = byId('dlToolsStatus');
  els.summary = byId('dlSummary');
  els.list = byId('dlList');
  els.clearBtn = byId('dlClearBtn');
  els.loadMoreBtn = byId('dlLoadMore');
  els.navBadge = byId('dlNavBadge');
  els.pickerModal = byId('dlPickerModal');
  els.pickerPath = byId('dlPickerPath');
  els.pickerUpBtn = byId('dlPickerUpBtn');
  els.pickerList = byId('dlPickerList');
  els.pickerSelectBtn = byId('confirmDlPickerBtn');
  els.closePickerBtn = byId('closeDlPickerBtn');
  els.cancelPickerBtn = byId('cancelDlPickerBtn');
}

const state = {
  target: '/',
  status: null,        // latest /api/backup/upload/url/status payload
  historyRows: [],     // newest-first finished jobs from the history endpoint
  historyTotal: 0,
  historyLoaded: false,
  recent: [],
  pickerPath: '',
  pickerIsRoot: true,
  submitting: false,
};

// ---------------------------------------------------------------- target

function renderTarget() {
  if (els.targetLabel) els.targetLabel.textContent = state.target || '/';
  if (els.targetClearBtn) els.targetClearBtn.classList.toggle('hidden', !state.target || state.target === '/');
  const isZip = /\.zip$/i.test(state.target || '');
  if (els.zipWarning) els.zipWarning.classList.toggle('hidden', !isZip);
  if (isZip && els.warningText) {
    els.warningText.textContent = 'ZIP archives can’t receive downloads — pick a real folder instead.';
    els.warningText.classList.remove('hidden');
  }
}

function renderRecentChips() {
  if (!els.recentChips) return;
  if (!state.recent.length) {
    els.recentChips.classList.add('hidden');
    els.recentChips.innerHTML = '';
    return;
  }
  els.recentChips.classList.remove('hidden');
  els.recentChips.innerHTML = state.recent.map(path => `
    <button type="button" data-recent="${escapeHtml(path)}" class="max-w-[180px] px-2.5 py-1 rounded-lg bg-slate-800/80 hover:bg-slate-700 border border-slate-700/70 text-[11px] font-mono text-slate-300 hover:text-white transition flex items-center gap-1.5 min-w-0" title="${escapeHtml(path)}">
      <span class="text-emerald-400 shrink-0">📁</span>
      <span class="truncate">${escapeHtml(path === '/' ? '/' : path.split('/').filter(Boolean).pop())}</span>
    </button>
  `).join('');
  els.recentChips.querySelectorAll('[data-recent]').forEach(btn => {
    btn.addEventListener('click', () => {
      state.target = btn.dataset.recent;
      renderTarget();
      showToast('Target folder updated', 'info');
    });
  });
}

function setTarget(path) {
  state.target = path || '/';
  renderTarget();
}

// ------------------------------------------------------------ availability

function renderToolsStatus(tools) {
  if (!els.toolsStatus || !tools) return;
  const ytdlp = Boolean(tools.ytdlp);
  const gallery = Boolean(tools.gallery_dl);
  const dot = els.toolsStatus.querySelector('span');
  if (ytdlp && gallery) {
    if (dot) dot.className = 'w-2 h-2 rounded-full bg-emerald-500 shrink-0';
    els.toolsStatus.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-500 shrink-0"></span><span>✓ yt-dlp available · ✓ gallery-dl available</span>';
  } else if (ytdlp || gallery) {
    if (dot) dot.className = 'w-2 h-2 rounded-full bg-amber-400 shrink-0';
    const present = ytdlp ? 'yt-dlp' : 'gallery-dl';
    const missing = ytdlp ? 'gallery-dl' : 'yt-dlp';
    els.toolsStatus.innerHTML = `<span class="w-2 h-2 rounded-full bg-amber-400 shrink-0"></span><span>${present} available — ${missing} is not installed (limited coverage)</span>`;
  } else {
    if (dot) dot.className = 'w-2 h-2 rounded-full bg-red-500 shrink-0';
    els.toolsStatus.innerHTML = '<span class="w-2 h-2 rounded-full bg-red-500 shrink-0"></span><span>Neither yt-dlp nor gallery-dl is installed — install via requirements.txt</span>';
  }
}

// ----------------------------------------------------------------- jobs

function renderJobs() {
  const data = state.status;
  const active = (data && data.active) || [];
  const queued = (data && data.queued) || [];

  // History rows are stale once a job is retried back into the live queues;
  // drop any that are active/queued right now.
  const liveIds = new Set([...active, ...queued].map(j => j.id).filter(Boolean));
  const historyRows = state.historyRows.filter(r => !liveIds.has(r.id));

  if (els.summary) els.summary.textContent = urlDownloadsSummaryText(data);
  if (els.navBadge) {
    const count = urlDownloadsActivityCount(data);
    els.navBadge.textContent = count > 99 ? '99+' : String(count);
    els.navBadge.classList.toggle('hidden', count === 0);
  }

  const liveHtml = [...active, ...queued]
    .map(j => urlDownloadRowHtml(j, { retryable: true }))
    .join('');
  const historyHtml = historyRows
    .map(j => urlDownloadRowHtml(j, { retryable: true }))
    .join('');

  if (!liveHtml && !historyHtml) {
    els.list.innerHTML = `<div class="p-6 text-center text-[11px] text-slate-500 bg-slate-950/60 border border-slate-800 rounded-xl">No downloads yet — paste a URL on the left to start.</div>`;
  } else {
    const sections = [];
    if (liveHtml) {
      if (active.length || queued.length) {
        const head = active.length ? `Active & queued` : '';
        sections.push(`<div class="text-[10px] uppercase font-bold text-slate-500">${head}</div>${liveHtml}`);
      }
    }
    if (historyHtml) sections.push(`<div class="text-[10px] uppercase font-bold text-slate-500 pt-1">History (${state.historyTotal})</div>${historyHtml}`);
    els.list.innerHTML = sections.join('');
  }
}

function renderLoadMore() {
  if (!els.loadMoreBtn) return;
  const finished = state.historyRows.length;
  const more = state.historyLoaded && finished < state.historyTotal;
  els.loadMoreBtn.classList.toggle('hidden', !more);
}

async function fetchHistory(reset = false) {
  if (reset) {
    state.historyRows = [];
    state.historyTotal = 0;
    state.historyLoaded = false;
  }
  const offset = state.historyRows.length;
  try {
    const res = await fetch(`/api/backup/upload/url/history?limit=${HISTORY_PAGE_SIZE}&offset=${offset}`);
    const j = await res.json();
    if (res.ok) {
      const incoming = (j.history || []).filter(r => !state.historyRows.some(ex => ex.id === r.id));
      state.historyRows = state.historyRows.concat(incoming);
      state.historyTotal = j.total || 0;
      state.historyLoaded = true;
      renderJobs();
      renderLoadMore();
    }
  } catch (err) {
    /* transient — the next poll/load retries */
  }
}

async function pollStatus() {
  try {
    const res = await fetch('/api/backup/upload/url/status');
    const j = await res.json();
    if (res.ok) {
      state.status = j;
      renderToolsStatus(j.tools);
      renderJobs();
      renderLoadMore();
    }
  } catch (err) {
    /* transient network hiccup; keep the last known state */
  }
}

// ---------------------------------------------------------------- actions

function splitUrls(raw) {
  return raw
    .split(/\s+/)
    .map(s => s.trim())
    .filter(Boolean)
    .filter((url, i, arr) => arr.indexOf(url) === i);
}

async function enqueueUrl(url) {
  const res = await fetch('/api/backup/upload/url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, destination_dir: state.target }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(j.detail || res.status);
  return j;
}

async function submitDownloads() {
  if (state.submitting) return;
  const raw = els.urlsInput ? els.urlsInput.value : '';
  const urls = splitUrls(raw);
  if (!urls.length) {
    showToast('Enter at least one video or playlist URL', 'error');
    return;
  }
  if (/\.zip$/i.test(state.target || '')) {
    showToast('Pick a real folder first — ZIP archives can’t receive downloads', 'error');
    return;
  }

  const valid = [];
  const invalid = [];
  for (const url of urls) {
    if (URL_RE.test(url) && url.length <= 2048) valid.push(url);
    else invalid.push(url);
  }
  if (!valid.length) {
    showToast('None of those look like valid http(s) URLs', 'error');
    return;
  }

  state.submitting = true;
  if (els.startBtn) {
    els.startBtn.disabled = true;
    els.startBtnText.textContent = valid.length > 1 ? `Queueing ${valid.length} downloads…` : 'Queueing…';
  }
  if (els.submitResult) {
    els.submitResult.classList.remove('hidden');
    els.submitResult.textContent = `Queueing ${valid.length} download${valid.length > 1 ? 's' : ''} into ${state.target}…`;
  }

  let queued = 0;
  let failed = 0;
  const failedUrls = [];
  let firstError = '';
  try {
    for (const url of valid) {
      try {
        await enqueueUrl(url);
        queued += 1;
      } catch (err) {
        failed += 1;
        failedUrls.push(url);
        firstError = firstError || err.message;
      }
    }

    if (queued > 0) {
      rememberRecentTarget(state.target);
      state.recent = readRecentTargets();
      renderRecentChips();
    }
    if (els.urlsInput) {
      // Keep lines that weren't queued so the user can fix and re-submit them
      // without re-pasting. Successful lines are dropped.
      const remaining = [...invalid, ...failedUrls];
      els.urlsInput.value = remaining.join('\n');
    }

    const parts = [];
    if (queued) parts.push(`Queued ${queued} download${queued > 1 ? 's' : ''}`);
    if (failed) parts.push(`${failed} failed (${firstError})`);
    if (invalid.length) parts.push(`${invalid.length} invalid URL${invalid.length > 1 ? 's' : ''} skipped`);
    const msg = parts.join(' · ') || 'Nothing was queued';
    if (failed > 0 || invalid.length) {
      if (els.submitResult) els.submitResult.textContent = msg;
      showToast(parts[0] || 'Nothing was queued', failed > 0 ? 'error' : 'info');
    } else {
      if (els.submitResult) els.submitResult.classList.add('hidden');
      showToast(parts[0], 'success');
    }
    pollStatus();
  } catch (err) {
    showToast('Failed to queue download: ' + (err.message || 'Error'), 'error');
  } finally {
    state.submitting = false;
    if (els.startBtn) {
      els.startBtn.disabled = false;
      els.startBtnText.textContent = 'Start Download';
    }
  }
}

async function cancelJob(jobId) {
  try {
    const res = await fetch('/api/backup/upload/url/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: jobId }),
    });
    if (res.ok) {
      showToast('Download cancelled', 'info');
      pollStatus();
    } else {
      const j = await res.json().catch(() => ({}));
      showToast('Cancel failed: ' + (j.detail || res.status), 'error');
    }
  } catch (err) {
    showToast('Cancel failed: ' + (err.message || 'Error'), 'error');
  }
}

async function retryJob(jobId) {
  try {
    const res = await fetch('/api/backup/upload/url/retry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: jobId }),
    });
    const j = await res.json().catch(() => ({}));
    if (res.ok) {
      showToast('Download retried — back in the queue', 'success');
      pollStatus();
      fetchHistory();
    } else {
      showToast('Retry failed: ' + (j.detail || res.status), 'error');
    }
  } catch (err) {
    showToast('Retry failed: ' + (err.message || 'Error'), 'error');
  }
}

async function clearHistory() {
  if (!confirm('Remove all finished downloads from history? Active and queued downloads are unaffected.')) return;
  try {
    const res = await fetch('/api/backup/upload/url/clear-history', { method: 'POST' });
    if (res.ok) {
      showToast('Download history cleared', 'success');
      state.historyRows = [];
      state.historyTotal = 0;
      state.historyLoaded = true;
      renderJobs();
      renderLoadMore();
    } else {
      const j = await res.json().catch(() => ({}));
      showToast('Clear failed: ' + (j.detail || res.status), 'error');
    }
  } catch (err) {
    showToast('Clear failed: ' + (err.message || 'Error'), 'error');
  }
}

// ---------------------------------------------------------------- picker

async function loadPickerFolders(path) {
  try {
    const res = await fetch(`/api/backup/folders?path=${encodeURIComponent(path || '')}`);
    const data = await res.json();
    state.pickerPath = data.current_path || path || '/';
    state.pickerIsRoot = Boolean(data.is_root);
    state.pickerChildren = data.subfolders || [];
    renderPicker();
  } catch (err) {
    showToast('Error loading folders', 'error');
  }
}

function renderPicker() {
  els.pickerPath.textContent = state.pickerPath;
  els.pickerPath.title = state.pickerPath;
  els.pickerUpBtn.disabled = state.pickerIsRoot;
  els.pickerUpBtn.className = state.pickerIsRoot
    ? 'px-2 py-1 rounded-lg bg-slate-900 text-slate-600 text-xs shrink-0 transition cursor-not-allowed'
    : 'px-2 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs shrink-0 transition';

  els.pickerList.innerHTML = '<div class="text-xs text-slate-500 px-2 py-3 text-center">Loading…</div>';
  // Children were fetched in loadPickerFolders via state.pickerChildren.
  const dirs = (state.pickerChildren || []).filter(s => !(s.is_zip || /\.zip$/i.test(s.path || '')));
  if (!dirs.length) {
    els.pickerList.innerHTML = `<div class="text-xs text-slate-500 px-2 py-3 text-center">No subfolders — choose this location</div>`;
    return;
  }
  els.pickerList.innerHTML = dirs.map(s => `
    <button type="button" data-picker-dir="${escapeHtml(s.path)}" class="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs text-slate-200 hover:bg-slate-800 text-left transition min-w-0">
      <span class="text-emerald-400 shrink-0">📁</span>
      <span class="truncate flex-1">${escapeHtml(s.name)}</span>
      <span class="text-slate-500 shrink-0">›</span>
    </button>
  `).join('');
  els.pickerList.querySelectorAll('[data-picker-dir]').forEach(btn => {
    btn.addEventListener('click', () => loadPickerFolders(btn.dataset.pickerDir));
  });
}

function openPicker() {
  if (els.pickerModal) {
    els.pickerModal.classList.remove('hidden');
    els.pickerModal.classList.add('flex');
  }
  const start = state.target && state.target !== '/' ? state.target : '';
  loadPickerFolders(start);
}

function closePicker() {
  if (els.pickerModal) {
    els.pickerModal.classList.add('hidden');
    els.pickerModal.classList.remove('flex');
  }
}

// ---------------------------------------------------------------- theme

// Mirrors the library/sorter theme customizer (initThemeCustomizer) using the
// global THEME_MANAGER bootstrapped in theme_head.html.
function initThemeCustomizer() {
  const themeModal = document.getElementById('themeCustomizerModal');
  const themeModalBtn = document.getElementById('themeModalBtn');
  const closeBtn = document.getElementById('closeThemeModalBtn');
  const applyBtn = document.getElementById('applyThemeModalCloseBtn');
  const resetBtn = document.getElementById('resetThemeDefaultsBtn');
  const accentPicker = document.getElementById('themeAccentPicker');
  const basePicker = document.getElementById('themeBasePicker');
  const seekToggle = document.getElementById('mobileDoubleTapSeekToggle');
  if (!themeModal || !window.THEME_MANAGER) return;

  function renderPickers() {
    const cur = window.THEME_MANAGER.getCurrent();
    const accents = window.THEME_MANAGER.getAccents();
    const bases = window.THEME_MANAGER.getBases();

    if (accentPicker) {
      accentPicker.innerHTML = accents.map(acc => {
        const isSel = acc.key === cur.accent;
        return `
          <button type="button" data-accent="${acc.key}" class="theme-accent-btn group flex flex-col items-center gap-1.5 p-2 rounded-xl border transition cursor-pointer select-none ${isSel ? 'border-emerald-500 bg-emerald-500/10 shadow-sm' : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'}">
            <div class="relative w-7 h-7 sm:w-8 sm:h-8 rounded-full shadow-md flex items-center justify-center transition-transform group-hover:scale-110" style="background-color: ${acc.accent};">${isSel ? '<span class="text-white text-xs font-bold drop-shadow">✓</span>' : ''}</div>
            <span class="text-[11px] font-medium ${isSel ? 'text-white font-semibold' : 'text-slate-400 group-hover:text-slate-300'}">${acc.name}</span>
          </button>`;
      }).join('');
      accentPicker.querySelectorAll('.theme-accent-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          window.THEME_MANAGER.setTheme(btn.dataset.accent, window.THEME_MANAGER.getCurrent().base);
          renderPickers();
          showToast(`🎨 Accent changed to ${btn.dataset.accent}`, 'success');
        });
      });
    }

    if (basePicker) {
      basePicker.innerHTML = bases.map(b => {
        const isSel = b.key === cur.base;
        return `
          <button type="button" data-base="${b.key}" class="theme-base-btn text-left p-3 rounded-xl border transition cursor-pointer select-none ${isSel ? 'border-emerald-500 bg-emerald-500/10 shadow-sm' : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'}">
            <div class="flex items-center justify-between mb-1">
              <span class="text-xs font-bold text-white flex items-center gap-1.5"><span>${b.emoji}</span><span>${b.name}</span></span>
              ${isSel ? '<span class="text-emerald-400 text-xs font-bold">✓</span>' : ''}
            </div>
            <p class="text-[11px] text-slate-400 leading-tight">${b.desc}</p>
          </button>`;
      }).join('');
      basePicker.querySelectorAll('.theme-base-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          window.THEME_MANAGER.setTheme(window.THEME_MANAGER.getCurrent().accent, btn.dataset.base);
          renderPickers();
          showToast(`🌌 Theme changed to ${btn.dataset.base}`, 'success');
        });
      });
    }
  }

  function openModal() {
    renderPickers();
    document.body.classList.add('modal-scroll-lock');
    themeModal.classList.remove('hidden');
    themeModal.classList.add('flex');
  }
  function closeModal() {
    document.body.classList.remove('modal-scroll-lock');
    themeModal.classList.add('hidden');
    themeModal.classList.remove('flex');
  }

  if (themeModalBtn) themeModalBtn.addEventListener('click', openModal);
  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  if (applyBtn) applyBtn.addEventListener('click', closeModal);
  themeModal.addEventListener('click', (e) => { if (e.target === themeModal) closeModal(); });
  if (resetBtn) resetBtn.addEventListener('click', () => {
    window.THEME_MANAGER.setTheme('amber', 'stone');
    localStorage.setItem('lib_touch_double_tap_seek', 'true');
    if (seekToggle) seekToggle.checked = true;
    renderPickers();
    showToast('🎨 Theme reset to default', 'info');
  });
  if (seekToggle) {
    seekToggle.checked = localStorage.getItem('lib_touch_double_tap_seek') !== 'false';
    seekToggle.addEventListener('change', (e) => {
      localStorage.setItem('lib_touch_double_tap_seek', e.target.checked ? 'true' : 'false');
      showToast(e.target.checked ? 'Double-tap seek enabled (±5s)' : 'Double-tap seek disabled', 'info');
    });
  }
}

// ------------------------------------------------------------------ init

async function init() {
  cacheEls();

  // Remember the URL param is respected but a bad .zip or non-existent folder
  // is caught on submit; the picker is the reliable way to choose.
  const params = new URLSearchParams(window.location.search);
  const folderParam = params.get('folder');

  state.recent = readRecentTargets();
  state.target = folderParam || state.recent[0] || '/';
  renderTarget();
  renderRecentChips();

  // Keyboard shortcut parity (T opens the theme modal) — optional nicety.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (els.pickerModal && !els.pickerModal.classList.contains('hidden')) closePicker();
      else if (document.getElementById('themeCustomizerModal') && !document.getElementById('themeCustomizerModal').classList.contains('hidden')) {
        document.getElementById('themeCustomizerModal').classList.add('hidden');
        document.body.classList.remove('modal-scroll-lock');
      }
    }
    if (e.key === 't' && !e.ctrlKey && !e.metaKey) {
      const btn = document.getElementById('themeModalBtn');
      if (btn && document.activeElement && document.activeElement.tagName !== 'TEXTAREA') btn.click();
    }
  });

  // Events
  if (els.browseBtn) els.browseBtn.addEventListener('click', openPicker);
  if (els.pickerSelectBtn) els.pickerSelectBtn.addEventListener('click', () => {
    if (state.pickerPath) setTarget(state.pickerPath);
    closePicker();
  });
  if (els.closePickerBtn) els.closePickerBtn.addEventListener('click', closePicker);
  if (els.cancelPickerBtn) els.cancelPickerBtn.addEventListener('click', closePicker);
  if (els.pickerUpBtn) {
    els.pickerUpBtn.addEventListener('click', () => {
      if (state.pickerIsRoot || !state.pickerPath || state.pickerPath === '/') return;
      const parent = state.pickerPath.substring(0, state.pickerPath.lastIndexOf('/')) || '';
      loadPickerFolders(parent);
    });
  }
  if (els.startBtn) els.startBtn.addEventListener('click', submitDownloads);
  if (els.urlsInput) {
    els.urlsInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        submitDownloads();
      }
    });
  }
  if (els.clearBtn) els.clearBtn.addEventListener('click', clearHistory);
  if (els.loadMoreBtn) els.loadMoreBtn.addEventListener('click', () => fetchHistory());
  if (els.targetClearBtn) {
    els.targetClearBtn.addEventListener('click', () => {
      setTarget('/');
      showToast('Target folder reset to library root', 'info');
    });
  }

  // Delegated cancel/retry on the job list.
  if (els.list) {
    els.list.addEventListener('click', (e) => {
      const cancelBtn = e.target.closest('[data-urldl-cancel]');
      const retryBtn = e.target.closest('[data-urldl-retry]');
      if (cancelBtn) cancelJob(cancelBtn.dataset.urldlCancel);
      else if (retryBtn) retryJob(retryBtn.dataset.urldlRetry);
    });
  }

  initThemeCustomizer();

  // First paint then steady polling (browsers throttle background tabs).
  await Promise.allSettled([pollStatus(), fetchHistory(true)]);
  setInterval(pollStatus, 1000);
}

document.addEventListener('DOMContentLoaded', init);