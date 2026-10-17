// /people — face clusters, naming and per-person browsing.
//
// Everything here reads the People API, which is index-only on the server (no
// mount walks): clusters from GET /api/people, one person's faces + media from
// GET /api/people/{id}, and curation through rename / merge / split / dismiss /
// re-cluster. A person's media opens in Fancybox, and a video card's moment
// chips jump straight to the second that person appears.
//
// The scan panel drives the background worker: the strictness presets are
// stored server-side (db_meta) so a choice applies to the very next scan.

import '../../../static/styles.css';
import { escapeHtml, formatDuration, getResolutionLabel, showToast } from '../library/utils.js';
import { hydrateIcons } from '../shared/icons.js';

const PEOPLE_PAGE = 120;
const MEDIA_PAGE = 60;
const FACES_PAGE = 60;
const PRESET_KEY = 'pp_strictness';

const els = {};
const state = {
  people: [],
  counts: null,
  status: null,
  total: 0,
  offset: 0,
  person: null,
  faces: [],
  facesTotal: 0,
  selected: new Set(),
  media: [],
  mediaTotal: 0,
  mediaOffset: 0,
  sort: 'count',
  loading: false,
};

function byId(id) {
  return document.getElementById(id);
}

function cacheEls() {
  els.mobileSubtitle = byId('ppMobileSubtitle');
  els.counts = byId('ppCounts');
  els.scanAllBtn = byId('ppScanAllBtn');
  els.scanFolder = byId('ppScanFolder');
  els.scanFolderBtn = byId('ppScanFolderBtn');
  els.reclusterBtn = byId('ppReclusterBtn');
  els.pauseBtn = byId('ppPauseBtn');
  els.presets = byId('ppPresets');
  els.progress = byId('ppProgress');
  els.progressBar = byId('ppProgressBar');
  els.progressText = byId('ppProgressText');
  els.notice = byId('ppNotice');
  els.loading = byId('ppLoading');
  els.error = byId('ppError');
  els.peopleView = byId('ppPeopleView');
  els.grid = byId('ppGrid');
  els.gridEmpty = byId('ppGridEmpty');
  els.morePeopleBtn = byId('ppMorePeopleBtn');
  els.personView = byId('ppPersonView');
  els.backBtn = byId('ppBackBtn');
  els.personCover = byId('ppPersonCover');
  els.personName = byId('ppPersonName');
  els.saveNameBtn = byId('ppSaveNameBtn');
  els.personMeta = byId('ppPersonMeta');
  els.mergeBtn = byId('ppMergeBtn');
  els.rescanBtn = byId('ppRescanBtn');
  els.dismissPersonBtn = byId('ppDismissPersonBtn');
  els.openInLibraryBtn = byId('ppOpenInLibraryBtn');
  els.faces = byId('ppFaces');
  els.faceSelCount = byId('ppFaceSelCount');
  els.splitBtn = byId('ppSplitBtn');
  els.dismissFacesBtn = byId('ppDismissFacesBtn');
  els.media = byId('ppMedia');
  els.mediaMeta = byId('ppMediaMeta');
  els.mediaSort = byId('ppMediaSort');
  els.moreMediaBtn = byId('ppMoreMediaBtn');
  els.mergeModal = byId('ppMergeModal');
  els.mergeList = byId('ppMergeList');
  els.mergeSearch = byId('ppMergeSearch');
  els.mergeCount = byId('ppMergeCount');
  els.mergeSubtitle = byId('ppMergeSubtitle');
  els.mergeCloseBtn = byId('ppMergeCloseBtn');
  els.mergeCancelBtn = byId('ppMergeCancelBtn');
  els.mergeConfirmBtn = byId('ppMergeConfirmBtn');
}

// ------------------------------------------------------------------- helpers

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch (e) { /* non-JSON error body */ }
    throw new Error(detail);
  }
  return res.json();
}

async function postJson(url, payload) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
  let body = null;
  try {
    body = await res.json();
  } catch (e) { /* empty body */ }
  if (!res.ok) {
    throw new Error((body && body.detail) || `Request failed (${res.status})`);
  }
  return body || {};
}

function fmtInt(n) {
  return Number(n || 0).toLocaleString();
}

function folderLabel(path) {
  const parts = String(path || '').split('/').filter(Boolean);
  return parts.length ? parts[parts.length - 1] : '';
}

function formatTime(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (v) => String(v).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

function chip(label, extra = '') {
  return `<span class="px-2 py-1 rounded-lg bg-slate-900/70 border border-slate-800 text-[10px] font-mono text-slate-300 ${extra}">${label}</span>`;
}

// The page scrolls inside <main>, not the document (the body is a fixed
// viewport-locked column), so a view switch must reset that element — calling
// window.scrollTo() leaves the new view scrolled down under the top bar.
function scrollStageToTop() {
  const stage = document.querySelector('main');
  if (stage) stage.scrollTop = 0;
  else window.scrollTo(0, 0);
}

// -------------------------------------------------------------------- status

function renderCounts() {
  if (!els.counts) return;
  const c = state.counts || {};
  const s = state.status || {};
  els.counts.innerHTML = [
    chip(`${fmtInt(c.people)} people`),
    chip(`${fmtInt(c.named)} named`, 'text-emerald-300 border-emerald-900/40'),
    chip(`${fmtInt(c.faces)} faces`),
    chip(`${fmtInt(c.files)} files`),
    c.dismissed ? chip(`${fmtInt(c.dismissed)} dismissed`) : '',
    s.pending_count ? chip(`${fmtInt(s.pending_count)} to scan`, 'text-amber-300 border-amber-900/40') : '',
  ].join('');
  if (els.mobileSubtitle) {
    els.mobileSubtitle.textContent = `${fmtInt(c.people)} people · ${fmtInt(c.faces)} faces`;
  }
}

function renderPresets() {
  if (!els.presets) return;
  const status = state.status || {};
  const presets = status.threshold_presets || { strict: 0.28, balanced: 0.36, loose: 0.46 };
  const active = status.threshold_preset
    || localStorage.getItem(PRESET_KEY)
    || 'balanced';
  els.presets.innerHTML = Object.keys(presets).map((name) => {
    const on = name === active;
    const label = name.charAt(0).toUpperCase() + name.slice(1);
    return `<button type="button" data-preset="${name}" title="Match strictness ${presets[name]}" class="px-2 py-1 rounded-md text-[10px] font-semibold transition ${on ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-slate-200'}">${label}</button>`;
  }).join('');
}

function renderProgress() {
  if (!els.progress) return;
  const s = state.status || {};
  const scanning = s.status === 'scanning';
  const pending = Number(s.pending_count || 0);
  if (!scanning && !pending) {
    els.progress.classList.add('hidden');
    return;
  }
  els.progress.classList.remove('hidden');
  const pct = Number(s.percent || 0);
  if (els.progressBar) els.progressBar.style.width = `${pct}%`;
  if (els.progressText) {
    const current = s.current_file ? ` · ${s.current_file}` : '';
    els.progressText.textContent = scanning
      ? `${pct.toFixed(1)}% · ${fmtInt(pending)} left${current}`
      : `${fmtInt(pending)} files queued`;
  }
}

function renderNotice() {
  if (!els.notice) return;
  const s = state.status || {};
  const messages = [];
  if (!s.enabled) {
    messages.push(`
      <p class="font-semibold text-amber-100">Face recognition is off.</p>
      <p>Set <code class="px-1 rounded bg-slate-900 text-amber-200 font-mono">FACE_RECOGNITION_ENABLED=true</code>
      in the environment and restart the service to start scanning. Detection is local: no image ever leaves this machine.</p>
    `);
  } else if (s.engine_available === false) {
    messages.push(`
      <p class="font-semibold text-amber-100">The face engine is not ready yet.</p>
      <p>${escapeHtml(s.engine_reason || 'OpenCV or the face models are missing.')}</p>
      <p class="text-amber-300/80">The worker downloads YuNet + SFace into
      <code class="px-1 rounded bg-slate-900 font-mono">${escapeHtml(s.model_dir || 'DATA_DIR/models')}</code> automatically;
      a failed download retries every few minutes.</p>
    `);
  } else if (s.status === 'unavailable') {
    messages.push('<p class="font-semibold text-amber-100">The scanner is unavailable right now.</p>');
  }
  if (!messages.length) {
    els.notice.classList.add('hidden');
    els.notice.innerHTML = '';
    return;
  }
  els.notice.classList.remove('hidden');
  els.notice.innerHTML = messages.join('');
}

function renderScanControls() {
  const s = state.status || {};
  const usable = Boolean(s.enabled) && s.engine_available !== false;
  [els.scanAllBtn, els.scanFolderBtn].forEach((btn) => {
    if (btn) btn.disabled = !usable;
  });
  if (els.pauseBtn) {
    els.pauseBtn.textContent = s.is_paused ? 'Resume' : 'Pause';
    els.pauseBtn.disabled = !s.enabled;
  }
}

function applyStatus(status) {
  state.status = status || {};
  if (status && status.counts) state.counts = status.counts;
  renderCounts();
  renderPresets();
  renderProgress();
  renderNotice();
  renderScanControls();
}

async function refreshStatus() {
  try {
    applyStatus(await getJson('/api/people/status'));
  } catch (e) {
    // A status hiccup must not take the page down; the next poll retries.
  }
}

// ------------------------------------------------------------------- clusters

function personCardHtml(p) {
  const cover = p.cover_url
    ? `<img src="${escapeHtml(p.cover_url)}" alt="" loading="lazy" class="transition duration-200 group-hover:scale-[1.03]">`
    : '<div class="aspect-square flex items-center justify-center text-slate-600 text-2xl">🧑</div>';
  const name = p.name
    ? `<span class="text-[11px] font-semibold text-white truncate">${escapeHtml(p.name)}</span>`
    : '<span class="text-[11px] font-medium text-slate-500 italic truncate">Unnamed</span>';
  return `
    <button type="button" data-person="${p.id}" class="pp-card group bg-slate-900/50 border border-slate-800/70 rounded-xl overflow-hidden text-left hover:border-slate-600 transition focus:outline-none focus:border-emerald-600">
      ${cover}
      <div class="p-2 flex flex-col gap-0.5">
        ${name}
        <span class="text-[10px] text-slate-500 font-mono">${fmtInt(p.face_count)} faces · ${fmtInt(p.media_count)} files</span>
      </div>
    </button>
  `;
}

function renderPeople() {
  if (!els.grid) return;
  if (!state.people.length) {
    els.grid.innerHTML = '';
    els.gridEmpty.classList.remove('hidden');
    els.gridEmpty.textContent = (state.status && !state.status.enabled)
      ? 'Face recognition is disabled — enable it and scan to build clusters.'
      : 'No people yet. Scan the library to detect and cluster faces.';
    els.morePeopleBtn.classList.add('hidden');
    return;
  }
  els.gridEmpty.classList.add('hidden');
  els.grid.innerHTML = state.people.map(personCardHtml).join('');
  els.morePeopleBtn.classList.toggle('hidden', state.people.length >= state.total);
}

async function loadPeople(reset = true) {
  if (reset) {
    state.offset = 0;
    state.people = [];
  }
  const url = `/api/people?limit=${PEOPLE_PAGE}&offset=${state.offset}`;
  const data = await getJson(url);
  state.people = state.people.concat(data.people || []);
  state.total = data.total || state.people.length;
  state.counts = data.counts || state.counts;
  state.offset = state.people.length;
  applyStatus(data.status);
  renderPeople();
}

// ---------------------------------------------------------------- one person

function renderPerson() {
  const person = state.person;
  if (!person) return;
  const cover = state.faces[0] && state.faces[0].crop_url;
  if (els.personCover) {
    els.personCover.src = cover || '';
    els.personCover.classList.toggle('hidden', !cover);
  }
  if (els.personName && document.activeElement !== els.personName) {
    els.personName.value = person.name || '';
  }
  if (els.personMeta) {
    els.personMeta.textContent = `${fmtInt(person.face_count)} faces · ${fmtInt(person.media_total)} files`;
  }
  if (els.openInLibraryBtn) {
    const named = Boolean(person.name && person.name.trim());
    els.openInLibraryBtn.classList.toggle('hidden', !named);
    els.openInLibraryBtn.textContent = named ? `Search “${person.name}” in Library →` : '';
  }
  renderFaces();
  renderMedia();
}

function renderFaces() {
  if (!els.faces) return;
  if (!state.faces.length) {
    els.faces.innerHTML = '<p class="text-[11px] text-slate-500 col-span-full">No faces cached for this cluster.</p>';
  } else {
    els.faces.innerHTML = state.faces.map((f) => {
      const selected = state.selected.has(f.id);
      const when = f.frame_time ? ` · ${formatTime(f.frame_time)}` : '';
      const title = `${f.name || ''}${when}`;
      return `
        <button type="button" data-face="${f.id}" class="pp-face rounded-lg overflow-hidden border border-slate-800" aria-pressed="${selected ? 'true' : 'false'}" title="${escapeHtml(title)}">
          ${f.crop_url ? `<img src="${escapeHtml(f.crop_url)}" alt="" loading="lazy">` : '<span class="block aspect-square bg-slate-900"></span>'}
        </button>
      `;
    }).join('');
  }
  const n = state.selected.size;
  if (els.faceSelCount) els.faceSelCount.textContent = `${n} selected`;
  if (els.splitBtn) els.splitBtn.disabled = n === 0;
  if (els.dismissFacesBtn) els.dismissFacesBtn.disabled = n === 0;
}

function mediaCardHtml(f, index) {
  const isVid = f.type === 'video';
  const res = getResolutionLabel(f.width, f.height);
  const dur = isVid && f.duration ? formatDuration(f.duration) : '';
  const dir = folderLabel(f.path);
  const moments = (f.moments || []).slice(0, 6).map((m) => (
    `<button type="button" data-moment="${index}" data-time="${m.frame_time}" class="px-1.5 py-0.5 rounded bg-slate-800/80 hover:bg-slate-700 border border-slate-700 text-[9px] font-mono text-slate-300 transition" title="Open at ${formatTime(m.frame_time)}">▶ ${formatTime(m.frame_time)}</button>`
  )).join('');
  return `
    <div class="pp-media bg-slate-900/50 border border-slate-800/70 rounded-xl overflow-hidden flex flex-col">
      <button type="button" data-media="${index}" class="relative block w-full aspect-video bg-black">
        <img src="${escapeHtml(f.thumb_url)}" alt="" loading="lazy">
        ${dur ? `<span class="absolute bottom-1 right-1 px-1.5 py-0.5 rounded bg-slate-950/85 text-[10px] font-mono text-slate-200">${dur}</span>` : ''}
        ${isVid ? `<span class="absolute top-1 left-1 px-1.5 py-0.5 rounded bg-slate-950/85 text-[9px] font-mono text-violet-300">${fmtInt(f.frames)} frames</span>` : ''}
        ${res ? `<span class="absolute top-1 right-1 px-1.5 py-0.5 rounded bg-slate-950/85 text-[9px] font-mono text-emerald-300">${res}</span>` : ''}
      </button>
      <div class="p-2 flex flex-col gap-1 min-w-0">
        <span class="text-[11px] text-slate-200 truncate" title="${escapeHtml(f.path)}">${escapeHtml(f.name)}</span>
        <span class="text-[10px] text-slate-500 font-mono truncate">${escapeHtml(dir)} · ${escapeHtml(f.size_formatted || '')}</span>
        ${moments ? `<div class="flex flex-wrap gap-1">${moments}</div>` : ''}
      </div>
    </div>
  `;
}

function renderMedia() {
  if (!els.media) return;
  if (!state.media.length) {
    els.media.innerHTML = '<p class="text-[11px] text-slate-500 col-span-full">No indexed media for this cluster yet.</p>';
  } else {
    els.media.innerHTML = state.media.map(mediaCardHtml).join('');
  }
  if (els.mediaMeta) {
    els.mediaMeta.textContent = `${fmtInt(state.media.length)} of ${fmtInt(state.mediaTotal)} files shown`;
  }
  if (els.moreMediaBtn) {
    els.moreMediaBtn.classList.toggle('hidden', state.media.length >= state.mediaTotal);
  }
}

async function openPerson(personId) {
  state.selected = new Set();
  state.mediaOffset = 0;
  state.media = [];
  try {
    const data = await getJson(`/api/people/${personId}?limit=${MEDIA_PAGE}&offset=0&sort=${state.sort}`);
    state.person = data;
    state.faces = data.faces || [];
    state.facesTotal = data.face_count || 0;
    state.media = data.media || [];
    state.mediaTotal = data.media_total || 0;
    state.mediaOffset = state.media.length;
  } catch (e) {
    showToast(e.message, 'error');
    return;
  }
  els.peopleView.classList.add('hidden');
  els.personView.classList.remove('hidden');
  els.personView.classList.add('flex');
  renderPerson();
  scrollStageToTop();
}

function showPeopleView() {
  state.person = null;
  els.personView.classList.add('hidden');
  els.personView.classList.remove('flex');
  els.peopleView.classList.remove('hidden');
  els.peopleView.classList.add('flex');
  scrollStageToTop();
}

async function loadMoreFaces() {
  if (!state.person || state.faces.length >= state.facesTotal) return;
  const data = await getJson(`/api/people/${state.person.id}/faces?limit=${FACES_PAGE}&offset=${state.faces.length}`);
  state.faces = state.faces.concat(data.faces || []);
  state.facesTotal = data.total || state.facesTotal;
  renderFaces();
}

async function loadMoreMedia() {
  if (!state.person) return;
  const url = `/api/people/${state.person.id}?limit=${MEDIA_PAGE}&offset=${state.mediaOffset}&sort=${state.sort}`;
  const data = await getJson(url);
  const existing = new Set(state.media.map((m) => m.path));
  state.media = state.media.concat((data.media || []).filter((m) => !existing.has(m.path)));
  state.mediaTotal = data.media_total || state.mediaTotal;
  state.mediaOffset = state.media.length;
  renderMedia();
}

async function reloadMedia() {
  if (!state.person) return;
  state.media = [];
  state.mediaOffset = 0;
  const data = await getJson(`/api/people/${state.person.id}?limit=${MEDIA_PAGE}&offset=0&sort=${state.sort}`);
  state.media = data.media || [];
  state.mediaTotal = data.media_total || state.mediaTotal;
  state.mediaOffset = state.media.length;
  renderMedia();
}

// ---------------------------------------------------------------- lightbox

function mediaItems() {
  return state.media.map((f) => {
    const isVid = f.type === 'video';
    const item = {
      src: f.media_url,
      thumb: f.thumb_url,
      type: isVid ? 'html5video' : 'image',
      caption: `
        <div style="padding:.6rem .8rem;font:12px/1.4 Inter,sans-serif;color:#e7e5e4">
          <div style="font-weight:600">${escapeHtml(f.name)}</div>
          <div style="opacity:.7">${escapeHtml(folderLabel(f.path))} · ${escapeHtml(f.size_formatted || '')}</div>
        </div>
      `,
    };
    if (isVid && f.width && f.height) item.videoRatio = f.width / f.height;
    return item;
  });
}

function openMedia(startIndex, frameTime) {
  if (!window.Fancybox) {
    showToast('Lightbox library still loading…', 'info');
    return;
  }
  const items = mediaItems();
  if (!items.length) return;
  const index = Math.max(0, Math.min(items.length - 1, Number(startIndex) || 0));
  if (frameTime !== undefined && frameTime !== null) {
    items[index].src = `${items[index].src}#t=${Number(frameTime).toFixed(2)}`;
  }
  window.Fancybox.show(items, { startIndex: index, Thumbs: { type: 'classic' } });
}

// ------------------------------------------------------------------ curation

async function saveName() {
  if (!state.person || !els.personName) return;
  const name = els.personName.value.trim();
  try {
    await postJson('/api/people/rename', { person_id: state.person.id, name });
    state.person.name = name || null;
    const listed = state.people.find((p) => p.id === state.person.id);
    if (listed) {
      listed.name = name || null;
      listed.is_named = Boolean(name);
    }
    showToast(name ? `Named “${name}”` : 'Name cleared', 'success');
    renderPerson();
    renderPeople();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function dismissSelectedFaces() {
  const ids = Array.from(state.selected);
  if (!ids.length) return;
  if (!window.confirm(`Mark ${ids.length} face(s) as “not a person”? They will be ignored in every future scan.`)) return;
  try {
    await postJson('/api/people/dismiss', { face_ids: ids });
    showToast('Faces dismissed', 'success');
    state.selected = new Set();
    await openPerson(state.person.id);
    await refreshStatus();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function splitSelectedFaces() {
  const ids = Array.from(state.selected);
  if (!ids.length) return;
  try {
    const res = await postJson('/api/people/split', { face_ids: ids });
    showToast(`Split ${res.moved} face(s) into a new cluster`, 'success');
    state.selected = new Set();
    await openPerson(state.person.id);
    await refreshStatus();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function dismissWholePerson() {
  if (!state.person) return;
  const label = state.person.name ? `“${state.person.name}”` : 'this cluster';
  if (!window.confirm(`Mark every face of ${label} as “not a person”? The cluster is removed and those faces are ignored in future scans.`)) return;
  try {
    await postJson('/api/people/dismiss', { person_id: state.person.id });
    showToast('Cluster dismissed', 'success');
    showPeopleView();
    await loadPeople(true);
    await refreshStatus();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function rescanPersonFiles() {
  if (!state.person) return;
  const paths = state.media.map((m) => m.path);
  if (!paths.length) {
    showToast('No files to re-scan for this cluster', 'info');
    return;
  }
  try {
    const res = await postJson('/api/people/scan', { paths });
    showToast(`Queued ${res.queued} file(s) for re-scan`, 'success');
  } catch (e) {
    showToast(e.message, 'error');
  }
}

// --------------------------------------------------------------- merge modal

const mergeState = { selected: new Set(), filter: '' };

function renderMergeList() {
  if (!els.mergeList || !state.person) return;
  const options = state.people.filter((p) => p.id !== state.person.id);
  const needle = mergeState.filter.trim().toLowerCase();
  const shown = options.filter((p) => {
    if (!needle) return true;
    const label = (p.name || `Person ${p.id}`).toLowerCase();
    return label.includes(needle);
  });
  els.mergeList.innerHTML = shown.length
    ? shown.map((p) => {
      const on = mergeState.selected.has(p.id);
      const cover = p.cover_url
        ? `<img src="${escapeHtml(p.cover_url)}" alt="" class="w-11 h-11 rounded-lg object-cover bg-slate-950 border border-slate-800 shrink-0">`
        : '<span class="w-11 h-11 rounded-lg bg-slate-950 border border-slate-800 shrink-0"></span>';
      return `
        <button type="button" data-merge="${p.id}" aria-pressed="${on ? 'true' : 'false'}"
          class="flex items-center gap-3 p-2 rounded-xl border text-left transition ${on ? 'border-emerald-600 bg-emerald-600/10' : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'}">
          ${cover}
          <span class="min-w-0 flex flex-col">
            <span class="text-xs font-semibold text-white truncate">${escapeHtml(p.name || `Person ${p.id}`)}</span>
            <span class="text-[10px] text-slate-500 font-mono">${fmtInt(p.face_count)} faces · ${fmtInt(p.media_count)} files</span>
          </span>
          ${on ? '<span class="ml-auto text-emerald-400 text-xs font-bold">✓</span>' : ''}
        </button>
      `;
    }).join('')
    : '<p class="text-[11px] text-slate-500 p-3">No other clusters to merge.</p>';
  if (els.mergeCount) els.mergeCount.textContent = `${mergeState.selected.size} selected`;
}

function openMergeModal() {
  if (!state.person) return;
  mergeState.selected = new Set();
  mergeState.filter = '';
  if (els.mergeSearch) els.mergeSearch.value = '';
  if (els.mergeSubtitle) {
    els.mergeSubtitle.textContent = `Fold the selected clusters into “${state.person.name || 'this cluster'}”`;
  }
  els.mergeModal.classList.remove('hidden');
  els.mergeModal.classList.add('flex');
  document.body.classList.add('modal-scroll-lock');
  renderMergeList();
}

function closeMergeModal() {
  els.mergeModal.classList.add('hidden');
  els.mergeModal.classList.remove('flex');
  document.body.classList.remove('modal-scroll-lock');
}

async function confirmMerge() {
  if (!state.person || !mergeState.selected.size) {
    showToast('Pick at least one cluster to merge', 'info');
    return;
  }
  try {
    const res = await postJson('/api/people/merge', {
      target_id: state.person.id,
      source_ids: Array.from(mergeState.selected),
    });
    closeMergeModal();
    showToast(`Merged ${res.moved} face(s) in`, 'success');
    await openPerson(state.person.id);
    await loadPeople(true);
    await refreshStatus();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

// --------------------------------------------------------------- scan panel

async function scanLibrary(folder) {
  try {
    const payload = folder ? { folder } : { all: true };
    const res = await postJson('/api/people/scan', payload);
    showToast(folder
      ? `Queued ${res.queued} scan record(s) for that folder`
      : `Queued ${res.queued} file(s) for face scanning`, 'success');
    await refreshStatus();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function togglePause() {
  const paused = !(state.status && state.status.is_paused);
  try {
    await postJson('/api/people/pause', { paused });
    showToast(paused ? 'Face scanning paused' : 'Face scanning resumed', 'success');
    await refreshStatus();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function reclusterAll() {
  if (!window.confirm('Re-group every face now? Existing names are kept by matching each cluster to its nearest previous centroid.')) return;
  showToast('Re-grouping faces…', 'info');
  try {
    const res = await postJson('/api/people/recluster', {});
    showToast(`Re-grouped ${res.faces} faces into ${res.people} people (${res.kept} names kept)`, 'success');
    await loadPeople(true);
    await refreshStatus();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function applyPreset(name) {
  try {
    const res = await postJson('/api/people/threshold', { preset: name });
    localStorage.setItem(PRESET_KEY, name);
    state.status = Object.assign({}, state.status, {
      threshold: res.threshold,
      threshold_preset: res.preset,
      threshold_presets: res.presets,
    });
    renderPresets();
    showToast(`Match strictness: ${name} (${res.threshold}). Re-group to apply.`, 'info');
  } catch (e) {
    showToast(e.message, 'error');
  }
}

// ------------------------------------------------------------------ wiring

function initThemeCustomizer() {
  const modal = byId('themeCustomizerModal');
  const triggers = Array.from(document.querySelectorAll('[data-theme-modal-btn]'));
  const closeBtn = byId('closeThemeModalBtn');
  const applyBtn = byId('applyThemeModalCloseBtn');
  const resetBtn = byId('resetThemeDefaultsBtn');
  const accentPicker = byId('themeAccentPicker');
  const basePicker = byId('themeBasePicker');
  const seekToggle = byId('mobileDoubleTapSeekToggle');
  if (!modal || !window.THEME_MANAGER) return;

  function renderPickers() {
    const cur = window.THEME_MANAGER.getCurrent();
    if (accentPicker) {
      accentPicker.innerHTML = window.THEME_MANAGER.getAccents().map((acc) => {
        const sel = acc.key === cur.accent;
        return `
          <button type="button" data-accent="${acc.key}" class="theme-accent-btn group flex flex-col items-center gap-1.5 p-2 rounded-xl border transition cursor-pointer select-none ${sel ? 'border-emerald-500 bg-emerald-500/10 shadow-sm' : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'}">
            <span class="w-7 h-7 rounded-full border border-white/20" style="background:${acc.color}"></span>
            <span class="text-[10px] font-medium text-slate-300">${acc.label}</span>
          </button>
        `;
      }).join('');
    }
    if (basePicker) {
      basePicker.innerHTML = window.THEME_MANAGER.getBases().map((b) => {
        const sel = b.key === cur.base;
        return `
          <button type="button" data-base="${b.key}" class="theme-base-btn group flex flex-col items-start gap-1.5 p-2.5 rounded-xl border transition cursor-pointer text-left select-none ${sel ? 'border-emerald-500 bg-emerald-500/10 shadow-sm' : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'}">
            <span class="text-[11px] font-semibold text-slate-200">${b.label}</span>
            <span class="text-[10px] text-slate-500 leading-snug">${b.desc}</span>
          </button>
        `;
      }).join('');
    }
    if (seekToggle) {
      const on = Boolean(window.THEME_MANAGER.getDoubleTapSeek());
      seekToggle.setAttribute('aria-checked', String(on));
      seekToggle.classList.toggle('bg-emerald-600', on);
      seekToggle.classList.toggle('bg-slate-700', !on);
    }
  }

  function openModal() {
    renderPickers();
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    document.body.classList.add('modal-scroll-lock');
  }
  function closeModal() {
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    document.body.classList.remove('modal-scroll-lock');
  }

  triggers.forEach((btn) => btn.addEventListener('click', openModal));
  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  if (applyBtn) applyBtn.addEventListener('click', closeModal);
  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      window.THEME_MANAGER.reset();
      renderPickers();
    });
  }
  if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
  if (accentPicker) {
    accentPicker.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-accent]');
      if (!btn) return;
      window.THEME_MANAGER.setAccent(btn.dataset.accent);
      renderPickers();
    });
  }
  if (basePicker) {
    basePicker.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-base]');
      if (!btn) return;
      window.THEME_MANAGER.setBase(btn.dataset.base);
      renderPickers();
    });
  }
  if (seekToggle) {
    seekToggle.addEventListener('click', () => {
      window.THEME_MANAGER.setDoubleTapSeek(!window.THEME_MANAGER.getDoubleTapSeek());
      renderPickers();
    });
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) closeModal();
    if (e.key === 't' && !e.ctrlKey && !e.metaKey) {
      const btn = triggers.find((b) => b.offsetParent !== null);
      if (btn && document.activeElement && document.activeElement.tagName !== 'TEXTAREA') btn.click();
    }
  });
}

function initEvents() {
  if (els.grid) {
    els.grid.addEventListener('click', (e) => {
      const card = e.target.closest('[data-person]');
      if (card) openPerson(Number(card.dataset.person));
    });
  }
  if (els.morePeopleBtn) els.morePeopleBtn.addEventListener('click', () => loadPeople(false));
  if (els.backBtn) els.backBtn.addEventListener('click', showPeopleView);
  if (els.saveNameBtn) els.saveNameBtn.addEventListener('click', saveName);
  if (els.personName) {
    els.personName.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); saveName(); }
    });
  }
  if (els.faces) {
    els.faces.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-face]');
      if (!btn) return;
      const id = Number(btn.dataset.face);
      if (state.selected.has(id)) state.selected.delete(id);
      else state.selected.add(id);
      renderFaces();
      if (state.selected.size && state.faces.length >= state.facesTotal) return;
      if (state.faces.length < state.facesTotal && !state.selected.size) loadMoreFaces();
    });
  }
  if (els.splitBtn) els.splitBtn.addEventListener('click', splitSelectedFaces);
  if (els.dismissFacesBtn) els.dismissFacesBtn.addEventListener('click', dismissSelectedFaces);
  if (els.dismissPersonBtn) els.dismissPersonBtn.addEventListener('click', dismissWholePerson);
  if (els.rescanBtn) els.rescanBtn.addEventListener('click', rescanPersonFiles);
  if (els.mergeBtn) els.mergeBtn.addEventListener('click', openMergeModal);
  if (els.mergeCloseBtn) els.mergeCloseBtn.addEventListener('click', closeMergeModal);
  if (els.mergeCancelBtn) els.mergeCancelBtn.addEventListener('click', closeMergeModal);
  if (els.mergeConfirmBtn) els.mergeConfirmBtn.addEventListener('click', confirmMerge);
  if (els.mergeSearch) {
    els.mergeSearch.addEventListener('input', () => {
      mergeState.filter = els.mergeSearch.value;
      renderMergeList();
    });
  }
  if (els.mergeList) {
    els.mergeList.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-merge]');
      if (!btn) return;
      const id = Number(btn.dataset.merge);
      if (mergeState.selected.has(id)) mergeState.selected.delete(id);
      else mergeState.selected.add(id);
      renderMergeList();
    });
  }
  if (els.mergeModal) {
    els.mergeModal.addEventListener('click', (e) => { if (e.target === els.mergeModal) closeMergeModal(); });
  }
  if (els.openInLibraryBtn) {
    els.openInLibraryBtn.addEventListener('click', () => {
      if (!state.person || !state.person.name) return;
      window.location.href = `/?q=${encodeURIComponent(`person:${state.person.name}`)}`;
    });
  }
  if (els.media) {
    els.media.addEventListener('click', (e) => {
      const moment = e.target.closest('[data-moment]');
      if (moment) {
        openMedia(Number(moment.dataset.moment), Number(moment.dataset.time));
        return;
      }
      const card = e.target.closest('[data-media]');
      if (card) openMedia(Number(card.dataset.media));
    });
  }
  if (els.moreMediaBtn) els.moreMediaBtn.addEventListener('click', loadMoreMedia);
  if (els.mediaSort) {
    els.mediaSort.addEventListener('change', () => {
      state.sort = els.mediaSort.value || 'count';
      reloadMedia();
    });
  }
  if (els.scanAllBtn) els.scanAllBtn.addEventListener('click', () => scanLibrary(null));
  if (els.scanFolderBtn) {
    els.scanFolderBtn.addEventListener('click', () => {
      const value = (els.scanFolder && els.scanFolder.value.trim()) || '';
      if (!value) {
        showToast('Enter a folder path to scan', 'info');
        return;
      }
      scanLibrary(value);
    });
  }
  if (els.reclusterBtn) els.reclusterBtn.addEventListener('click', reclusterAll);
  if (els.pauseBtn) els.pauseBtn.addEventListener('click', togglePause);
  if (els.presets) {
    els.presets.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-preset]');
      if (btn) applyPreset(btn.dataset.preset);
    });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const modal = byId('ppMergeModal');
      if (modal && !modal.classList.contains('hidden')) closeMergeModal();
    }
  });
}

async function init() {
  cacheEls();
  hydrateIcons(document);
  initThemeCustomizer();
  initEvents();

  // ?folder= prefills the "scan folder" box, so the Library can deep-link here.
  const folderParam = new URLSearchParams(window.location.search).get('folder');
  if (folderParam && els.scanFolder) els.scanFolder.value = folderParam;

  try {
    await loadPeople(true);
  } catch (e) {
    els.error.classList.remove('hidden');
    els.error.textContent = `Could not load people: ${e.message}`;
  } finally {
    els.loading.classList.add('hidden');
    els.peopleView.classList.remove('hidden');
    els.peopleView.classList.add('flex');
  }

  await refreshStatus();
  // Status drives the progress bar and pause button while a sweep runs.
  setInterval(refreshStatus, 5000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
