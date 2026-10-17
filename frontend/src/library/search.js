import { els, state } from './state.js';
import { showToast } from './utils.js';
import { applyFiltersAndSort, renderCards } from './gallery.js';
import { updateSelectAllBtn } from './management.js';
import { pollSystemStatus, updateHeaderTaskBadge } from './tasks.js';

// Global index search and crawler/index status controls.
// Extracted from the former monolithic library index.js.

let globalSearchDebounceTimer = null;
// Global SQLite Index Search
async function performGlobalSearch() {
  const q = state.searchQuery.trim();
  if (!q) {
    state.searchTruncated = false;
    if (els.searchTruncationBanner) els.searchTruncationBanner.classList.add('hidden');
    applyFiltersAndSort();
    return;
  }

  els.emptyState.classList.add('hidden');
  if (els.searchTruncationBanner) els.searchTruncationBanner.classList.add('hidden');
  els.mediaGrid.innerHTML = `
    <div class="w-full py-16 flex flex-col items-center justify-center gap-3 text-slate-400">
      <span class="w-7 h-7 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin"></span>
      <span class="text-xs">Searching all backup folders for "${q}"...</span>
    </div>
  `;

  try {
    const typeParam = state.activeFilter === 'all' ? '' : `&type=${state.activeFilter}`;
    const url = `/api/backup/search?q=${encodeURIComponent(q)}${typeParam}&sort=${state.activeSort}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Search failed (${res.status})`);
    const data = await res.json();

    state.filteredFiles = data.results || [];
    state.searchTruncated = Boolean(data.truncated);
    state.searchLimit = data.limit || 1000;
    state.displayedCount = 50;

    // `person:name` filters the search to a named face cluster; an unknown name
    // returns nothing, which the empty state has to explain (otherwise it looks
    // like a broken search).
    const person = data.person || null;
    if (person && !person.matched) {
      const terms = (person.terms || []).join(', ');
      els.emptyState.classList.remove('hidden');
      els.emptyStateMsg.textContent = `No person called "${terms}". Name a cluster on the People page first, then search person:${terms}.`;
      els.mediaGrid.innerHTML = '';
      els.infiniteScrollSentinel.classList.add('hidden');
      if (els.countAllBadge) els.countAllBadge.textContent = '0';
      if (els.mobileCountAllBadge) els.mobileCountAllBadge.textContent = '0';
      updateSelectAllBtn();
      return;
    }
    if (person && person.matched && (person.people || []).length) {
      showToast(`Filtering to ${person.people.map(p => p.name).join(', ')}`, 'info');
    }

    const videoCount = state.filteredFiles.filter(f => f.type === 'video').length;
    const imageCount = state.filteredFiles.filter(f => f.type === 'image').length;
    const plusSuffix = state.searchTruncated ? '+' : '';

    els.countAllBadge.textContent = `${state.filteredFiles.length}${plusSuffix}`;
    els.countVideosBadge.textContent = `${videoCount}${plusSuffix && state.filteredFiles.length === videoCount ? plusSuffix : ''}`;
    els.countImagesBadge.textContent = `${imageCount}${plusSuffix && state.filteredFiles.length === imageCount ? plusSuffix : ''}`;
    if (els.mobileCountAllBadge) els.mobileCountAllBadge.textContent = `${state.filteredFiles.length}${plusSuffix}`;
    if (els.mobileCountVideosBadge) els.mobileCountVideosBadge.textContent = `${videoCount}${plusSuffix && state.filteredFiles.length === videoCount ? plusSuffix : ''}`;
    if (els.mobileCountImagesBadge) els.mobileCountImagesBadge.textContent = `${imageCount}${plusSuffix && state.filteredFiles.length === imageCount ? plusSuffix : ''}`;

    if (els.searchTruncationBanner) {
      if (state.searchTruncated && state.filteredFiles.length > 0) {
        if (els.searchTruncationText) {
          els.searchTruncationText.textContent = `Search results capped at ${state.searchLimit.toLocaleString()} matches. Refine your query to see more specific results.`;
        }
        els.searchTruncationBanner.classList.remove('hidden');
      } else {
        els.searchTruncationBanner.classList.add('hidden');
      }
    }

    els.mediaGrid.innerHTML = '';
    if (state.filteredFiles.length === 0) {
      els.emptyState.classList.remove('hidden');
      els.emptyStateMsg.textContent = `No files found matching "${q}" in the backup index.`;
      els.infiniteScrollSentinel.classList.add('hidden');
    } else {
      els.emptyState.classList.remove('hidden');
      els.infiniteScrollSentinel.classList.remove('hidden');
      renderCards(0, state.displayedCount);
    }
    updateSelectAllBtn();
  } catch (err) {
    console.error('Global search error:', err);
    showToast('Search failed: ' + err.message, 'error');
  }
}
// Switch between "This Folder" and "All Folders" search scopes
function setSearchScope(scope) {
  state.searchScope = scope;
  if (scope === 'global') {
    els.scopeGlobalBtn.className = 'px-2 sm:px-2.5 py-1 rounded-md transition bg-emerald-600 text-white font-semibold shadow-sm whitespace-nowrap';
    els.scopeCurrentBtn.className = 'px-2 sm:px-2.5 py-1 rounded-md transition text-slate-400 hover:text-white whitespace-nowrap';
    els.fileSearchInput.placeholder = 'Search all backup folders...';
    if (state.searchQuery.trim()) {
      performGlobalSearch();
    }
  } else {
    state.searchTruncated = false;
    if (els.searchTruncationBanner) els.searchTruncationBanner.classList.add('hidden');
    els.scopeCurrentBtn.className = 'px-2 sm:px-2.5 py-1 rounded-md transition bg-emerald-600 text-white font-semibold shadow-sm whitespace-nowrap';
    els.scopeGlobalBtn.className = 'px-2 sm:px-2.5 py-1 rounded-md transition text-slate-400 hover:text-white whitespace-nowrap';
    els.fileSearchInput.placeholder = 'Search filename...';
    applyFiltersAndSort();
  }
}
// Render Media Library Indexer Status (Header Pill + Dropdown Panel)
function renderIndexStatus(data) {
  if (!data) return;
  const isCrawling = data.status === 'crawling';
  const scanned = data.scanned_folders || 0;
  const total = data.total_folders || 0;
  const filesCount = data.total_files || 0;
  const staleFolders = data.stale_folders || 0;
  const staleFiles = data.stale_files || 0;
  // A stale index is idle + rows pointing at folders that are gone from the
  // mount and whose prune was blocked by the mount-safety guard.
  const isStale = !isCrawling && staleFolders > 0;
  const pct = typeof data.percent === 'number' ? data.percent : (total > 0 ? (scanned / total * 100) : 100);

  // Update Dropdown elements
  if (els.indexSummaryText) {
    if (isCrawling) {
      els.indexSummaryText.textContent = 'Indexing';
      els.indexSummaryText.className = 'text-[10px] text-amber-400 font-mono font-medium animate-pulse';
    } else if (isStale) {
      els.indexSummaryText.textContent = 'Index stale';
      els.indexSummaryText.className = 'text-[10px] text-amber-400 font-mono font-medium';
    } else {
      els.indexSummaryText.textContent = 'Indexed';
      els.indexSummaryText.className = 'text-[10px] text-emerald-400 font-mono';
    }
  }

  if (els.triggerIndexSyncDropdownBtn) {
    els.triggerIndexSyncDropdownBtn.disabled = isCrawling;
    if (isCrawling) {
      els.triggerIndexSyncDropdownBtn.classList.add('opacity-50', 'pointer-events-none');
    } else {
      els.triggerIndexSyncDropdownBtn.classList.remove('opacity-50', 'pointer-events-none');
    }
  }
  if (els.indexSyncDropdownIcon) {
    if (isCrawling) {
      els.indexSyncDropdownIcon.classList.add('animate-spin');
    } else {
      els.indexSyncDropdownIcon.classList.remove('animate-spin');
    }
  }

  if (els.indexCurrentFolder) {
    if (isCrawling && data.current_folder) {
      els.indexCurrentFolder.textContent = `Scanning: ${data.current_folder}`;
      els.indexCurrentFolder.title = data.current_folder;
    } else if (isCrawling) {
      els.indexCurrentFolder.textContent = 'Scanning folders...';
      els.indexCurrentFolder.title = '';
    } else if (isStale) {
      els.indexCurrentFolder.textContent = `${staleFolders.toLocaleString()} folders missing on mount`;
      els.indexCurrentFolder.title = data.prune_reason || '';
    } else {
      els.indexCurrentFolder.textContent = 'All folders indexed';
      els.indexCurrentFolder.title = '';
    }
  }

  if (els.indexStaleWarning) {
    els.indexStaleWarning.classList.toggle('hidden', !isStale);
  }
  if (els.indexStaleText) {
    if (isStale) {
      const fileNote = staleFiles > 0 ? ` (${staleFiles.toLocaleString()} files)` : '';
      els.indexStaleText.textContent = `${staleFolders.toLocaleString()} indexed folders${fileNote} no longer exist on the mount. Prune was blocked by the mount-safety guard${data.prune_reason ? `: ${data.prune_reason}` : ''}.`;
    } else {
      els.indexStaleText.textContent = '';
    }
  }

  if (els.indexPercentText) {
    els.indexPercentText.textContent = `${Math.min(100, Math.max(0, pct)).toFixed(0)}%`;
    els.indexPercentText.className = isStale
      ? 'text-amber-400 font-mono text-[10px] font-bold shrink-0'
      : 'text-emerald-400 font-mono text-[10px] font-bold shrink-0';
  }

  if (els.indexProgressBar) {
    els.indexProgressBar.style.width = `${Math.min(100, Math.max(0, pct))}%`;
    if (isCrawling) {
      els.indexProgressBar.className = 'bg-gradient-to-r from-amber-500 to-emerald-400 h-1.5 rounded-full transition-all duration-200';
    } else if (isStale) {
      els.indexProgressBar.className = 'bg-gradient-to-r from-amber-500 to-amber-400 h-1.5 rounded-full transition-all duration-200';
    } else {
      els.indexProgressBar.className = 'bg-gradient-to-r from-emerald-500 to-teal-400 h-1.5 rounded-full transition-all duration-200';
    }
  }

  if (els.indexCountsText) {
    els.indexCountsText.textContent = `${scanned.toLocaleString()} / ${total.toLocaleString()} folders`;
  }

  if (els.indexFilesText) {
    els.indexFilesText.textContent = `${filesCount.toLocaleString()} files`;
  }

  // Update Header Topbar Pill
  if (isCrawling) {
    if (els.indexDot) els.indexDot.className = 'w-2 h-2 rounded-full bg-amber-400 animate-pulse';
    if (els.indexStatusText) els.indexStatusText.textContent = `Indexing (${scanned}/${total})...`;
    if (els.syncIcon) els.syncIcon.classList.add('animate-spin');
  } else {
    if (els.indexDot) els.indexDot.className = 'w-2 h-2 rounded-full bg-emerald-500 shadow-sm';
    if (els.indexStatusText) {
      els.indexStatusText.textContent = filesCount > 0 
        ? `Indexed (${filesCount.toLocaleString()} files)`
        : 'Indexed';
    }
    if (els.syncIcon) els.syncIcon.classList.remove('animate-spin');
  }

  if (els.mobileIndexDot) {
    els.mobileIndexDot.className = isCrawling 
      ? 'w-3 h-3 rounded-full bg-amber-400 animate-pulse shrink-0' 
      : 'w-3 h-3 rounded-full bg-emerald-500 shadow-sm shrink-0';
  }
  if (els.mobileIndexStatusText && els.indexStatusText) {
    els.mobileIndexStatusText.textContent = els.indexStatusText.textContent;
  }

  updateHeaderTaskBadge();
}
// Poll SQLite index crawler status
function pollIndexStatus() {
  pollSystemStatus(true);
}
// Trigger manual background index sync
async function triggerIndexSync() {
  try {
    if (els.syncIcon) els.syncIcon.classList.add('animate-spin');
    showToast('Starting library index scan...', 'info');
    const res = await fetch('/api/backup/index/sync', { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      showToast('Background crawler started', 'success');
    } else {
      showToast(data.message || 'Crawler already running', 'info');
    }
    pollIndexStatus();
  } catch (err) {
    showToast('Failed to trigger index sync: ' + err.message, 'error');
  }
}
// Drop index rows for folders that no longer exist on the mount. The
// crawler's automatic prune refuses to delete more than ~10% of the index at
// once (transient-mount protection), so a genuinely large remote deletion
// has to be confirmed by hand from the indexer card.
async function forceIndexPrune() {
  if (state.indexPruneRunning) return;
  state.indexPruneRunning = true;
  const btn = els.indexForcePruneBtn;
  const originalLabel = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.classList.add('opacity-60', 'pointer-events-none');
    btn.textContent = 'Pruning...';
  }
  showToast('Removing missing folders from the index...', 'info');
  try {
    const res = await fetch('/api/backup/index/prune', { method: 'POST' });
    const data = await res.json();
    const stats = (data && data.stats) || {};
    if (stats.aborted) {
      showToast(stats.reason || 'Prune was aborted', 'error');
    } else {
      const folders = stats.pruned_folders || 0;
      const files = stats.pruned_files || 0;
      showToast(
        `Pruned ${folders.toLocaleString()} missing folders and ${files.toLocaleString()} file rows`,
        'success'
      );
    }
    await pollSystemStatus(true);
  } catch (err) {
    showToast('Failed to prune index: ' + err.message, 'error');
  } finally {
    state.indexPruneRunning = false;
    if (btn) {
      btn.disabled = false;
      btn.classList.remove('opacity-60', 'pointer-events-none');
      btn.textContent = originalLabel || 'Remove missing folders from index';
    }
  }
}

// Debounced global search used by the desktop search box and mobile sheet.
function debouncedGlobalSearch() {
  clearTimeout(globalSearchDebounceTimer);
  globalSearchDebounceTimer = setTimeout(() => performGlobalSearch(), 250);
}

export { debouncedGlobalSearch, forceIndexPrune, globalSearchDebounceTimer, performGlobalSearch, pollIndexStatus, renderIndexStatus, setSearchScope, triggerIndexSync };
