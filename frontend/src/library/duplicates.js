import { icon } from '../shared/icons.js';
import { els, state } from './state.js';
import { escapeHtml, showToast } from './utils.js';
import { formatDate } from './management.js';
import { syncDuplicatesCount } from './theme.js';

// Duplicate review flow and ignored-duplicate management.
// Extracted from the former monolithic library index.js.

async function fetchDuplicateSummary() {
  try {
    const res = await fetch('/api/backup/duplicates?mode=content&limit=1');
    if (!res.ok) return;
    const data = await res.json();
    state.duplicates.totalGroups = data.total_groups || 0;
    state.duplicates.reclaimableFormatted = data.reclaimable_formatted || '0 GB';
    syncDuplicatesCount(data.total_groups || 0);
  } catch (e) {
    console.error('Error fetching duplicate summary:', e);
  }
}
function openDuplicatesModal() {
  state.duplicates.isOpen = true;
  if (els.duplicatesModal) {
    els.duplicatesModal.classList.remove('hidden');
    if (els.duplicateCardsContainer) {
      els.duplicateCardsContainer.scrollTop = 0;
    }
    loadDuplicateGroups(true);
  }
}
function closeDuplicatesModal() {
  state.duplicates.isOpen = false;
  if (els.duplicatesModal) {
    const videos = els.duplicatesModal.querySelectorAll('video');
    videos.forEach(v => {
      try { v.pause(); } catch (e) {}
    });
    els.duplicatesModal.classList.add('hidden');
  }
}
function setDuplicatesMode(newMode) {
  if (newMode !== 'content' && newMode !== 'visual') newMode = 'content';
  if (state.duplicates.mode === newMode) return;
  state.duplicates.mode = newMode;

  const baseClass = 'px-2.5 sm:px-3 py-1 rounded-lg transition text-center truncate font-medium';
  const activeClass = `${baseClass} bg-amber-600 text-white font-semibold shadow-sm`;
  const visualActiveClass = `${baseClass} bg-sky-600 text-white font-semibold shadow-sm flex items-center justify-center gap-1`;
  const inactiveClass = `${baseClass} text-slate-400 hover:text-white`;
  const visualInactiveClass = `${baseClass} text-slate-400 hover:text-white flex items-center justify-center gap-1`;

  if (els.dupModeContentBtn) els.dupModeContentBtn.className = (newMode === 'content') ? activeClass : inactiveClass;
  if (els.dupModeVisualBtn) els.dupModeVisualBtn.className = (newMode === 'visual') ? visualActiveClass : visualInactiveClass;

  loadDuplicateGroups(true);
}
async function loadDuplicateGroups(resetIndex = true) {
  if (resetIndex) {
    state.duplicates.currentIndex = 0;
  }
  if (els.duplicateCardsContainer) {
    els.duplicateCardsContainer.innerHTML = `
      <div class="col-span-full py-16 flex flex-col items-center justify-center gap-3 text-slate-400">
        <div class="w-8 h-8 border-2 border-amber-500 border-t-transparent rounded-full animate-spin"></div>
        <span class="text-xs font-mono">Analyzing duplicate sets...</span>
      </div>
    `;
  }

  try {
    updateIgnoredCountBadge();

    const res = await fetch(`/api/backup/duplicates?mode=${state.duplicates.mode}&limit=100`);
    if (!res.ok) throw new Error('Failed to load duplicates');
    const data = await res.json();
    state.duplicates.groups = data.groups || [];
    state.duplicates.totalGroups = data.total_groups || 0;
    state.duplicates.reclaimableFormatted = data.reclaimable_formatted || '0 GB';
    state.duplicates.verifiedGroupsCount = data.verified_groups_count || 0;
    state.duplicates.verifiedReclaimableFormatted = data.verified_reclaimable_formatted || '0 B';

    if (els.duplicatesTotalReclaimable) els.duplicatesTotalReclaimable.textContent = data.reclaimable_formatted || '0 GB';
    if (els.duplicatesTotalSets) els.duplicatesTotalSets.textContent = `${data.total_groups} duplicate sets`;
    if (els.duplicatesHeaderBadge) {
      if (data.total_groups > 0) {
        els.duplicatesHeaderBadge.textContent = data.total_groups;
        els.duplicatesHeaderBadge.classList.remove('hidden');
      } else {
        els.duplicatesHeaderBadge.classList.add('hidden');
      }
    }

    if (els.autoDeleteBadgeCount) {
      els.autoDeleteBadgeCount.textContent = state.duplicates.verifiedGroupsCount;
    }
    if (els.autoDeleteAllVerifiedBtn) {
      if (state.duplicates.mode === 'visual') {
        els.autoDeleteAllVerifiedBtn.disabled = true;
        els.autoDeleteAllVerifiedBtn.classList.add('opacity-40', 'pointer-events-none');
        els.autoDeleteAllVerifiedBtn.title = 'Visual matches require manual review (auto-delete applies only to exact chunk hash matches)';
      } else if (state.duplicates.verifiedGroupsCount > 0) {
        els.autoDeleteAllVerifiedBtn.disabled = false;
        els.autoDeleteAllVerifiedBtn.classList.remove('opacity-40', 'pointer-events-none');
        els.autoDeleteAllVerifiedBtn.title = `Auto-delete duplicate copies across ${state.duplicates.verifiedGroupsCount} verified sets (~${state.duplicates.verifiedReclaimableFormatted} reclaimable)`;
      } else {
        els.autoDeleteAllVerifiedBtn.disabled = true;
        els.autoDeleteAllVerifiedBtn.classList.add('opacity-40', 'pointer-events-none');
        els.autoDeleteAllVerifiedBtn.title = 'No 100% hash-verified duplicate sets available to auto-delete';
      }
    }

    renderCurrentDuplicateGroup();
  } catch (e) {
    console.error(e);
    if (els.duplicateCardsContainer) {
      els.duplicateCardsContainer.innerHTML = `
        <div class="col-span-full py-16 text-center text-xs text-rose-400">
          Failed to scan duplicates: ${e.message}
        </div>
      `;
    }
  }
}
function renderCurrentDuplicateGroup() {
  const groups = state.duplicates.groups;
  const total = groups.length;
  const idx = state.duplicates.currentIndex;

  if (total === 0) {
    els.duplicatesGroupPill.textContent = '0 sets';
    els.duplicateGroupCounter.textContent = '0 / 0';
    els.prevDuplicateGroupBtn.disabled = true;
    els.nextDuplicateGroupBtn.disabled = true;
    els.duplicatesVerificationBadge.className = 'text-[11px] font-mono px-2 sm:px-2.5 py-0.5 rounded-lg bg-emerald-950 text-emerald-300 border border-emerald-800 flex items-center gap-1.5 min-w-0 truncate max-w-full';
    els.duplicatesVerificationBadge.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-400 shrink-0"></span> <span class="truncate">All Clean</span>';
    els.duplicatesGroupSizeBadge.textContent = '';
    els.duplicateCardsContainer.innerHTML = `
      <div class="col-span-full py-20 flex flex-col items-center justify-center gap-3 text-center">
        <div class="w-16 h-16 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-3xl">🎉</div>
        <h3 class="text-sm font-bold text-white">No duplicates found!</h3>
        <p class="text-xs text-slate-400 max-w-sm">Every file in this mode is unique across your Backup collection.</p>
      </div>
    `;
    return;
  }

  if (idx < 0) state.duplicates.currentIndex = 0;
  if (idx >= total) state.duplicates.currentIndex = total - 1;
  const currentIdx = state.duplicates.currentIndex;
  const group = groups[currentIdx];

  els.duplicatesGroupPill.textContent = `Set ${currentIdx + 1} of ${total}`;
  els.duplicateGroupCounter.textContent = `${currentIdx + 1} / ${total}`;
  els.prevDuplicateGroupBtn.disabled = (currentIdx === 0);
  els.nextDuplicateGroupBtn.disabled = (currentIdx === total - 1);

  if (group.match_type === 'visual') {
    els.duplicatesSubtitle.textContent = `Visual Match: ~${group.similarity}% thumbnail similarity (${group.files_count} files, ${group.reclaimable_formatted} reclaimable)`;
    const isAllImages = group.files && group.files.every(f => f.type === 'image');
    const diffText = (typeof group.max_distance === 'number') 
      ? (group.max_distance === 0 ? (isAllImages ? '100% Exact Image Match' : 'Exact 0-bit match (100%)') : `Hamming diff: ≤ ${group.max_distance} / 64 bits`)
      : `Hamming diff: ${group.min_distance ?? 0} / 64 bits`;
    els.duplicatesGroupSizeBadge.textContent = diffText;
  } else {
    els.duplicatesSubtitle.textContent = `Exact content match: ${group.size_formatted} each (${group.reclaimable_formatted} reclaimable)`;
    els.duplicatesGroupSizeBadge.textContent = `Exact size & hash: ${group.size_formatted} (${group.files_count} files)`;
  }

  // Explicitly abort and unload previous video streams to release network sockets immediately
  const oldVideos = els.duplicateCardsContainer.querySelectorAll('video');
  oldVideos.forEach(v => {
    try {
      v.pause();
      v.removeAttribute('src');
      v.load();
    } catch (e) {}
  });

  els.duplicateCardsContainer.scrollTop = 0;
  els.duplicateCardsContainer.innerHTML = group.files.map((file, fIdx) => {
    const isRecommended = (fIdx === group.suggested_keep_index);
    const isVid = (file.type === 'video');

    return `
      <div class="bg-slate-950 rounded-2xl border ${isRecommended ? 'border-emerald-500/60 ring-1 ring-emerald-500/30 shadow-emerald-950/40' : 'border-slate-800'} p-3.5 flex flex-col gap-3 shadow-xl transition relative group">
        
        <!-- Card Header / Badges -->
        <div class="flex items-center justify-between gap-2 shrink-0">
          ${isRecommended
            ? `<span class="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 flex items-center gap-1 shadow-sm">
                 <span>⭐</span> Recommended Keep
               </span>`
            : `<span class="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-slate-800/80 text-slate-400 border border-slate-700/80">
                 Alternate Copy #${fIdx + 1}
               </span>`
          }
          <span class="text-[11px] font-mono font-medium text-slate-400 shrink-0">${escapeHtml(file.size_formatted)}</span>
        </div>

        <!-- Video / Image Preview Player -->
        <div class="relative bg-black rounded-xl overflow-hidden aspect-video border border-slate-800/90 flex items-center justify-center shadow-inner">
          ${isVid ? `
            <video 
              src="${file.media_url}" 
              poster="${file.thumb_url}" 
              controls 
              preload="none" 
              playsinline 
              class="w-full h-full object-contain pointer-events-auto bg-black"
            ></video>
          ` : `
            <a href="${file.media_url}" target="_blank" class="block w-full h-full flex items-center justify-center cursor-zoom-in" title="View full image">
              <img 
                src="${file.media_url}" 
                alt="${escapeHtml(file.name)}" 
                loading="lazy" 
                class="w-full h-full object-contain" 
              />
            </a>
          `}
        </div>

        <!-- Metadata & Location -->
        <div class="flex flex-col gap-1 min-w-0">
          <span class="text-xs font-bold text-white break-all hover:text-emerald-300 transition" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
          <div class="flex items-center gap-1 text-[11px] text-slate-400 font-mono truncate" title="${escapeHtml(file.folder_path)}">
            <span class="text-slate-500 shrink-0">📁</span>
            <a href="/?folder=${encodeURIComponent(file.folder_path)}" target="_blank" class="truncate hover:text-emerald-400 hover:underline">
              ${escapeHtml(file.folder_display)}
            </a>
          </div>
          <div class="flex items-center gap-2 text-[10px] text-slate-500 font-mono mt-0.5">
            <span>Date: ${formatDate(file.mtime)}</span>
          </div>
        </div>

        <!-- Action Buttons -->
        <div class="flex flex-col gap-1.5 mt-auto pt-3 border-t border-slate-800/80">
          <button 
            onclick="window.libraryApp.resolveDuplicateSet(${fIdx}, this)"
            class="w-full py-2 sm:py-1.5 px-3 rounded-xl bg-emerald-600 hover:bg-emerald-500 active:scale-98 text-white text-xs font-semibold flex items-center justify-center gap-1.5 transition shadow-sm cursor-pointer disabled:opacity-50 disabled:pointer-events-none"
            title="Keep this file and safely delete all other copies in this set"
          >
            <span>✓</span> Keep This (Delete Others)
          </button>
          <button 
            onclick="window.libraryApp.deleteSingleDuplicateFile(${fIdx}, this)"
            class="w-full py-2 sm:py-1.5 px-3 rounded-xl bg-slate-900 hover:bg-rose-900/60 hover:text-rose-200 border border-slate-800 text-slate-400 text-xs font-medium flex items-center justify-center gap-1.5 transition cursor-pointer disabled:opacity-50 disabled:pointer-events-none"
            title="Delete only this specific file"
          >
            <span>🗑️</span> Delete This Copy Only
          </button>
        </div>

      </div>
    `;
  }).join('');

  verifyCurrentGroupHashes(group);
}
let currentVerifyAbort = null;
async function verifyCurrentGroupHashes(group) {
  if (currentVerifyAbort) {
    currentVerifyAbort.abort();
    currentVerifyAbort = null;
  }

  if (!group) return;

  if (group.match_type === 'visual') {
    els.duplicatesVerificationBadge.className = 'text-[11px] font-mono px-2 sm:px-2.5 py-0.5 rounded-lg bg-sky-950 text-sky-300 border border-sky-800 flex items-center gap-1.5 min-w-0 truncate max-w-full';
    els.duplicatesVerificationBadge.innerHTML = `<span class="w-2 h-2 rounded-full bg-sky-400 shrink-0"></span> <span class="truncate">👁️ ${group.similarity}% Visual Match <span class="hidden sm:inline">(Manual Review)</span></span>`;
    return;
  }


  // If all files in group already have quick_hash cached, verify instantly in JS
  const knownHashes = group.files.map(f => f.quick_hash).filter(Boolean);
  if (knownHashes.length === group.files.length && group.files.length > 0) {
    const allMatch = (new Set(knownHashes).size === 1);
    if (allMatch) {
      els.duplicatesVerificationBadge.className = 'text-[11px] font-mono px-2 sm:px-2.5 py-0.5 rounded-lg bg-emerald-950 text-emerald-300 border border-emerald-800 flex items-center gap-1.5 min-w-0 truncate max-w-full';
      els.duplicatesVerificationBadge.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-400 shrink-0"></span> <span class="truncate">✓ 100% Content Hash Verified</span>';

      if (state.duplicates.autoDeleteOnMatch && !isResolvingDuplicateSet) {
        const keepIdx = (typeof group.suggested_keep_index === 'number') ? group.suggested_keep_index : 0;
        setTimeout(() => {
          if (state.duplicates.autoDeleteOnMatch && !isResolvingDuplicateSet && state.duplicates.groups[state.duplicates.currentIndex] === group) {
            resolveDuplicateSet(keepIdx);
          }
        }, 350);
      }
    } else {
      els.duplicatesVerificationBadge.className = 'text-[11px] font-mono px-2 sm:px-2.5 py-0.5 rounded-lg bg-amber-950 text-amber-300 border border-amber-800 flex items-center gap-1.5 min-w-0 truncate max-w-full';
      els.duplicatesVerificationBadge.innerHTML = '<span class="w-2 h-2 rounded-full bg-amber-400 shrink-0"></span> <span class="truncate">⚠️ Hashes Differ <span class="hidden sm:inline">(Not Exact Match)</span></span>';
    }
    return;
  }

  els.duplicatesVerificationBadge.className = 'text-[11px] font-mono px-2 sm:px-2.5 py-0.5 rounded-lg bg-amber-950 text-amber-300 border border-amber-800/80 flex items-center gap-1.5 min-w-0 truncate max-w-full';
  els.duplicatesVerificationBadge.innerHTML = '<span class="w-2 h-2 border-2 border-amber-400 border-t-transparent rounded-full animate-spin shrink-0"></span> <span class="truncate">Verifying Chunk...</span>';

  const abortCtrl = new AbortController();
  currentVerifyAbort = abortCtrl;

  try {
    const paths = group.files.map(f => f.path);
    const res = await fetch('/api/backup/duplicates/verify-group', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths }),
      signal: abortCtrl.signal,
    });
    if (!res.ok) throw new Error('Verification failed');
    const data = await res.json();

    if (abortCtrl.signal.aborted) return;

    // Cache returned hashes in local file objects
    if (data.hashes) {
      group.files.forEach(f => {
        if (data.hashes[f.path]) f.quick_hash = data.hashes[f.path];
      });
    }

    if (data.all_match) {
      els.duplicatesVerificationBadge.className = 'text-[11px] font-mono px-2 sm:px-2.5 py-0.5 rounded-lg bg-emerald-950 text-emerald-300 border border-emerald-800 flex items-center gap-1.5 min-w-0 truncate max-w-full';
      els.duplicatesVerificationBadge.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-400 shrink-0"></span> <span class="truncate">✓ 100% Content Hash Verified</span>';

      if (state.duplicates.autoDeleteOnMatch && !isResolvingDuplicateSet) {
        const keepIdx = (typeof group.suggested_keep_index === 'number') ? group.suggested_keep_index : 0;
        setTimeout(() => {
          if (state.duplicates.autoDeleteOnMatch && !isResolvingDuplicateSet && state.duplicates.groups[state.duplicates.currentIndex] === group) {
            resolveDuplicateSet(keepIdx);
          }
        }, 350);
      }
    } else {
      els.duplicatesVerificationBadge.className = 'text-[11px] font-mono px-2 sm:px-2.5 py-0.5 rounded-lg bg-amber-950 text-amber-300 border border-amber-800 flex items-center gap-1.5 min-w-0 truncate max-w-full';
      els.duplicatesVerificationBadge.innerHTML = '<span class="w-2 h-2 rounded-full bg-amber-400 shrink-0"></span> <span class="truncate">⚠️ Hashes Differ <span class="hidden sm:inline">(Not Exact Match)</span></span>';
    }
  } catch (e) {
    if (e.name === 'AbortError') return;
    els.duplicatesVerificationBadge.className = 'text-[11px] font-mono px-2 sm:px-2.5 py-0.5 rounded-lg bg-slate-800 text-slate-400 flex items-center gap-1.5 border border-slate-700 min-w-0 truncate max-w-full';
    els.duplicatesVerificationBadge.innerHTML = '<span class="w-2 h-2 rounded-full bg-slate-500 shrink-0"></span> <span class="truncate">Size Matched</span>';
  } finally {
    if (currentVerifyAbort === abortCtrl) {
      currentVerifyAbort = null;
    }
  }
}
let isResolvingDuplicateSet = false;
async function resolveDuplicateSet(target, btnEl = null) {
  if (isResolvingDuplicateSet) return;
  const group = state.duplicates.groups[state.duplicates.currentIndex];
  if (!group) return;

  let keepPath = '';
  let keepFile = null;
  if (typeof target === 'number') {
    keepFile = group.files[target];
    if (!keepFile) return;
    keepPath = keepFile.path;
  } else if (typeof target === 'string') {
    try {
      keepPath = decodeURIComponent(target);
    } catch (e) {
      keepPath = target;
    }
    keepFile = group.files.find(f => f.path === keepPath);
  }
  if (!keepPath) return;

  const deletePaths = group.files.map(f => f.path).filter(p => p !== keepPath);
  if (deletePaths.length === 0) {
    showToast('No other duplicate copies to remove in this set', 'info');
    return;
  }

  const keepName = keepFile ? keepFile.name : 'selected file';

  let originalBtnHtml = '';
  if (btnEl) {
    originalBtnHtml = btnEl.innerHTML;
    btnEl.disabled = true;
    btnEl.innerHTML = '<span class="inline-block animate-spin">⏳</span> Deleting other copies...';
  }

  isResolvingDuplicateSet = true;
  try {
    const res = await fetch('/api/backup/duplicates/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keep_path: keepPath, delete_paths: deletePaths })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to resolve duplicate set');

    if (data.failed && data.failed.length > 0) {
      showToast(`Kept "${keepName}", but failed to delete ${data.failed.length} copy`, 'warning');
    } else {
      const removedCount = (data.deleted && data.deleted.length) ? data.deleted.length : deletePaths.length;
      showToast(`Kept "${keepName}", removed ${removedCount} duplicate copy`, 'success');
    }

    // Sync state: remove deleted files from state.allFiles if gallery is loaded
    if (Array.isArray(state.allFiles)) {
      state.allFiles = state.allFiles.filter(f => !deletePaths.includes(f.path));
    }

    state.duplicates.groups.splice(state.duplicates.currentIndex, 1);
    state.duplicates.totalGroups = Math.max(0, state.duplicates.totalGroups - 1);
    if (els.duplicatesHeaderBadge) {
      if (state.duplicates.totalGroups > 0) {
        els.duplicatesHeaderBadge.textContent = state.duplicates.totalGroups;
        els.duplicatesHeaderBadge.classList.remove('hidden');
      } else {
        els.duplicatesHeaderBadge.classList.add('hidden');
      }
    }
    if (els.duplicatesTotalSets) {
      els.duplicatesTotalSets.textContent = `${state.duplicates.totalGroups} duplicate sets`;
    }
    renderCurrentDuplicateGroup();
  } catch (err) {
    console.error('Error resolving duplicate set:', err);
    showToast(err.message || 'Error resolving duplicates', 'error');
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.innerHTML = originalBtnHtml;
    }
  } finally {
    isResolvingDuplicateSet = false;
  }
}
async function deleteSingleDuplicateFile(target, btnEl = null) {
  if (isResolvingDuplicateSet) return;
  const group = state.duplicates.groups[state.duplicates.currentIndex];
  if (!group) return;

  let targetPath = '';
  let file = null;
  if (typeof target === 'number') {
    file = group.files[target];
    if (!file) return;
    targetPath = file.path;
  } else if (typeof target === 'string') {
    try {
      targetPath = decodeURIComponent(target);
    } catch (e) {
      targetPath = target;
    }
    file = group.files.find(f => f.path === targetPath);
  }
  if (!targetPath) return;

  const fname = file ? file.name : 'file';

  let originalBtnHtml = '';
  if (btnEl) {
    originalBtnHtml = btnEl.innerHTML;
    btnEl.disabled = true;
    btnEl.innerHTML = '<span class="inline-block animate-spin">⏳</span> Deleting...';
  }

  isResolvingDuplicateSet = true;
  try {
    const res = await fetch('/api/backup/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: targetPath })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to delete file');

    showToast(`Deleted "${fname}"`, 'success');

    // Sync state: remove from state.allFiles
    if (Array.isArray(state.allFiles)) {
      state.allFiles = state.allFiles.filter(f => f.path !== targetPath);
    }

    group.files = group.files.filter(f => f.path !== targetPath);
    if (group.files.length <= 1) {
      state.duplicates.groups.splice(state.duplicates.currentIndex, 1);
      state.duplicates.totalGroups = Math.max(0, state.duplicates.totalGroups - 1);
      if (els.duplicatesHeaderBadge) {
        if (state.duplicates.totalGroups > 0) {
          els.duplicatesHeaderBadge.textContent = state.duplicates.totalGroups;
          els.duplicatesHeaderBadge.classList.remove('hidden');
        } else {
          els.duplicatesHeaderBadge.classList.add('hidden');
        }
      }
      if (els.duplicatesTotalSets) {
        els.duplicatesTotalSets.textContent = `${state.duplicates.totalGroups} duplicate sets`;
      }
    }
    renderCurrentDuplicateGroup();
  } catch (err) {
    console.error('Error deleting file:', err);
    showToast(err.message || 'Error deleting file', 'error');
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.innerHTML = originalBtnHtml;
    }
  } finally {
    isResolvingDuplicateSet = false;
  }
}
let isBulkAutoDeleting = false;
async function autoDeleteAllVerified(btnEl = null) {
  if (isBulkAutoDeleting || isResolvingDuplicateSet) return;
  const vCount = state.duplicates.verifiedGroupsCount;
  const vSize = state.duplicates.verifiedReclaimableFormatted;

  if (vCount <= 0) {
    showToast('No 100% hash-verified duplicate sets available to auto-delete', 'info');
    return;
  }

  const confirmMsg = 
    `⚡ Auto-Delete All 100% Hash-Verified Duplicates?\n\n` +
    `• ${vCount} verified duplicate sets will be resolved (~${vSize} reclaimable).\n` +
    `• The single best recommended copy in each set will be safely KEPT.\n` +
    `• Exact duplicate copies will be permanently deleted from Backup.\n` +
    `• Any sets with different hashes (mismatches) will remain untouched for your manual review.\n\n` +
    `Proceed with automatic cleanup?`;

  if (!window.confirm(confirmMsg)) return;

  isBulkAutoDeleting = true;
  let originalHtml = '';
  if (btnEl) {
    originalHtml = btnEl.innerHTML;
    btnEl.disabled = true;
    btnEl.innerHTML = `<span class="inline-block animate-spin shrink-0">⏳</span> <span class="truncate">Auto-deleting ${vCount} sets...</span>`;
  }

  try {
    const res = await fetch('/api/backup/duplicates/autodelete-verified', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to auto-delete verified duplicates');

    showToast(`✓ Auto-cleaned ${data.deleted_count} duplicate files, freed ${data.reclaimed_formatted}!`, 'success', 5000);

    // Sync state: remove deleted paths from state.allFiles if loaded
    if (Array.isArray(state.allFiles) && Array.isArray(data.deleted_paths)) {
      const delSet = new Set(data.deleted_paths);
      state.allFiles = state.allFiles.filter(f => !delSet.has(f.path));
    }

    await loadDuplicateGroups(true);
  } catch (err) {
    console.error('Error auto-deleting verified duplicates:', err);
    showToast(err.message || 'Error running auto-delete', 'error');
  } finally {
    isBulkAutoDeleting = false;
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.innerHTML = originalHtml || '<span>⚡</span><span class="truncate"><span class="sm:hidden">Auto-Delete</span><span class="hidden sm:inline">Auto-Delete All Verified</span></span><span id="autoDeleteBadgeCount" class="px-1.5 py-0.2 rounded-full bg-emerald-950 text-emerald-300 text-[10px] font-mono border border-emerald-700/80 shrink-0">' + state.duplicates.verifiedGroupsCount + '</span>';
    }
  }
}
// ==========================================
// Ignored Duplicates Management
// ==========================================
async function updateIgnoredCountBadge(delta = null) {
  if (typeof delta === 'number') {
    state.duplicates.ignoredCount = Math.max(0, (state.duplicates.ignoredCount || 0) + delta);
    if (els.ignoredDuplicatesCount) els.ignoredDuplicatesCount.textContent = state.duplicates.ignoredCount;
    if (els.ignoredDuplicatesFooterCount) els.ignoredDuplicatesFooterCount.textContent = `${state.duplicates.ignoredCount} ignored pair${state.duplicates.ignoredCount === 1 ? '' : 's'}`;
    return;
  }
  try {
    const res = await fetch('/api/backup/duplicates/ignored?limit=0');
    if (res.ok) {
      const data = await res.json();
      const cnt = data.total || 0;
      state.duplicates.ignoredCount = cnt;
      if (els.ignoredDuplicatesCount) els.ignoredDuplicatesCount.textContent = cnt;
      if (els.ignoredDuplicatesFooterCount) els.ignoredDuplicatesFooterCount.textContent = `${cnt} ignored pair${cnt === 1 ? '' : 's'}`;
    }
  } catch (e) {
    console.error('Error fetching ignored count:', e);
  }
}
let isIgnoringCurrentGroup = false;
async function ignoreCurrentDuplicateMatch(btnEl = null) {
  if (isIgnoringCurrentGroup) return;
  const groups = state.duplicates.groups;
  if (!groups || groups.length === 0) return;
  const currentIdx = state.duplicates.currentIndex;
  const group = groups[currentIdx];
  if (!group || !group.files || group.files.length < 2) return;

  // Calculate pair combinations
  const numFiles = group.files.length;
  const pairsCount = (numFiles * (numFiles - 1)) / 2;
  const paths = group.files.map(f => f.path);
  const matchType = group.match_type || state.duplicates.mode;

  // Optimistic UI update: advance to next match immediately without waiting for network roundtrip
  const [removedGroup] = state.duplicates.groups.splice(currentIdx, 1);
  state.duplicates.totalGroups = Math.max(0, state.duplicates.totalGroups - 1);
  if (state.duplicates.currentIndex >= state.duplicates.groups.length) {
    state.duplicates.currentIndex = Math.max(0, state.duplicates.groups.length - 1);
  }

  if (els.duplicatesHeaderBadge) {
    if (state.duplicates.totalGroups > 0) {
      els.duplicatesHeaderBadge.textContent = state.duplicates.totalGroups;
      els.duplicatesHeaderBadge.classList.remove('hidden');
    } else {
      els.duplicatesHeaderBadge.classList.add('hidden');
    }
  }
  if (els.duplicatesTotalSets) {
    els.duplicatesTotalSets.textContent = `${state.duplicates.totalGroups} duplicate sets`;
  }

  updateIgnoredCountBadge(pairsCount);
  renderCurrentDuplicateGroup();
  showToast('Match ignored. It will not appear in duplicate scans.', 'info', 2000);

  // Send API ignore request asynchronously in background
  isIgnoringCurrentGroup = true;
  try {
    const res = await fetch('/api/backup/duplicates/ignore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        paths: paths,
        match_type: matchType
      })
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.detail || 'Failed to ignore match');
    }
  } catch (err) {
    console.error('Error ignoring match in background:', err);
    showToast('Error ignoring match: ' + err.message, 'error');
    // Rollback optimistic removal
    state.duplicates.groups.splice(currentIdx, 0, removedGroup);
    state.duplicates.totalGroups += 1;
    updateIgnoredCountBadge(-pairsCount);
    renderCurrentDuplicateGroup();
  } finally {
    isIgnoringCurrentGroup = false;
  }
}
function openIgnoredDuplicatesModal() {
  if (els.ignoredDuplicatesModal) {
    els.ignoredDuplicatesModal.classList.remove('hidden');
    loadIgnoredDuplicatesList();
  }
}
function closeIgnoredDuplicatesModal() {
  if (els.ignoredDuplicatesModal) {
    els.ignoredDuplicatesModal.classList.add('hidden');
  }
}
async function loadIgnoredDuplicatesList() {
  if (!els.ignoredDuplicatesList) return;
  els.ignoredDuplicatesList.innerHTML = `
    <div class="py-12 flex flex-col items-center justify-center gap-2 text-slate-400">
      <div class="w-6 h-6 border-2 border-amber-500 border-t-transparent rounded-full animate-spin"></div>
      <span class="text-xs">Loading ignored matches...</span>
    </div>
  `;

  try {
    const res = await fetch('/api/backup/duplicates/ignored?limit=100');
    if (!res.ok) throw new Error('Failed to load ignored matches');
    const data = await res.json();
    const items = data.items || [];
    const total = data.total || 0;

    if (els.ignoredDuplicatesFooterCount) {
      els.ignoredDuplicatesFooterCount.textContent = `${total} ignored pair${total === 1 ? '' : 's'}`;
    }
    if (els.ignoredDuplicatesCount) {
      els.ignoredDuplicatesCount.textContent = total;
    }

    if (items.length === 0) {
      els.ignoredDuplicatesList.innerHTML = `
        <div class="py-12 flex flex-col items-center justify-center gap-2 text-center text-slate-400">
          <div class="w-12 h-12 rounded-xl bg-slate-800 flex items-center justify-center text-2xl text-slate-500">
            <i class="fa-solid fa-ban"></i>
          </div>
          <p class="text-sm font-semibold text-slate-300">No ignored matches</p>
          <p class="text-xs text-slate-500 max-w-sm">When you click "Ignore Match" on any duplicate pair, it will appear here so you can review or un-ignore it at any time.</p>
        </div>
      `;
      return;
    }

    els.ignoredDuplicatesList.innerHTML = items.map(it => `
      <div class="bg-slate-950/80 border border-slate-800/90 rounded-xl p-3 flex flex-col sm:flex-row items-center justify-between gap-3 shadow-md">
        <!-- Pair preview -->
        <div class="flex items-center gap-3 min-w-0 flex-1 w-full">
          <!-- File 1 -->
          <div class="flex items-center gap-2 min-w-0 flex-1">
            <img src="${it.thumb1}" onerror="this.src='/static/icons/video.svg'" class="w-12 h-8 rounded-lg object-cover bg-black border border-slate-800 shrink-0" />
            <div class="min-w-0">
              <p class="text-xs font-semibold text-slate-200 truncate" title="${escapeHtml(it.name1)}">${escapeHtml(it.name1)}</p>
              <p class="text-[10px] text-slate-500 font-mono">${escapeHtml(it.size1 || '')}</p>
            </div>
          </div>

          <!-- Match icon -->
          <div class="shrink-0 flex flex-col items-center px-1">
            <span class="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono uppercase">${escapeHtml(it.match_type || 'match')}</span>
            <span class="text-xs text-amber-500 mt-0.5"><i class="fa-solid fa-ban"></i></span>
          </div>

          <!-- File 2 -->
          <div class="flex items-center gap-2 min-w-0 flex-1">
            <img src="${it.thumb2}" onerror="this.src='/static/icons/video.svg'" class="w-12 h-8 rounded-lg object-cover bg-black border border-slate-800 shrink-0" />
            <div class="min-w-0">
              <p class="text-xs font-semibold text-slate-200 truncate" title="${escapeHtml(it.name2)}">${escapeHtml(it.name2)}</p>
              <p class="text-[10px] text-slate-500 font-mono">${escapeHtml(it.size2 || '')}</p>
            </div>
          </div>
        </div>

        <!-- Un-ignore Action Button -->
        <div class="shrink-0 w-full sm:w-auto flex justify-end">
          <button 
            onclick="window.libraryApp.unignoreDuplicatePair(${it.id}, this)" 
            class="w-full sm:w-auto px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 hover:text-white text-slate-300 text-xs font-medium transition cursor-pointer flex items-center justify-center gap-1.5"
            title="Remove from ignored list so these files can be detected again"
          >
            <span>↺</span> Un-ignore
          </button>
        </div>
      </div>
    `).join('');
  } catch (err) {
    els.ignoredDuplicatesList.innerHTML = `
      <div class="py-8 text-center text-xs text-rose-400">
        Failed to load ignored list: ${err.message}
      </div>
    `;
  }
}
async function unignoreDuplicatePair(id, btnEl = null) {
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.innerHTML = '<span class="inline-block animate-spin">⏳</span>';
  }
  try {
    const res = await fetch('/api/backup/duplicates/unignore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id })
    });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.detail || 'Failed to un-ignore match');

    showToast('Match un-ignored. It can now be scanned as a duplicate.', 'info');
    loadIgnoredDuplicatesList();
    updateIgnoredCountBadge();
  } catch (err) {
    showToast('Error un-ignoring: ' + err.message, 'error');
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.innerHTML = '↺ Un-ignore';
    }
  }
}

export { autoDeleteAllVerified, closeDuplicatesModal, closeIgnoredDuplicatesModal, currentVerifyAbort, deleteSingleDuplicateFile, fetchDuplicateSummary, ignoreCurrentDuplicateMatch, isBulkAutoDeleting, isIgnoringCurrentGroup, isResolvingDuplicateSet, loadDuplicateGroups, loadIgnoredDuplicatesList, openDuplicatesModal, openIgnoredDuplicatesModal, renderCurrentDuplicateGroup, resolveDuplicateSet, setDuplicatesMode, unignoreDuplicatePair, updateIgnoredCountBadge, verifyCurrentGroupHashes };
