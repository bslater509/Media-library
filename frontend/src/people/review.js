// /people/review — the unmatched-cluster review queue.
//
// One unnamed cluster at a time, biggest first (GET /api/people?named=false&
// limit=1&offset=0&samples=8), with three ways out of the queue:
//
//   * Match   — fold its faces into a person the user already named. The card
//               offers the six best guesses as name-only pills (ranked by the
//               folders they share with this cluster, `near=`), one tap each, and
//               "More…" (or M) opens the full list as a sheet. Matching posts the
//               same /api/people/merge the People page's picker uses, with the
//               named person as the target and the only source being the cluster
//               on screen.
//   * Name    — POST /api/people/rename, so the cluster becomes a new person.
//   * Dismiss — POST /api/people/dismiss with person_id: every one of its faces
//               is recorded as "not a person". Irreversible (there is no
//               un-dismiss endpoint), hence the confirm.
//
// All three take the cluster out of the unnamed set, so "the next one" is simply
// the largest remaining unnamed cluster — the queue keeps no state of its own and
// a reload lands exactly where a fresh queue would.
//
// The picker sheet behind "More…" is the mirror image of the People page's merge
// sheet: it lists *named* people (an unnamed cluster is never offered as a target
// — folding into it would just move the problem) with their crops and the same
// folder-path search, and matches on tap. A wrong match is unpicked on /people
// with Split / Not a person.
//
// Everything is index-only on the server and read from the local crop cache, so
// nothing here walks a mount.

import '../../../static/styles.css';
import { escapeHtml, showToast } from '../library/utils.js';
import { hydrateIcons, icon } from '../shared/icons.js';

//: Crops per card — a handful, so a cluster can be recognised without opening it.
const CROPS = 8;
//: Name-only pills on the card: the likely people, one tap each.
const SUGGESTION_PILLS = 6;
//: Named people per picker page (behind the pills' "More…").
const SUGGESTION_PAGE = 30;
//: Typing in the folder box re-queries the server; wait for a pause so a phone
//: keyboard does not fire a request per character.
const FILTER_DEBOUNCE_MS = 250;
//: Drag past this and a phone sheet closes.
const SHEET_DISMISS_PX = 120;

// The one source of truth for this page's keys: the reference sheet renders from
// this table, so a new binding only has to be described once.
export const REVIEW_SHORTCUTS = [
  {
    title: 'Review',
    iconHtml: icon('user-check', 'w-3.5 h-3.5'),
    entries: [
      { keys: ['1', '…', '9'], description: 'Match the numbered person pill straight away' },
      { keys: ['M'], description: 'Look through everyone you have named (the full list)' },
      { keys: ['N'], description: 'Name this cluster as a new person' },
      { keys: ['Enter'], description: 'Create the person whose name you typed' },
      { keys: ['X'], description: 'Mark the whole cluster as “not a person”' },
      { keys: ['?'], description: 'Show this key reference' },
      { keys: ['Esc'], description: 'Close the picker or this reference' },
    ],
  },
  {
    title: 'The full list (M)',
    iconHtml: icon('scan-face', 'w-3.5 h-3.5'),
    entries: [
      { keys: ['1', '…', '9'], description: 'Match the numbered person straight away' },
      { keys: ['/'], description: 'Jump to the folder filter box' },
    ],
  },
];

const els = {};
const state = {
  cluster: null,
  remaining: 0,
  counts: null,
  status: null,
  loading: false,
  // True while between two clusters, so neither the stale card nor the "nothing
  // left" state is painted during the fetch.
  pending: false,
  // The name pills on the card: the likely people for the cluster on screen, plus
  // how many named people exist in total (the "More…" pill and the M key).
  suggestions: [],
  suggestionsLoading: false,
  suggestionsError: false,
  namedTotal: 0,
};

function byId(id) {
  return document.getElementById(id);
}

function cacheEls() {
  els.mobileSubtitle = byId('prMobileSubtitle');
  els.stats = byId('prStats');
  els.notice = byId('prNotice');
  els.loading = byId('prLoading');
  els.error = byId('prError');
  els.done = byId('prDone');
  els.doneText = byId('prDoneText');
  els.reloadBtn = byId('prReloadBtn');
  els.card = byId('prCard');
  els.cover = byId('prCover');
  els.meta = byId('prMeta');
  els.folders = byId('prFolders');
  els.crops = byId('prCrops');
  els.dismissBtn = byId('prDismissBtn');
  els.nameInput = byId('prNameInput');
  els.nameBtn = byId('prNameBtn');
  els.suggestions = byId('prSuggestions');
  els.matchModal = byId('prMatchModal');
  els.matchList = byId('prMatchList');
  els.matchSearch = byId('prMatchSearch');
  els.matchCount = byId('prMatchCount');
  els.matchSubtitle = byId('prMatchSubtitle');
  els.matchSubject = byId('prMatchSubject');
  els.matchSubjectMeta = byId('prMatchSubjectMeta');
  els.matchCloseBtn = byId('prMatchCloseBtn');
  els.matchCancelBtn = byId('prMatchCancelBtn');
  els.keysModal = byId('prKeysModal');
  els.keysGroups = byId('prKeysGroups');
  els.keysCloseBtn = byId('prKeysCloseBtn');
  els.keysFooterBtn = byId('prKeysFooterBtn');
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

function chip(label, extra = '') {
  return `<span class="px-2 py-1 rounded-lg bg-slate-900/70 border border-slate-800 text-[10px] font-mono text-slate-300 ${extra}">${label}</span>`;
}

function folderBadge(label, tone = 'emerald', extra = '') {
  if (!label) return '';
  const style = tone === 'emerald'
    ? 'bg-emerald-500/10 border-emerald-900/40 text-emerald-300'
    : 'bg-slate-800/60 border-slate-700 text-slate-400';
  return `<span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-mono max-w-full truncate ${style} ${extra}">${icon('folder', 'w-3 h-3')}${escapeHtml(label)}</span>`;
}

// The page scrolls inside <main> (the body is a viewport-locked column), so a
// view switch resets that element rather than the document.
function scrollStageToTop() {
  const stage = document.querySelector('main');
  if (stage) stage.scrollTop = 0;
  else window.scrollTo(0, 0);
}

function isTyping(target) {
  if (!target || !target.tagName) return false;
  const tag = target.tagName.toUpperCase();
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
    || Boolean(target.isContentEditable);
}

// -------------------------------------------------------------------- status

function renderStats() {
  if (!els.stats) return;
  const c = state.counts || {};
  const s = state.status || {};
  els.stats.innerHTML = [
    chip(`${fmtInt(state.remaining)} to review`, 'text-emerald-300 border-emerald-900/40'),
    chip(`${fmtInt(c.named)} named`),
    c.dismissed ? chip(`${fmtInt(c.dismissed)} dismissed`) : '',
    s.pending_count ? chip(`${fmtInt(s.pending_count)} to scan`, 'text-amber-300 border-amber-900/40') : '',
  ].join('');
  if (els.mobileSubtitle) {
    // The chips are desktop-only (on a phone the header carries the counts, so
    // the card fits one screen), so the dismissed total rides along here.
    const bits = [
      `${fmtInt(state.remaining)} unmatched`,
      `${fmtInt(c.named)} named`,
    ];
    if (c.dismissed) bits.push(`${fmtInt(c.dismissed)} dismissed`);
    els.mobileSubtitle.textContent = bits.join(' · ');
  }
}

function renderNotice() {
  if (!els.notice) return;
  const s = state.status || {};
  const messages = [];
  if (!s.enabled) {
    messages.push(`
      <p class="font-semibold text-amber-100">Face recognition is off.</p>
      <p>Scanning is paused (set <code class="px-1 rounded bg-slate-900 text-amber-200 font-mono">FACE_RECOGNITION_ENABLED=true</code>),
      but the clusters already in the index can still be reviewed and named here.</p>
    `);
  } else if (s.engine_available === false) {
    messages.push(`
      <p class="font-semibold text-amber-100">The face engine is not ready.</p>
      <p>${escapeHtml(s.engine_reason || 'OpenCV or the face models are missing.')} Existing clusters can still be reviewed here.</p>
    `);
  }
  if (!messages.length) {
    els.notice.classList.add('hidden');
    els.notice.innerHTML = '';
    return;
  }
  els.notice.classList.remove('hidden');
  els.notice.innerHTML = messages.join('');
}

function applyStatus(status) {
  if (status) state.status = status;
  if (status && status.counts) state.counts = status.counts;
  renderStats();
  renderNotice();
}

// --------------------------------------------------------------- the cluster

function renderCluster() {
  const cluster = state.cluster;
  if (!cluster) {
    if (state.pending) {
      // Between two clusters: the loading block is already up, and neither the
      // stale card nor the "nothing left" state belongs on screen.
      return;
    }
    showDone();
    return;
  }

  if (els.done) {
    els.done.classList.add('hidden');
    els.done.classList.remove('flex');
  }
  if (els.loading) els.loading.classList.add('hidden');
  if (els.card) {
    els.card.classList.remove('hidden');
    els.card.classList.add('flex');
  }

  if (els.cover) {
    const cover = cluster.cover_url || (cluster.sample_urls || [])[0] || '';
    els.cover.src = cover;
    els.cover.classList.toggle('invisible', !cover);
  }
  if (els.meta) {
    els.meta.textContent = `Cluster #${cluster.id} · ${fmtInt(cluster.face_count)} faces · ${fmtInt(cluster.media_count)} files`;
  }
  if (els.folders) {
    const names = cluster.folder_names || [];
    els.folders.innerHTML = names.length
      ? `<span class="text-[10px] uppercase tracking-wider text-slate-500">Filed in</span>`
        + names.map((n) => folderBadge(n, 'neutral')).join('')
      : '<span class="text-[10px] text-slate-500">Its files are not in the index any more — dismissing is usually right here.</span>';
  }
  if (els.crops) {
    const samples = (cluster.sample_urls || []).slice(0, CROPS);
    els.crops.innerHTML = samples.length
      ? samples.map((url) => `<img src="${escapeHtml(url)}" alt="" loading="lazy" class="pr-face rounded-lg border border-slate-800">`).join('')
      : '<span class="col-span-full text-[11px] text-slate-500">No cached crops for this cluster.</span>';
  }
  if (els.nameInput && document.activeElement !== els.nameInput) {
    els.nameInput.value = '';
  }
  renderSuggestions();
  renderStats();
}

// The card's match suggestions as name-only pills: one tap matches, and the
// number on each is the key that does the same thing. The people that reach the
// pills are ranked by the folders they share with this cluster (`near=`), so the
// answer is usually the first one; "More…" opens the full list — the picker sheet
// with every named person, the folder-path search and the crops.
function pillHtml(person, index) {
  const shared = (person.shared_folder_names || [])[0] || '';
  const why = shared ? ` — filed in ${shared}` : '';
  const title = `${person.name} · ${fmtInt(person.face_count)} faces${why}`;
  return `
    <button type="button" data-pill="${person.id}" data-name="${escapeHtml(person.name || '')}" title="${escapeHtml(title)}"
      class="flex items-center gap-1 max-w-[11rem] px-2 py-1 rounded-full bg-slate-800 hover:bg-emerald-600 active:scale-95 border border-slate-700 hover:border-emerald-500 text-slate-100 text-[11px] font-semibold transition">
      ${index < 9 ? `<kbd class="text-[9px] font-mono text-slate-400">${index + 1}</kbd>` : ''}
      <span class="truncate">${escapeHtml(person.name || 'Unnamed')}</span>
    </button>
  `;
}

function renderSuggestions() {
  if (!els.suggestions) return;
  if (!state.suggestions.length) {
    const text = state.suggestionsError
      ? 'Could not load the people you named — press M to look through them.'
      : (state.suggestionsLoading
        ? 'Looking for people you named…'
        : (state.namedTotal
          ? 'Nothing of theirs is filed near this cluster — press M to look through everyone you named.'
          : 'No named people yet — name this cluster below (N).'));
    const tone = state.suggestionsError ? 'text-amber-300' : 'text-slate-500';
    els.suggestions.innerHTML = `<span class="text-[11px] ${tone}">${escapeHtml(text)}</span>`;
    return;
  }
  const hidden = Math.max(0, Number(state.namedTotal || 0) - state.suggestions.length);
  const more = hidden
    ? `
      <button type="button" data-more="1" title="Look through everyone you have named (M)"
        class="flex items-center gap-1.5 px-2 py-1 rounded-full bg-slate-950 hover:bg-slate-800 active:scale-95 border border-slate-700 text-slate-300 text-[11px] font-semibold transition">
        ${icon('search', 'w-3 h-3')}<span>More…</span>
        <span class="text-[9px] font-mono text-slate-500">${fmtInt(hidden)}</span>
      </button>
    `
    : '';
  els.suggestions.innerHTML = state.suggestions.map(pillHtml).join('') + more;
}

// Fetched per cluster, so the pills always answer for what is on screen. A stale
// response (the user acted before it landed) is dropped rather than painted.
async function loadCardSuggestions() {
  const cluster = state.cluster;
  if (!cluster) return;
  state.suggestions = [];
  state.suggestionsLoading = true;
  state.suggestionsError = false;
  renderSuggestions();
  try {
    const data = await getJson(
      `/api/people?named=true&near=${cluster.id}&limit=${SUGGESTION_PILLS}&samples=1`
    );
    if (state.cluster !== cluster) return;
    state.suggestions = (data.people || []).filter((p) => p.is_named);
    state.namedTotal = Math.max(state.suggestions.length, Number(data.total || 0));
    if (data.counts) {
      state.counts = data.counts;
      renderStats();
    }
    if (data.status) applyStatus(data.status);
  } catch (e) {
    if (state.cluster !== cluster) return;
    state.suggestionsError = true;
  } finally {
    if (state.cluster === cluster) {
      state.suggestionsLoading = false;
      renderSuggestions();
    }
  }
}

function showDone() {
  if (els.error) els.error.classList.add('hidden');
  if (els.card) {
    els.card.classList.add('hidden');
    els.card.classList.remove('flex');
  }
  if (els.loading) els.loading.classList.add('hidden');
  if (!els.done) return;
  els.done.classList.remove('hidden');
  els.done.classList.add('flex');
  const dismissed = state.counts ? Number(state.counts.dismissed || 0) : 0;
  if (els.doneText) {
    els.doneText.textContent = dismissed
      ? 'Every detected cluster has been matched to a named person, named itself, or dismissed '
        + `(${fmtInt(dismissed)} faces dismissed in total).`
      : 'Every detected cluster has been matched to a named person, named itself, or dismissed.';
  }
  renderStats();
}

function showLoading() {
  if (els.error) els.error.classList.add('hidden');
  if (els.card) {
    els.card.classList.add('hidden');
    els.card.classList.remove('flex');
  }
  if (els.done) {
    els.done.classList.add('hidden');
    els.done.classList.remove('flex');
  }
  if (els.loading) els.loading.classList.remove('hidden');
}

function showError(message) {
  if (els.loading) els.loading.classList.add('hidden');
  if (!els.error) return;
  els.error.innerHTML = `
    <p>Could not load the next cluster: ${escapeHtml(message)}</p>
    <button type="button" data-review-retry="1" class="mt-2 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-[11px] font-semibold transition">Try again</button>
  `;
  els.error.classList.remove('hidden');
}

async function loadNext() {
  if (state.loading) return;
  state.loading = true;
  state.pending = true;
  showLoading();
  try {
    // `limit=1` on the unnamed clusters, biggest first, is the whole queue: the
    // server orders by face count, so the next call returns the next cluster.
    const data = await getJson(
      `/api/people?named=false&limit=1&offset=0&samples=${CROPS}`
    );
    state.cluster = (data.people || [])[0] || null;
    state.remaining = Number(data.total || 0);
    if (data.counts) state.counts = data.counts;
    applyStatus(data.status);
    // The pills belong to the cluster on screen: clear them before painting, so
    // the previous cluster's people are never offered for this one.
    state.suggestions = [];
    state.suggestionsLoading = Boolean(state.cluster);
    state.suggestionsError = false;
    state.pending = false;
    renderCluster();
    scrollStageToTop();
    if (state.cluster) await loadCardSuggestions();
  } catch (e) {
    state.cluster = null;
    state.pending = false;
    showError(e.message);
  } finally {
    state.loading = false;
    if (els.loading) els.loading.classList.add('hidden');
  }
}

// Move to the next cluster: the action that just ran removed this one from the
// unnamed set, so re-reading the queue's head is the advance.
async function advance(message) {
  if (message) showToast(message, 'success');
  state.cluster = null;
  await loadNext();
}

// ------------------------------------------------------------------ curation

async function nameCluster() {
  const cluster = state.cluster;
  if (!cluster || !els.nameInput) return;
  const name = els.nameInput.value.trim();
  if (!name) {
    showToast('Type a name first', 'info');
    els.nameInput.focus();
    return;
  }
  try {
    await postJson('/api/people/rename', { person_id: cluster.id, name });
    els.nameInput.value = '';
    await advance(`Named “${name}” — ${fmtInt(cluster.face_count)} faces`);
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function dismissCluster() {
  const cluster = state.cluster;
  if (!cluster) return;
  if (!window.confirm(
    `Mark every face of this cluster (${cluster.face_count}) as “not a person”?\n\n`
    + 'They will be ignored by every future scan, and this cannot be undone.'
  )) return;
  try {
    const res = await postJson('/api/people/dismiss', { person_id: cluster.id });
    await advance(`Dismissed ${fmtInt(res.dismissed || cluster.face_count)} face(s)`);
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function matchCluster(personId, personName) {
  const cluster = state.cluster;
  if (!cluster) return;
  try {
    const res = await postJson('/api/people/merge', {
      target_id: personId,
      source_ids: [cluster.id],
    });
    closeSheet(els.matchModal);
    await advance(`Matched ${fmtInt(res.moved || cluster.face_count)} face(s) into “${personName}”`);
  } catch (e) {
    showToast(e.message, 'error');
  }
}

// -------------------------------------------------------------- match picker

const picker = {
  people: [],
  total: 0,
  hasMore: false,
  filter: '',
  loading: false,
  timer: null,
};

function suggestionRowHtml(person, index) {
  const faces = (person.sample_urls && person.sample_urls.length
    ? person.sample_urls
    : [person.cover_url]).filter(Boolean).slice(0, 3);
  const crops = faces.length
    ? faces.map((url) => `<img src="${escapeHtml(url)}" alt="" loading="lazy" class="w-16 h-16 rounded-xl object-cover bg-slate-950 border border-slate-800 shrink-0">`).join('')
    : '<span class="w-16 h-16 rounded-xl bg-slate-950 border border-slate-800 shrink-0"></span>';
  const shared = person.shared_folder_names || [];
  const matched = person.match_folders || [];
  const label = shared[0] || matched[0] || '';
  const badge = folderBadge(label, shared.length ? 'emerald' : 'neutral', 'max-w-full');
  // The key cap doubles as the hint that 1–9 matches straight away.
  const key = index < 9
    ? `<kbd class="shrink-0 px-1.5 py-0.5 rounded-md bg-slate-800 border border-slate-700 text-[10px] font-mono font-semibold text-slate-200">${index + 1}</kbd>`
    : '';
  return `
    <button type="button" data-match="${person.id}" data-name="${escapeHtml(person.name || '')}" class="pr-row flex items-center gap-3 p-2 rounded-xl border border-slate-800 bg-slate-950/60 hover:border-emerald-700 hover:bg-emerald-600/5 text-left transition">
      ${key}
      <span class="flex items-center gap-1 shrink-0">${crops}</span>
      <span class="min-w-0 flex flex-1 flex-col gap-0.5">
        <span class="text-[12px] font-semibold text-white truncate">${escapeHtml(person.name || 'Unnamed')}</span>
        <span class="text-[10px] text-slate-500 font-mono truncate">${fmtInt(person.face_count)} faces · ${fmtInt(person.media_count)} files</span>
        ${badge}
      </span>
    </button>
  `;
}

function renderPicker() {
  if (!els.matchList) return;
  const left = Math.max(0, picker.total - picker.people.length);
  const more = picker.hasMore
    ? `<button type="button" data-match-more="1" class="w-full px-3 py-2 rounded-xl bg-slate-800/70 hover:bg-slate-700 border border-slate-700 text-[11px] font-semibold text-slate-200 transition">Show more${left ? ` (${fmtInt(left)} more)` : ''}</button>`
    : '';
  const needle = picker.filter.trim();
  if (!picker.people.length) {
    const empty = picker.loading
      ? 'Searching the people you have named…'
      : (needle
        ? `No named person has a face in a folder matching “${escapeHtml(needle)}”.`
        : 'No named people yet — close this and name a cluster first (N).');
    els.matchList.innerHTML = `<p class="text-[11px] text-slate-500 p-3">${empty}</p>${more}`;
  } else {
    els.matchList.innerHTML = picker.people.map(suggestionRowHtml).join('') + more;
  }
  if (els.matchCount) {
    els.matchCount.textContent = picker.people.length
      ? `${fmtInt(picker.people.length)} of ${fmtInt(Math.max(picker.total, picker.people.length))} named`
      : '—';
  }
}

async function loadSuggestions(reset = false) {
  const cluster = state.cluster;
  if (!cluster || picker.loading) return;
  if (reset) {
    picker.people = [];
    picker.total = 0;
    picker.hasMore = false;
  }
  picker.loading = true;
  renderPicker();
  try {
    const offset = picker.people.length;
    const filter = picker.filter.trim();
    // `named=true` is the mirror of the People page's picker: only people the
    // user has named can be a merge target here. `near` ranks the ones filed in
    // the same folders as this cluster first, so the likely answer is on top.
    const query = [
      `limit=${SUGGESTION_PAGE}`,
      `offset=${offset}`,
      'named=true',
      `near=${cluster.id}`,
      `samples=3`,
      filter ? `folder=${encodeURIComponent(filter)}` : '',
    ].filter(Boolean).join('&');
    const data = await getJson(`/api/people?${query}`);
    const raw = data.people || [];
    const seen = new Set(picker.people.map((p) => p.id));
    // Trust the query, but keep the picker's own rule (named only) so a stale
    // row can never offer an unnamed cluster as a target.
    const fresh = raw.filter((p) => p.is_named && !seen.has(p.id));
    picker.people = picker.people.concat(fresh);
    picker.total = Math.max(picker.people.length, Number(data.total || 0));
    picker.hasMore = raw.length >= SUGGESTION_PAGE && fresh.length > 0;
    if (data.counts) {
      state.counts = data.counts;
      renderStats();
    }
    if (data.status) applyStatus(data.status);
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    picker.loading = false;
    renderPicker();
  }
}

function openMatchPicker() {
  const cluster = state.cluster;
  if (!cluster) return;
  clearTimeout(picker.timer);
  picker.filter = '';
  picker.people = [];
  picker.total = 0;
  picker.hasMore = false;
  if (els.matchSearch) els.matchSearch.value = '';
  if (els.matchSubtitle) {
    els.matchSubtitle.textContent = 'Their faces move into the person you tap';
  }
  if (els.matchSubject) {
    const cover = cluster.cover_url || (cluster.sample_urls || [])[0] || '';
    els.matchSubject.src = cover;
    els.matchSubject.classList.toggle('invisible', !cover);
  }
  if (els.matchSubjectMeta) {
    els.matchSubjectMeta.textContent = `Matching cluster #${cluster.id} · ${fmtInt(cluster.face_count)} faces`;
  }
  renderPicker();
  openSheet(els.matchModal);
  loadSuggestions(true);
}

// ---------------------------------------------------------------------- keys

function renderKeysHelp() {
  if (!els.keysGroups) return;
  els.keysGroups.innerHTML = REVIEW_SHORTCUTS.map((group) => `
    <section class="flex flex-col gap-1.5">
      <h4 class="text-[11px] font-bold uppercase tracking-wide text-slate-400 flex items-center gap-1.5">
        ${group.iconHtml}<span>${escapeHtml(group.title)}</span>
      </h4>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-1">
        ${group.entries.map((entry) => `
          <div class="flex items-center justify-between gap-2 px-2 py-1.5 rounded-lg bg-slate-950/60 border border-slate-800/80">
            <span class="text-[11px] text-slate-300 min-w-0">${escapeHtml(entry.description)}</span>
            <span class="flex items-center gap-0.5 shrink-0">${entry.keys
              .map((k) => `<kbd class="px-1.5 py-0.5 rounded-md bg-slate-800 border border-slate-700 text-[10px] font-mono font-semibold text-slate-200 whitespace-nowrap">${escapeHtml(k)}</kbd>`)
              .join('<span class="text-slate-600 text-[10px]">+</span>')}</span>
          </div>`).join('')}
      </div>
    </section>`).join('');
}

// -------------------------------------------------------------------- sheets

function openSheet(modal) {
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.classList.add('flex');
  document.body.classList.add('modal-scroll-lock');
}

function closeSheet(modal) {
  if (!modal) return;
  modal.classList.add('hidden');
  modal.classList.remove('flex');
  document.body.classList.remove('modal-scroll-lock');
}

function isSheetOpen(modal) {
  return Boolean(modal && !modal.classList.contains('hidden'));
}

function openKeysHelp() {
  renderKeysHelp();
  openSheet(els.keysModal);
}

// Swipe-to-dismiss, mirroring the other mobile sheets: the drag starts on the
// grabber handle or the header background (never on a button or the filter
// input, so tapping and typing keep working). No-ops on desktop and under
// prefers-reduced-motion, where the sheet is a plain centred dialog.
function initSheetDrag(modal) {
  if (!modal) return;
  if (!window.matchMedia('(max-width: 767px)').matches) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const card = modal.querySelector('.ml-sheet-card');
  if (!card) return;
  const grabber = card.querySelector('[data-sheet-grabber]');
  const header = grabber ? grabber.nextElementSibling : null;
  let startY = 0;
  let dragging = false;

  const onPointerDown = (e) => {
    if (dragging) return;
    const el = e.target;
    const interactive = el.closest
      && el.closest('button, input, select, textarea, a, label, [role="button"]');
    const inHeader = header && !interactive
      && (el === header || (header.contains && header.contains(el)));
    if (!(grabber && el.closest('[data-sheet-grabber]')) && !inHeader) return;
    startY = e.clientY;
    dragging = true;
    card.classList.add('ml-dragging');
    try { card.setPointerCapture(e.pointerId); } catch (err) { /* unsupported */ }
  };

  const onPointerMove = (e) => {
    if (!dragging) return;
    card.style.transform = `translateY(${Math.max(0, e.clientY - startY)}px)`;
  };

  const onPointerUp = (e) => {
    if (!dragging) return;
    dragging = false;
    const dy = Math.max(0, (e.clientY || 0) - startY);
    card.classList.remove('ml-dragging');
    card.classList.add('ml-snap-back');
    if (dy > SHEET_DISMISS_PX) {
      card.style.transform = 'translateY(105%)';
      setTimeout(() => {
        card.classList.remove('ml-snap-back');
        card.style.transform = '';
        closeSheet(modal);
      }, 240);
    } else {
      card.style.transform = 'translateY(0)';
      setTimeout(() => {
        card.classList.remove('ml-snap-back');
        card.style.transform = '';
      }, 280);
    }
  };

  card.addEventListener('pointerdown', onPointerDown);
  card.addEventListener('pointermove', onPointerMove);
  card.addEventListener('pointerup', onPointerUp);
  card.addEventListener('pointercancel', onPointerUp);
}

// ------------------------------------------------------------------ keyboard

// Exported so the key-reference tests can drive the real branches with a
// synthetic event: every import of this module adds a document-level listener,
// and a test that dispatched real keydown events would be answered by every
// earlier import as well.
export function onKeydown(e) {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const key = e.key;
  const typing = isTyping(e.target);

  // The picker is modal: its own keys win while it is up.
  if (isSheetOpen(els.matchModal)) {
    if (key === 'Escape') {
      e.preventDefault();
      closeSheet(els.matchModal);
      return;
    }
    if (typing) return;
    if (key === '/') {
      e.preventDefault();
      if (els.matchSearch) els.matchSearch.focus();
      return;
    }
    if (/^[1-9]$/.test(key)) {
      const person = picker.people[Number(key) - 1];
      if (person) {
        e.preventDefault();
        matchCluster(person.id, person.name || 'Unnamed');
      }
    }
    return;
  }

  if (key === 'Escape' && isSheetOpen(els.keysModal)) {
    e.preventDefault();
    closeSheet(els.keysModal);
    return;
  }
  if (typing) return;
  if (key === '?') {
    e.preventDefault();
    if (isSheetOpen(els.keysModal)) closeSheet(els.keysModal);
    else openKeysHelp();
    return;
  }
  if (!state.cluster) return;
  const lower = key.toLowerCase();
  if (lower === 'm') {
    e.preventDefault();
    if (state.namedTotal) openMatchPicker();
    else showToast('Name a cluster first — there is nobody to match against yet', 'info');
    return;
  }
  if (lower === 'n') {
    e.preventDefault();
    if (els.nameInput) {
      els.nameInput.focus();
      els.nameInput.select();
    }
    return;
  }
  if (lower === 'x') {
    e.preventDefault();
    dismissCluster();
    return;
  }
  // A digit matches the pill carrying it, exactly as tapping it would.
  if (/^[1-9]$/.test(key)) {
    const person = state.suggestions[Number(key) - 1];
    if (person) {
      e.preventDefault();
      matchCluster(person.id, person.name || 'Unnamed');
    }
  }
}

// ------------------------------------------------------------------- wiring

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
  modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
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
}

function initEvents() {
  if (els.nameBtn) els.nameBtn.addEventListener('click', nameCluster);
  if (els.dismissBtn) els.dismissBtn.addEventListener('click', dismissCluster);
  if (els.reloadBtn) els.reloadBtn.addEventListener('click', loadNext);
  if (els.suggestions) {
    els.suggestions.addEventListener('click', (e) => {
      if (e.target.closest('[data-more]')) {
        openMatchPicker();
        return;
      }
      const pill = e.target.closest('[data-pill]');
      if (pill) matchCluster(Number(pill.dataset.pill), pill.dataset.name || 'Unnamed');
    });
  }
  if (els.nameInput) {
    els.nameInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        nameCluster();
      }
    });
  }
  if (els.matchCloseBtn) els.matchCloseBtn.addEventListener('click', () => closeSheet(els.matchModal));
  if (els.matchCancelBtn) els.matchCancelBtn.addEventListener('click', () => closeSheet(els.matchModal));
  if (els.keysCloseBtn) els.keysCloseBtn.addEventListener('click', () => closeSheet(els.keysModal));
  if (els.keysFooterBtn) els.keysFooterBtn.addEventListener('click', () => closeSheet(els.keysModal));
  if (els.matchModal) {
    els.matchModal.addEventListener('click', (e) => { if (e.target === els.matchModal) closeSheet(els.matchModal); });
  }
  if (els.keysModal) {
    els.keysModal.addEventListener('click', (e) => { if (e.target === els.keysModal) closeSheet(els.keysModal); });
  }
  if (els.matchSearch) {
    // The folder box is a server query (it must search every named person, not
    // just the loaded page), debounced so typing does not fire per key.
    els.matchSearch.addEventListener('input', () => {
      picker.filter = els.matchSearch.value;
      clearTimeout(picker.timer);
      picker.timer = setTimeout(() => loadSuggestions(true), FILTER_DEBOUNCE_MS);
    });
  }
  if (els.matchList) {
    els.matchList.addEventListener('click', (e) => {
      if (e.target.closest('[data-match-more]')) {
        loadSuggestions(false);
        return;
      }
      const btn = e.target.closest('[data-match]');
      if (!btn) return;
      matchCluster(Number(btn.dataset.match), btn.dataset.name || 'Unnamed');
    });
  }
  if (els.error) {
    els.error.addEventListener('click', (e) => {
      if (e.target.closest('[data-review-retry]')) loadNext();
    });
  }
  document.addEventListener('keydown', onKeydown);
}

async function init() {
  cacheEls();
  hydrateIcons(document);
  initThemeCustomizer();
  initEvents();
  initSheetDrag(els.matchModal);
  initSheetDrag(els.keysModal);

  await loadNext();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
