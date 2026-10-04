/**
 * Backup v2 Library & Media Viewer
 * Powered by Fancybox 5 & Tailwind CSS
 * Includes Auto-MP4 Background Transcoder & Real-Time In-Place File Updates
 */

(function () {
  'use strict';

  const state = {
    basePath: '',
    currentPath: '',
    rootFolders: [],
    subfolders: [],
    breadcrumbs: [],
    allFiles: [],
    filteredFiles: [],
    displayedCount: 50,
    activeFilter: 'all', // 'all' | 'video' | 'image'
    activeSort: 'name_asc',
    searchScope: 'current', // 'current' | 'global'
    searchQuery: '',
    folderSearchQuery: '',
    itemToRename: null,
    itemToDelete: null,
    observer: null,
    fancyboxInstance: null,
    transcodePollTimer: null,
    latestTranscodeData: null,
    indexPollTimer: null,
    thumbPollTimer: null,
    latestThumbData: null,
  };

  // DOM Elements
  const els = {
    headerPathSubtitle: document.getElementById('headerPathSubtitle'),
    folderCountBadge: document.getElementById('folderCountBadge'),
    folderSearchInput: document.getElementById('folderSearchInput'),
    rootFolderList: document.getElementById('rootFolderList'),
    toggleSidebarBtn: document.getElementById('toggleSidebarBtn'),
    sidebarDrawer: document.getElementById('sidebarDrawer'),
    sidebarBackdrop: document.getElementById('sidebarBackdrop'),
    breadcrumbNav: document.getElementById('breadcrumbNav'),
    refreshFolderBtn: document.getElementById('refreshFolderBtn'),
    subfoldersContainer: document.getElementById('subfoldersContainer'),
    subfolderChips: document.getElementById('subfolderChips'),
    subfolderCountBadge: document.getElementById('subfolderCountBadge'),
    filterAllBtn: document.getElementById('filterAllBtn'),
    filterVideosBtn: document.getElementById('filterVideosBtn'),
    filterImagesBtn: document.getElementById('filterImagesBtn'),
    convertAllNonMp4Btn: document.getElementById('convertAllNonMp4Btn'),
    convertBtnText: document.getElementById('convertBtnText'),
    generateFolderThumbsBtn: document.getElementById('generateFolderThumbsBtn'),
    thumbBtnText: document.getElementById('thumbBtnText'),
    countAllBadge: document.getElementById('countAllBadge'),
    countVideosBadge: document.getElementById('countVideosBadge'),
    countImagesBadge: document.getElementById('countImagesBadge'),
    // Search Scope Toggle
    scopeCurrentBtn: document.getElementById('scopeCurrentBtn'),
    scopeGlobalBtn: document.getElementById('scopeGlobalBtn'),
    fileSearchInput: document.getElementById('fileSearchInput'),
    clearFileSearchBtn: document.getElementById('clearFileSearchBtn'),
    sortSelect: document.getElementById('sortSelect'),
    mediaGrid: document.getElementById('mediaGrid'),
    infiniteScrollSentinel: document.getElementById('infiniteScrollSentinel'),
    loadingMoreSpinner: document.getElementById('loadingMoreSpinner'),
    emptyState: document.getElementById('emptyState'),
    emptyStateMsg: document.getElementById('emptyStateMsg'),
    toastContainer: document.getElementById('toastContainer'),
    // SQLite Index Status & Sync
    indexStatusPill: document.getElementById('indexStatusPill'),
    indexDot: document.getElementById('indexDot'),
    indexStatusText: document.getElementById('indexStatusText'),
    syncIndexBtn: document.getElementById('syncIndexBtn'),
    syncIcon: document.getElementById('syncIcon'),
    // Background Transcode Monitor
    transferBadgeBtn: document.getElementById('transferBadgeBtn'),
    transferSpinner: document.getElementById('transferSpinner'),
    transferCountText: document.getElementById('transferCountText'),
    transferDropdown: document.getElementById('transferDropdown'),
    transferBackdrop: document.getElementById('transferBackdrop'),
    closeTransferDropdownBtn: document.getElementById('closeTransferDropdownBtn'),
    transcodeSummaryText: document.getElementById('transcodeSummaryText'),
    toggleTranscodePauseBtn: document.getElementById('toggleTranscodePauseBtn'),
    activeTranscodeBox: document.getElementById('activeTranscodeBox'),
    activeTranscodeFilename: document.getElementById('activeTranscodeFilename'),
    activeTranscodeMethodBadge: document.getElementById('activeTranscodeMethodBadge'),
    activeTranscodePercent: document.getElementById('activeTranscodePercent'),
    activeTranscodeProgressBar: document.getElementById('activeTranscodeProgressBar'),
    activeTranscodeSpeed: document.getElementById('activeTranscodeSpeed'),
    activeTranscodeEta: document.getElementById('activeTranscodeEta'),
    queuedTranscodesContainer: document.getElementById('queuedTranscodesContainer'),
    queuedTranscodesList: document.getElementById('queuedTranscodesList'),
    transcodeHistoryList: document.getElementById('transcodeHistoryList'),
    // Cloud Transfers Elements
    cloudTransfersSection: document.getElementById('cloudTransfersSection'),
    transferDropdownSummary: document.getElementById('transferDropdownSummary'),
    activeTransferBox: document.getElementById('activeTransferBox'),
    activeTransferFilename: document.getElementById('activeTransferFilename'),
    activeTransferPercent: document.getElementById('activeTransferPercent'),
    activeTransferProgressBar: document.getElementById('activeTransferProgressBar'),
    activeTransferTarget: document.getElementById('activeTransferTarget'),
    activeTransferSize: document.getElementById('activeTransferSize'),
    queuedTransfersContainer: document.getElementById('queuedTransfersContainer'),
    queuedTransfersCount: document.getElementById('queuedTransfersCount'),
    queuedTransfersList: document.getElementById('queuedTransfersList'),
    // Thumbnail Generator Controls
    thumbSummaryText: document.getElementById('thumbSummaryText'),
    toggleThumbPauseBtn: document.getElementById('toggleThumbPauseBtn'),
    thumbProgressBox: document.getElementById('thumbProgressBox'),
    thumbCurrentFile: document.getElementById('thumbCurrentFile'),
    thumbPercentText: document.getElementById('thumbPercentText'),
    thumbProgressBar: document.getElementById('thumbProgressBar'),
    thumbCountsText: document.getElementById('thumbCountsText'),
    thumbStatusLabel: document.getElementById('thumbStatusLabel'),
    // Rename Modal
    renameModal: document.getElementById('renameModal'),
    newFileNameInput: document.getElementById('newFileNameInput'),
    confirmRenameBtn: document.getElementById('confirmRenameBtn'),
    cancelRenameBtn: document.getElementById('cancelRenameBtn'),
    closeRenameModalBtn: document.getElementById('closeRenameModalBtn'),
    // Delete Modal
    deleteModal: document.getElementById('deleteModal'),
    deleteFileName: document.getElementById('deleteFileName'),
    deleteFileSize: document.getElementById('deleteFileSize'),
    confirmDeleteBtn: document.getElementById('confirmDeleteBtn'),
    cancelDeleteBtn: document.getElementById('cancelDeleteBtn'),
    // Bulk Transcode Controls
    cancelAllTranscodesBtn: document.getElementById('cancelAllTranscodesBtn'),
    queuedTranscodesCount: document.getElementById('queuedTranscodesCount'),
    convertAllLibraryBtn: document.getElementById('convertAllLibraryBtn'),
    convertAllLibraryBtnText: document.getElementById('convertAllLibraryBtnText'),
    convertAllLibraryBadge: document.getElementById('convertAllLibraryBadge'),
    // Convert All Library Modal
    convertAllModal: document.getElementById('convertAllModal'),
    convertAllModalCount: document.getElementById('convertAllModalCount'),
    convertAllModalSize: document.getElementById('convertAllModalSize'),
    convertAllBreakdownChips: document.getElementById('convertAllBreakdownChips'),
    closeConvertAllModalBtn: document.getElementById('closeConvertAllModalBtn'),
    cancelConvertAllModalBtn: document.getElementById('cancelConvertAllModalBtn'),
    confirmConvertAllModalBtn: document.getElementById('confirmConvertAllModalBtn'),
  };

  // Toast Notifications
  function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    const colors = {
      success: 'bg-emerald-900/90 border-emerald-700 text-emerald-200',
      error: 'bg-red-900/90 border-red-700 text-red-200',
      info: 'bg-slate-900/90 border-slate-700 text-slate-200',
    };
    toast.className = `p-3 rounded-xl border text-xs font-medium backdrop-blur-md shadow-2xl flex items-center gap-2 pointer-events-auto transition-all duration-300 transform translate-y-2 opacity-0 ${colors[type] || colors.info}`;
    toast.innerHTML = `<span>${type === 'success' ? '✓' : type === 'error' ? '⚠️' : 'ℹ️'}</span><span class="truncate">${message}</span>`;
    els.toastContainer.appendChild(toast);

    requestAnimationFrame(() => {
      toast.classList.remove('translate-y-2', 'opacity-0');
    });

    setTimeout(() => {
      toast.classList.add('translate-y-2', 'opacity-0');
      setTimeout(() => toast.remove(), 300);
    }, 3500);
  }

  // Mobile Sidebar Drawer
  function setSidebarOpen(open) {
    if (open) {
      els.sidebarDrawer.classList.remove('-translate-x-full');
      els.sidebarBackdrop.classList.remove('hidden');
    } else {
      els.sidebarDrawer.classList.add('-translate-x-full');
      els.sidebarBackdrop.classList.add('hidden');
    }
  }

  // Load Folder Contents
  async function loadFolder(targetPath, pushHistory = true, forceRefresh = false) {
    try {
      els.emptyState.classList.add('hidden');
      els.mediaGrid.innerHTML = `
        <div class="col-span-full py-16 flex flex-col items-center justify-center gap-3 text-slate-400">
          <span class="w-7 h-7 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin"></span>
          <span class="text-xs">Loading media files...</span>
        </div>
      `;

      let url = targetPath 
        ? `/api/backup/browse?path=${encodeURIComponent(targetPath)}` 
        : `/api/backup/browse`;
      if (forceRefresh) {
        url += (url.includes('?') ? '&' : '?') + 'refresh=true';
      }

      const res = await fetch(url);
      if (!res.ok) {
        throw new Error(`Failed to load directory (${res.status})`);
      }

      const data = await res.json();
      state.basePath = data.base_path;
      state.currentPath = data.current_path;
      state.rootFolders = data.root_folders || [];
      state.subfolders = data.subfolders || [];
      state.breadcrumbs = data.breadcrumbs || [];
      state.allFiles = data.files || [];

      // Update subtitle & badges
      els.headerPathSubtitle.textContent = data.current_path;
      els.headerPathSubtitle.title = data.current_path;

      // Update URL query string
      if (pushHistory) {
        const newUrl = data.is_root ? '/library' : `/library?folder=${encodeURIComponent(data.current_path)}`;
        history.pushState({ path: data.current_path }, '', newUrl);
      }

      // Render components
      renderRootFolderList();
      renderBreadcrumbs();
      renderSubfolders();
      applyFiltersAndSort();

      // Trigger immediate transcode check
      pollTranscodeStatus();

      // Close mobile drawer after selecting
      if (window.innerWidth < 768) {
        setSidebarOpen(false);
      }
    } catch (err) {
      console.error('Error loading folder:', err);
      showToast(err.message || 'Error reading folder', 'error');
      els.mediaGrid.innerHTML = '';
      els.emptyState.classList.remove('hidden');
      els.emptyStateMsg.textContent = 'Could not access this folder or directory on pCloud.';
    }
  }

  // Render Root Folders List in Sidebar
  function renderRootFolderList() {
    els.folderCountBadge.textContent = `${state.rootFolders.length} folders`;

    const q = state.folderSearchQuery.toLowerCase().trim();
    const visibleFolders = state.rootFolders.filter(f => f.name.toLowerCase().includes(q));

    if (visibleFolders.length === 0) {
      els.rootFolderList.innerHTML = `<div class="text-center py-6 text-xs text-slate-500">No matching folders</div>`;
      return;
    }

    const currentNorm = state.currentPath.toLowerCase();

    els.rootFolderList.innerHTML = visibleFolders.map(folder => {
      const isSelected = currentNorm === folder.path.toLowerCase() || currentNorm.startsWith(folder.path.toLowerCase() + '/');
      const activeClass = isSelected
        ? 'bg-emerald-600/20 text-emerald-300 font-semibold border-emerald-500/40 shadow-sm'
        : 'text-slate-300 hover:bg-slate-800/80 hover:text-white border-transparent';

      const count = folder.file_count || 0;
      const countBadge = count > 0 
        ? `<span class="text-[10px] font-mono px-1.5 py-0.2 rounded-full bg-slate-800 text-slate-400 group-hover:text-slate-300 border border-slate-700/60 shrink-0">${count}</span>` 
        : '';

      return `
        <button 
          data-path="${encodeURIComponent(folder.path)}"
          class="w-full text-left px-2.5 py-1.5 rounded-lg border text-xs flex items-center justify-between gap-2 transition group ${activeClass}"
        >
          <div class="flex items-center gap-2 min-w-0 flex-1">
            <span class="text-sm shrink-0">📁</span>
            <span class="truncate">${folder.name}</span>
          </div>
          ${countBadge}
        </button>
      `;
    }).join('');

    // Attach click events
    els.rootFolderList.querySelectorAll('button[data-path]').forEach(btn => {
      btn.addEventListener('click', () => {
        const folderPath = decodeURIComponent(btn.dataset.path);
        loadFolder(folderPath);
      });
    });
  }

  // Render Breadcrumb Navigation
  function renderBreadcrumbs() {
    if (!state.breadcrumbs || state.breadcrumbs.length === 0) {
      els.breadcrumbNav.innerHTML = `<span class="text-slate-400 font-mono text-xs">/</span>`;
      return;
    }

    els.breadcrumbNav.innerHTML = state.breadcrumbs.map((crumb, idx) => {
      const isLast = idx === state.breadcrumbs.length - 1;
      const isRoot = idx === 0;
      const icon = isRoot ? '📁' : '';

      if (isLast) {
        return `
          <span class="text-white font-semibold flex items-center gap-1 shrink-0">
            ${icon} <span class="truncate max-w-[200px] sm:max-w-xs">${crumb.name}</span>
          </span>
        `;
      }

      return `
        <button 
          data-crumb-path="${encodeURIComponent(crumb.path)}"
          class="text-slate-400 hover:text-emerald-300 transition flex items-center gap-1 shrink-0 hover:underline"
        >
          ${icon} <span>${crumb.name}</span>
        </button>
        <span class="text-slate-600">/</span>
      `;
    }).join('');

    els.breadcrumbNav.querySelectorAll('button[data-crumb-path]').forEach(btn => {
      btn.addEventListener('click', () => {
        const targetPath = decodeURIComponent(btn.dataset.crumbPath);
        loadFolder(targetPath);
      });
    });
  }

  // Render Subfolders Chips
  function renderSubfolders() {
    if (!state.subfolders || state.subfolders.length === 0) {
      els.subfoldersContainer.classList.add('hidden');
      return;
    }

    els.subfoldersContainer.classList.remove('hidden');
    els.subfolderCountBadge.textContent = state.subfolders.length;

    els.subfolderChips.innerHTML = state.subfolders.map(sub => {
      return `
        <button 
          data-sub-path="${encodeURIComponent(sub.path)}"
          class="px-2.5 py-1 rounded-lg bg-slate-800/80 hover:bg-slate-700 text-slate-200 border border-slate-700 text-xs flex items-center gap-1.5 transition active:scale-95 group shadow-sm"
        >
          <span class="text-xs group-hover:scale-110 transition">📁</span>
          <span class="truncate max-w-[180px]">${sub.name}</span>
        </button>
      `;
    }).join('');

    els.subfolderChips.querySelectorAll('button[data-sub-path]').forEach(btn => {
      btn.addEventListener('click', () => {
        const subPath = decodeURIComponent(btn.dataset.subPath);
        loadFolder(subPath);
      });
    });
  }

  let globalSearchDebounceTimer = null;

  // Global SQLite Index Search
  async function performGlobalSearch() {
    const q = state.searchQuery.trim();
    if (!q) {
      applyFiltersAndSort();
      return;
    }

    els.emptyState.classList.add('hidden');
    els.mediaGrid.innerHTML = `
      <div class="col-span-full py-16 flex flex-col items-center justify-center gap-3 text-slate-400">
        <span class="w-7 h-7 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin"></span>
        <span class="text-xs">Searching all backup folders for "${q}"...</span>
      </div>
    `;

    try {
      const typeParam = state.activeFilter === 'all' ? '' : `&type=${state.activeFilter}`;
      const url = `/api/backup/search?q=${encodeURIComponent(q)}${typeParam}&sort=${state.activeSort}&limit=300`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Search failed (${res.status})`);
      const data = await res.json();

      state.filteredFiles = data.results || [];
      state.displayedCount = 50;

      const videoCount = state.filteredFiles.filter(f => f.type === 'video').length;
      const imageCount = state.filteredFiles.filter(f => f.type === 'image').length;
      els.countAllBadge.textContent = state.filteredFiles.length;
      els.countVideosBadge.textContent = videoCount;
      els.countImagesBadge.textContent = imageCount;

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
      els.scopeCurrentBtn.className = 'px-2 sm:px-2.5 py-1 rounded-md transition bg-emerald-600 text-white font-semibold shadow-sm whitespace-nowrap';
      els.scopeGlobalBtn.className = 'px-2 sm:px-2.5 py-1 rounded-md transition text-slate-400 hover:text-white whitespace-nowrap';
      els.fileSearchInput.placeholder = 'Search filename...';
      applyFiltersAndSort();
    }
  }

  // Poll SQLite index crawler status
  async function pollIndexStatus() {
    try {
      const res = await fetch('/api/backup/index/status');
      if (!res.ok) return;
      const data = await res.json();
      state.indexStatus = data.status;

      if (data.status === 'crawling') {
        if (els.indexDot) els.indexDot.className = 'w-2 h-2 rounded-full bg-amber-400 animate-pulse';
        const scanned = data.scanned_folders || 0;
        const total = data.total_folders || 0;
        if (els.indexStatusText) els.indexStatusText.textContent = `Indexing (${scanned}/${total})...`;
        if (els.syncIcon) els.syncIcon.classList.add('animate-spin');
        clearTimeout(state.indexPollTimer);
        state.indexPollTimer = setTimeout(pollIndexStatus, 1500);
      } else {
        if (els.indexDot) els.indexDot.className = 'w-2 h-2 rounded-full bg-emerald-500 shadow-sm';
        const filesCount = data.total_files || 0;
        if (els.indexStatusText) {
          els.indexStatusText.textContent = filesCount > 0 
            ? `Indexed (${filesCount.toLocaleString()} files)`
            : 'Indexed';
        }
        if (els.syncIcon) els.syncIcon.classList.remove('animate-spin');
        clearTimeout(state.indexPollTimer);
        state.indexPollTimer = setTimeout(pollIndexStatus, 10000);
      }
    } catch (err) {
      clearTimeout(state.indexPollTimer);
      state.indexPollTimer = setTimeout(pollIndexStatus, 15000);
    }
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

  // Apply Type Filter, Sort and Search
  function applyFiltersAndSort() {
    if (state.searchScope === 'global' && state.searchQuery.trim()) {
      performGlobalSearch();
      return;
    }

    const videoCount = state.allFiles.filter(f => f.type === 'video').length;
    const imageCount = state.allFiles.filter(f => f.type === 'image').length;

    els.countAllBadge.textContent = state.allFiles.length;
    els.countVideosBadge.textContent = videoCount;
    els.countImagesBadge.textContent = imageCount;

    // Check for non-MP4 videos to show Convert Button
    const nonMp4Videos = state.allFiles.filter(f => f.type === 'video' && f.needs_transcode);
    if (nonMp4Videos.length > 0) {
      els.convertAllNonMp4Btn.classList.remove('hidden');
      els.convertBtnText.textContent = `Convert ${nonMp4Videos.length} to MP4`;
    } else {
      els.convertAllNonMp4Btn.classList.add('hidden');
    }

    // Check for uncached thumbnails to show Cache Thumbs Button
    if (els.generateFolderThumbsBtn) {
      const uncachedFiles = state.allFiles.filter(f => !f.has_thumb);
      if (uncachedFiles.length > 0 && state.currentPath) {
        els.generateFolderThumbsBtn.classList.remove('hidden');
        els.thumbBtnText.textContent = `Cache ${uncachedFiles.length} Thumbs`;
      } else {
        els.generateFolderThumbsBtn.classList.add('hidden');
      }
    }

    // Filter by type
    let files = state.allFiles;
    if (state.activeFilter === 'video') {
      files = files.filter(f => f.type === 'video');
    } else if (state.activeFilter === 'image') {
      files = files.filter(f => f.type === 'image');
    }

    // Filter by search query
    const q = state.searchQuery.toLowerCase().trim();
    if (q) {
      files = files.filter(f => f.name.toLowerCase().includes(q));
    }

    // Sort
    files = files.slice().sort((a, b) => {
      switch (state.activeSort) {
        case 'name_desc':
          return b.name.localeCompare(a.name, undefined, { numeric: true, sensitivity: 'base' });
        case 'date_desc':
          return (b.mtime || 0) - (a.mtime || 0);
        case 'date_asc':
          return (a.mtime || 0) - (b.mtime || 0);
        case 'size_desc':
          return (b.size || 0) - (a.size || 0);
        case 'size_asc':
          return (a.size || 0) - (b.size || 0);
        case 'name_asc':
        default:
          return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
      }
    });

    state.filteredFiles = files;
    state.displayedCount = 50;

    // Render cards
    els.mediaGrid.innerHTML = '';
    if (state.filteredFiles.length === 0) {
      els.emptyState.classList.remove('hidden');
      els.emptyStateMsg.textContent = state.allFiles.length === 0 
        ? 'No media files found in this folder.' 
        : 'No files match your search filter.';
      els.infiniteScrollSentinel.classList.add('hidden');
    } else {
      els.emptyState.classList.add('hidden');
      els.infiniteScrollSentinel.classList.remove('hidden');
      renderCards(0, state.displayedCount);
      // Re-apply live transcode indicators on rendered cards
      if (state.latestTranscodeData) {
        updateCardsTranscodeStatus(state.latestTranscodeData);
      }
    }
  }

  // Render a Batch of Media Cards
  function renderCards(start, count) {
    const slice = state.filteredFiles.slice(start, start + count);
    if (slice.length === 0) return;

    const fragment = document.createDocumentFragment();

    slice.forEach((file, relativeIdx) => {
      const globalIdx = start + relativeIdx;
      const card = document.createElement('div');
      card.className = 'group relative flex flex-col bg-slate-900 rounded-xl overflow-hidden border border-slate-800 hover:border-slate-600 transition shadow hover:shadow-lg';
      card.dataset.index = globalIdx;
      card.dataset.path = file.path;

      const isVid = file.type === 'video';
      const extLabel = file.ext.toUpperCase().replace('.', '');

      card.innerHTML = `
        <!-- Thumbnail / Player Trigger Area -->
        <div class="relative w-full aspect-square bg-slate-950 overflow-hidden cursor-pointer select-none">
          <img 
            src="${file.thumb_url}" 
            alt="${file.name}" 
            loading="lazy" 
            class="card-thumb w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
            onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'100\\' height=\\'100\\' viewBox=\\'0 0 24 24\\' fill=\\'none\\' stroke=\\'%2364748b\\' stroke-width=\\'2\\'><rect width=\\'18\\' height=\\'18\\' x=\\'3\\' y=\\'3\\' rx=\\'2\\'/><circle cx=\\'8.5\\' cy=\\'8.5\\' r=\\'1.5\\'/><path d=\\'m21 15-5-5L5 21\\'/></svg>';"
          />

          <!-- Video Play Icon & Badge -->
          ${isVid ? `
            <div class="absolute inset-0 flex items-center justify-center bg-black/25 group-hover:bg-black/10 transition">
              <div class="w-8 h-8 sm:w-10 sm:h-10 rounded-full bg-emerald-600/90 text-white flex items-center justify-center shadow-lg group-hover:scale-110 group-active:scale-95 transition">
                <svg class="w-4 h-4 sm:w-5 sm:h-5 ml-0.5 fill-current" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
              </div>
            </div>
            <span class="card-ext-badge absolute top-2 left-2 px-1.5 py-0.5 rounded bg-black/70 backdrop-blur-md text-[9px] font-mono font-bold ${file.needs_transcode ? 'text-amber-400 border-amber-500/30' : 'text-emerald-400 border-emerald-500/30'} border">
              ${extLabel}
            </span>
          ` : `
            <span class="card-ext-badge absolute top-2 left-2 px-1.5 py-0.5 rounded bg-black/70 backdrop-blur-md text-[9px] font-mono font-bold text-slate-300 border border-slate-700">
              ${extLabel}
            </span>
          `}

          <!-- Live Transcode Overlay (shown when active) -->
          <div class="transcode-overlay hidden absolute inset-0 bg-slate-950/85 backdrop-blur-sm flex flex-col items-center justify-center p-3 text-center gap-1.5 z-10">
            <div class="flex items-center gap-1.5 text-emerald-400 text-xs font-semibold">
              <span class="w-3.5 h-3.5 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin"></span>
              <span class="transcode-status-text">Converting...</span>
            </div>
            <div class="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
              <div class="transcode-progress-bar bg-gradient-to-r from-emerald-500 to-teal-400 h-1.5 rounded-full transition-all duration-200" style="width: 0%"></div>
            </div>
            <div class="flex items-center justify-between w-full text-[9px] text-slate-400 font-mono">
              <span class="transcode-percent font-bold text-emerald-300">0%</span>
              <span class="transcode-fps">-- fps</span>
            </div>
          </div>

          <!-- Queued for Transcode Badge -->
          <div class="queued-badge hidden absolute bottom-2 left-2 z-10 px-1.5 py-0.5 rounded bg-amber-500/25 text-amber-300 border border-amber-500/30 text-[9px] font-mono font-semibold flex items-center gap-1 shadow">
            <span>⚙️</span> Queued
          </div>

          <!-- Quick Action Buttons on Hover -->
          <div class="absolute top-2 right-2 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-200 z-20">
            <button 
              data-action="rename" 
              data-idx="${globalIdx}" 
              title="Rename file" 
              class="p-1 rounded-md bg-slate-900/90 hover:bg-slate-800 text-slate-300 hover:text-white border border-slate-700 transition"
            >
              ✏️
            </button>
            <button 
              data-action="delete" 
              data-idx="${globalIdx}" 
              title="Delete file" 
              class="p-1 rounded-md bg-red-900/90 hover:bg-red-800 text-red-200 hover:text-white border border-red-700 transition"
            >
              🗑️
            </button>
          </div>
        </div>

        <!-- Card Footer -->
        <div class="p-1.5 sm:p-2.5 flex flex-col gap-0.5 sm:gap-1 min-w-0 bg-slate-900/90">
          ${file.folder_name ? `
            <button 
              type="button" 
              data-jump-folder="${encodeURIComponent(file.folder_path)}" 
              class="text-[10px] text-emerald-400 hover:text-emerald-300 hover:underline flex items-center gap-1 truncate text-left font-sans font-medium"
              title="Open folder: ${file.rel_folder || file.folder_name}"
            >
              <span class="shrink-0">📁</span>
              <span class="truncate">${file.rel_folder || file.folder_name}</span>
            </button>
          ` : ''}
          <span class="card-filename text-xs text-white font-medium truncate" title="${file.name}">
            ${file.name}
          </span>
          <div class="flex items-center justify-between text-[10px] text-slate-400 font-mono">
            <span class="card-filesize">${file.size_formatted}</span>
            <span class="text-[9px] uppercase px-1 rounded ${isVid ? 'text-emerald-400 bg-emerald-950/60' : 'text-slate-400 bg-slate-800'}">
              ${file.type}
            </span>
          </div>
        </div>
      `;

      // Click card to open Fancybox
      card.querySelector('.aspect-square').addEventListener('click', (e) => {
        if (e.target.closest('button[data-action]')) return;
        openFancybox(globalIdx);
      });

      // Jump to folder button (from global search results)
      const jumpBtn = card.querySelector('button[data-jump-folder]');
      if (jumpBtn) {
        jumpBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          const target = decodeURIComponent(jumpBtn.dataset.jumpFolder);
          setSearchScope('current');
          loadFolder(target);
        });
      }

      // Quick action buttons
      card.querySelectorAll('button[data-action]').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const action = btn.dataset.action;
          const idx = parseInt(btn.dataset.idx, 10);
          const f = state.filteredFiles[idx];
          if (!f) return;

          if (action === 'rename') {
            openRenameModal(f.path, f.name);
          } else if (action === 'delete') {
            openDeleteModal(f.path, f.name, f.size_formatted);
          }
        });
      });

      fragment.appendChild(card);
    });

    els.mediaGrid.appendChild(fragment);
  }

  // Open Fancybox 5 Gallery
  function openFancybox(startIndex) {
    if (!window.Fancybox) {
      showToast('Fancybox gallery library loading...', 'info');
      return;
    }

    const items = state.filteredFiles.map((f, idx) => {
      const isVid = f.type === 'video';
      return {
        src: f.media_url,
        thumb: f.thumb_url,
        type: isVid ? 'html5video' : 'image',
        caption: `
          <div class="flex items-center justify-between gap-4 w-full max-w-4xl mx-auto">
            <div class="flex items-center gap-2 truncate">
              <span class="font-mono text-xs sm:text-sm text-white truncate">${f.name}</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">${f.size_formatted}</span>
            </div>
            <div class="flex items-center gap-1.5 shrink-0">
              <button onclick="window.libraryApp.openRenameModal('${encodeURIComponent(f.path)}', '${encodeURIComponent(f.name)}')" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-xs font-semibold rounded-lg border border-slate-600 text-slate-200 transition">
                ✏️ Rename
              </button>
              <button onclick="window.libraryApp.openDeleteModal('${encodeURIComponent(f.path)}', '${encodeURIComponent(f.name)}', '${f.size_formatted}')" class="px-2.5 py-1 bg-red-600/80 hover:bg-red-600 text-xs font-semibold rounded-lg text-white transition">
                🗑️ Delete
              </button>
            </div>
          </div>
        `,
        customData: {
          path: f.path,
          name: f.name,
          size_formatted: f.size_formatted,
          index: idx,
        }
      };
    });

    Fancybox.show(items, {
      startIndex: startIndex,
      Thumbs: {
        type: 'classic',
      },
      Toolbar: {
        display: {
          left: ['infobar'],
          middle: ['zoomIn', 'zoomOut', 'toggle1to1', 'rotateCCW', 'rotateCW'],
          right: ['slideshow', 'thumbs', 'close'],
        },
      },
      on: {
        init: (fancybox) => {
          state.fancyboxInstance = fancybox;
        },
        destroy: () => {
          state.fancyboxInstance = null;
        }
      }
    });
  }

  // Keyboard Shortcuts Handler
  document.addEventListener('keydown', (e) => {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;

    if (state.fancyboxInstance) {
      const activeSlide = state.fancyboxInstance.getSlide();
      if (!activeSlide || !activeSlide.customData) return;

      const { path, name, size_formatted } = activeSlide.customData;

      if (e.key === 'r' || e.key === 'R') {
        e.preventDefault();
        openRenameModal(path, name);
      } else if (e.key === 'd' || e.key === 'D') {
        e.preventDefault();
        openDeleteModal(path, name, size_formatted);
      }
    }
  });

  // Rename Flow
  function openRenameModal(path, name) {
    state.itemToRename = {
      path: decodeURIComponent(path),
      name: decodeURIComponent(name),
    };
    els.newFileNameInput.value = state.itemToRename.name;
    els.renameModal.classList.remove('hidden');
    setTimeout(() => {
      els.newFileNameInput.focus();
      const lastDot = state.itemToRename.name.lastIndexOf('.');
      if (lastDot > 0) {
        els.newFileNameInput.setSelectionRange(0, lastDot);
      } else {
        els.newFileNameInput.select();
      }
    }, 50);
  }

  function closeRenameModal() {
    els.renameModal.classList.add('hidden');
    state.itemToRename = null;
  }

  async function submitRename() {
    if (!state.itemToRename) return;
    const newName = els.newFileNameInput.value.trim();
    if (!newName) {
      showToast('Filename cannot be empty', 'error');
      return;
    }
    if (newName === state.itemToRename.name) {
      closeRenameModal();
      return;
    }

    try {
      els.confirmRenameBtn.disabled = true;
      els.confirmRenameBtn.textContent = 'Saving...';

      const res = await fetch('/api/backup/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: state.itemToRename.path,
          new_name: newName,
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to rename file');
      }

      showToast(`Renamed to "${newName}"`, 'success');
      closeRenameModal();

      // Update in local arrays
      const file = state.allFiles.find(f => f.path === state.itemToRename.path);
      if (file) {
        file.name = newName;
        file.path = data.new_path;
        file.thumb_url = data.thumb_url;
        file.media_url = data.media_url;
      }

      if (state.fancyboxInstance) {
        Fancybox.close();
      }

      applyFiltersAndSort();
    } catch (err) {
      console.error(err);
      showToast(err.message || 'Error renaming file', 'error');
    } finally {
      els.confirmRenameBtn.disabled = false;
      els.confirmRenameBtn.textContent = 'Save Name';
    }
  }

  // Delete Flow
  function openDeleteModal(path, name, sizeFormatted) {
    state.itemToDelete = {
      path: decodeURIComponent(path),
      name: decodeURIComponent(name),
      size: sizeFormatted,
    };
    els.deleteFileName.textContent = state.itemToDelete.name;
    els.deleteFileSize.textContent = state.itemToDelete.size || '';
    els.deleteModal.classList.remove('hidden');
  }

  function closeDeleteModal() {
    els.deleteModal.classList.add('hidden');
    state.itemToDelete = null;
  }

  async function submitDelete() {
    if (!state.itemToDelete) return;

    try {
      els.confirmDeleteBtn.disabled = true;
      els.confirmDeleteBtn.textContent = 'Deleting...';

      const res = await fetch('/api/backup/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: state.itemToDelete.path,
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to delete file');
      }

      showToast(`Deleted "${state.itemToDelete.name}"`, 'success');
      const deletedPath = state.itemToDelete.path;
      closeDeleteModal();

      // Remove from arrays
      state.allFiles = state.allFiles.filter(f => f.path !== deletedPath);

      if (state.fancyboxInstance) {
        Fancybox.close();
      }

      applyFiltersAndSort();
    } catch (err) {
      console.error(err);
      showToast(err.message || 'Error deleting file', 'error');
    } finally {
      els.confirmDeleteBtn.disabled = false;
      els.confirmDeleteBtn.textContent = 'Delete File';
    }
  }

  // Cancel Transcode Task
  async function cancelTranscode(taskId) {
    try {
      const res = await fetch('/api/transcode/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: taskId }),
      });
      if (res.ok) {
        showToast('Conversion cancelled', 'info');
        pollTranscodeStatus();
      }
    } catch (err) {
      console.error('Error cancelling task:', err);
    }
  }

  // Bulk Library Video Conversion Flow
  async function openConvertAllModal() {
    els.convertAllModal.classList.remove('hidden');
    els.convertAllModalCount.textContent = 'Scanning library...';
    els.convertAllModalSize.textContent = '--';
    els.convertAllBreakdownChips.innerHTML = '<span class="text-[11px] text-slate-500 italic animate-pulse">Analyzing non-MP4 videos...</span>';
    els.confirmConvertAllModalBtn.disabled = true;

    try {
      const res = await fetch('/api/backup/transcode/summary');
      if (!res.ok) throw new Error('Failed to fetch summary');
      const data = await res.json();

      els.convertAllModalCount.textContent = `${data.pending_count} videos`;
      els.convertAllModalSize.textContent = data.total_size_formatted || '0 B';

      const chips = Object.entries(data.breakdown || {}).map(([ext, count]) => `
        <span class="px-2 py-0.5 rounded-md bg-slate-800 border border-slate-700 text-[10px] text-emerald-300 font-mono font-medium">
          ${count} ${ext}
        </span>
      `).join('');

      els.convertAllBreakdownChips.innerHTML = chips || '<span class="text-[11px] text-slate-500 italic">All library videos are already MP4!</span>';

      if (data.pending_count > 0) {
        els.confirmConvertAllModalBtn.disabled = false;
        els.confirmConvertAllModalBtn.innerHTML = `<span>Start Conversion (${data.pending_count})</span>`;
      } else {
        els.confirmConvertAllModalBtn.disabled = true;
        els.confirmConvertAllModalBtn.innerHTML = `<span>No Videos to Convert</span>`;
      }
    } catch (err) {
      console.error('Error fetching transcode summary:', err);
      els.convertAllModalCount.textContent = 'Error loading stats';
      els.convertAllBreakdownChips.innerHTML = '<span class="text-[11px] text-red-400">Failed to load breakdown</span>';
    }
  }

  function closeConvertAllModal() {
    els.convertAllModal.classList.add('hidden');
  }

  async function submitConvertAll() {
    try {
      els.confirmConvertAllModalBtn.disabled = true;
      els.confirmConvertAllModalBtn.innerHTML = `<span>Queueing...</span>`;

      const res = await fetch('/api/backup/transcode/enqueue_all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await res.json();

      if (res.ok && data.success) {
        showToast(`Queued ${data.enqueued_count} library videos for MP4 conversion!`, 'success');
        closeConvertAllModal();
        pollTranscodeStatus();
      } else {
        throw new Error(data.detail || 'Failed to queue all conversions');
      }
    } catch (err) {
      console.error(err);
      showToast(err.message || 'Error queueing bulk conversions', 'error');
    } finally {
      els.confirmConvertAllModalBtn.disabled = false;
      els.confirmConvertAllModalBtn.innerHTML = `<span>Start Conversion</span>`;
    }
  }

  async function cancelAllTranscodes() {
    try {
      if (els.cancelAllTranscodesBtn) els.cancelAllTranscodesBtn.disabled = true;
      const res = await fetch('/api/transcode/cancel_all', { method: 'POST' });
      const data = await res.json();
      if (res.ok && data.success) {
        showToast(`Cancelled ${data.cancelled_count} queued conversions`, 'info');
        pollTranscodeStatus();
      } else {
        throw new Error(data.detail || 'Failed to cancel queued conversions');
      }
    } catch (err) {
      console.error(err);
      showToast('Error cancelling queue', 'error');
    } finally {
      if (els.cancelAllTranscodesBtn) els.cancelAllTranscodesBtn.disabled = false;
    }
  }

  // Poll Transcode Status & Update UI
  async function pollTranscodeStatus() {
    let nextDelay = 3500;
    try {
      const res = await fetch('/api/transcode/status');
      if (res.ok) {
        const data = await res.json();
        state.latestTranscodeData = data;
        renderTranscodeStatus(data);
        updateCardsTranscodeStatus(data);

        const hasPending = data.active || (data.queued && data.queued.length > 0);
        if (hasPending) {
          nextDelay = 1000;
        }
      }
    } catch (err) {
      console.error('Error checking transcode status:', err);
    } finally {
      clearTimeout(state.transcodePollTimer);
      state.transcodePollTimer = setTimeout(pollTranscodeStatus, nextDelay);
    }
  }

  // Shared Header Task Badge Updater
  function updateHeaderTaskBadge() {
    const trData = state.latestTransferData;
    const tData = state.latestTranscodeData;
    const thData = state.latestThumbData;

    const trActive = trData && (trData.active || (trData.queued && trData.queued.length > 0));
    const tActive = tData && (tData.active || (tData.queued && tData.queued.length > 0));
    const thActive = thData && thData.status === 'generating' && (thData.pending_count > 0);

    if (trActive) {
      els.transferBadgeBtn.classList.remove('hidden');
      const active = trData.active;
      const queued = trData.queued || [];
      els.transferCountText.textContent = active 
        ? `Moving ${Math.round(active.percent || 0)}%`
        : `${queued.length} transfer${queued.length > 1 ? 's' : ''}`;
      if (els.transferSpinner) els.transferSpinner.classList.remove('hidden');
    } else if (tActive) {
      els.transferBadgeBtn.classList.remove('hidden');
      const active = tData.active;
      const queued = tData.queued || [];
      els.transferCountText.textContent = active 
        ? `${Math.round(active.percent || 0)}% (${queued.length + 1})`
        : `${queued.length} queued`;
      if (els.transferSpinner) els.transferSpinner.classList.remove('hidden');
    } else if (thActive) {
      els.transferBadgeBtn.classList.remove('hidden');
      els.transferCountText.textContent = `Thumbs ${Math.round(thData.percent || 0)}%`;
      if (els.transferSpinner) els.transferSpinner.classList.remove('hidden');
    } else {
      els.transferBadgeBtn.classList.add('hidden');
      els.transferDropdown.classList.add('hidden');
    }
  }

  // Poll Cloud Transfers Status & Update UI
  async function pollTransfersStatus() {
    let nextDelay = 3500;
    try {
      const res = await fetch('/api/transfers');
      if (res.ok) {
        const data = await res.json();
        state.latestTransferData = data;
        renderTransferStatus(data);
        const hasPending = data.active || (data.queued && data.queued.length > 0);
        if (hasPending) {
          nextDelay = 1500;
        }
      }
    } catch (err) {
      // Silent error
    } finally {
      clearTimeout(state.transferPollTimer);
      state.transferPollTimer = setTimeout(pollTransfersStatus, nextDelay);
    }
  }

  // Render Cloud Transfers Section
  function renderTransferStatus(data) {
    if (!els.cloudTransfersSection) return;
    const active = data.active;
    const queued = data.queued || [];

    updateHeaderTaskBadge();

    if (els.transferDropdownSummary) {
      els.transferDropdownSummary.textContent = active 
        ? `Moving (${Math.round(active.percent || 0)}%)` 
        : (queued.length > 0 ? `${queued.length} queued` : 'Idle');
    }

    if (active) {
      els.activeTransferBox.classList.remove('hidden');
      if (els.activeTransferFilename) els.activeTransferFilename.textContent = active.filename;
      if (els.activeTransferPercent) els.activeTransferPercent.textContent = `${Math.round(active.percent || 0)}%`;
      if (els.activeTransferProgressBar) els.activeTransferProgressBar.style.width = `${active.percent || 0}%`;
      if (els.activeTransferTarget) els.activeTransferTarget.textContent = `→ /${active.destination_folder || ''}`;
      if (els.activeTransferSize) {
        const copiedMb = ((active.bytes_copied || 0) / (1024 * 1024)).toFixed(1);
        const totalMb = ((active.total_bytes || 0) / (1024 * 1024)).toFixed(1);
        els.activeTransferSize.textContent = `${copiedMb} / ${totalMb} MB`;
      }
    } else {
      els.activeTransferBox.classList.add('hidden');
    }

    if (queued.length > 0) {
      els.queuedTransfersContainer.classList.remove('hidden');
      if (els.queuedTransfersCount) els.queuedTransfersCount.textContent = queued.length;
      els.queuedTransfersList.innerHTML = queued.slice(0, 10).map(q => `
        <div class="flex items-center justify-between gap-2 p-1.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
          <span class="truncate text-slate-300 font-mono text-[11px] flex-1 min-w-0">${q.filename}</span>
          <span class="text-[10px] text-emerald-400 font-mono shrink-0">→ /${q.destination_folder}</span>
        </div>
      `).join('') + (queued.length > 10 ? `<div class="text-[10px] text-slate-500 text-center py-1 font-mono font-medium">+${queued.length - 10} more in queue...</div>` : '');
    } else {
      els.queuedTransfersContainer.classList.add('hidden');
      els.queuedTransfersList.innerHTML = '';
      if (els.queuedTransfersCount) els.queuedTransfersCount.textContent = '0';
    }
  }

  // Render Background Tasks Dropdown & Header Badge
  function renderTranscodeStatus(data) {
    const active = data.active;
    const queued = data.queued || [];
    const history = data.history || [];
    const isPaused = data.is_paused;

    updateHeaderTaskBadge();

    els.transcodeSummaryText.textContent = isPaused ? 'Paused' : (active ? 'Converting' : 'Idle');
    els.toggleTranscodePauseBtn.textContent = isPaused ? 'Resume' : 'Pause';
    els.toggleTranscodePauseBtn.className = isPaused 
      ? 'px-2 py-0.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-[10px] font-semibold transition'
      : 'px-2 py-0.5 bg-slate-800 hover:bg-slate-700 text-emerald-300 hover:text-white border border-slate-700 rounded text-[10px] font-semibold transition';

    // Active task box
    if (active) {
      els.activeTranscodeBox.classList.remove('hidden');
      els.activeTranscodeFilename.textContent = active.filename;
      els.activeTranscodeMethodBadge.textContent = active.method === 'remux' ? 'Remux' : 'Encode';
      els.activeTranscodePercent.textContent = `${(active.percent || 0).toFixed(1)}%`;
      els.activeTranscodeProgressBar.style.width = `${active.percent || 0}%`;
      els.activeTranscodeSpeed.textContent = active.fps ? `${active.fps} fps` : '';
      els.activeTranscodeEta.textContent = active.eta ? `ETA: ${active.eta}` : '';
    } else {
      els.activeTranscodeBox.classList.add('hidden');
    }

    // Queued items
    if (queued.length > 0) {
      els.queuedTranscodesContainer.classList.remove('hidden');
      if (els.queuedTranscodesCount) els.queuedTranscodesCount.textContent = queued.length;
      const displayQueue = queued.slice(0, 15);
      const remainingCount = queued.length - displayQueue.length;
      els.queuedTranscodesList.innerHTML = displayQueue.map(q => `
        <div class="flex items-center justify-between gap-2 p-1.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
          <span class="truncate text-slate-300 font-mono text-[11px] flex-1 min-w-0">${q.filename}</span>
          <button onclick="window.libraryApp.cancelTranscode('${q.id}')" class="text-[10px] text-red-400 hover:text-red-300 px-1.5 py-0.5 bg-red-950/40 hover:bg-red-900/60 rounded border border-red-800/50 shrink-0">Cancel</button>
        </div>
      `).join('') + (remainingCount > 0 ? `<div class="text-[10px] text-slate-500 text-center py-1 font-mono font-medium">+${remainingCount} more in queue...</div>` : '');
    } else {
      els.queuedTranscodesContainer.classList.add('hidden');
      els.queuedTranscodesList.innerHTML = '';
      if (els.queuedTranscodesCount) els.queuedTranscodesCount.textContent = '0';
    }

    // Recent history
    if (history.length > 0) {
      els.transcodeHistoryList.innerHTML = history.slice(0, 8).map(h => {
        const isSuccess = h.status === 'completed';
        const icon = isSuccess ? '✓' : '✕';
        const color = isSuccess ? 'text-emerald-400' : 'text-red-400';
        return `
          <div class="flex items-center justify-between gap-2 text-[10px] font-mono py-0.5 border-b border-slate-800/50 last:border-0">
            <span class="truncate text-slate-300 flex-1 min-w-0">${h.filename}</span>
            <span class="${color} shrink-0">${icon} ${h.status}</span>
          </div>
        `;
      }).join('');
    } else {
      els.transcodeHistoryList.innerHTML = `<span class="text-slate-600 italic">No conversions yet</span>`;
    }
  }

  // Poll Thumbnail Status & Update UI
  async function pollThumbnailStatus() {
    let nextDelay = 3500;
    try {
      const res = await fetch('/api/backup/thumbnails/status');
      if (res.ok) {
        const data = await res.json();
        state.latestThumbData = data;
        renderThumbnailStatus(data);

        if (data.status === 'generating') {
          nextDelay = 1200;
        } else if (data.status === 'paused') {
          nextDelay = 5000;
        } else {
          nextDelay = 8000;
        }
      }
    } catch (err) {
      console.error('Error checking thumbnail status:', err);
    } finally {
      clearTimeout(state.thumbPollTimer);
      state.thumbPollTimer = setTimeout(pollThumbnailStatus, nextDelay);
    }
  }

  // Render Thumbnail Generator Status
  function renderThumbnailStatus(data) {
    if (!els.thumbSummaryText) return;
    const isPaused = data.is_paused;
    const isGenerating = data.status === 'generating';
    const isDone = data.status === 'complete' || (data.pending_count === 0 && data.total_files > 0);

    if (isPaused) {
      els.thumbSummaryText.textContent = 'Paused';
      if (els.thumbStatusLabel) els.thumbStatusLabel.textContent = 'Paused';
    } else if (isGenerating) {
      els.thumbSummaryText.textContent = 'Generating';
      if (els.thumbStatusLabel) els.thumbStatusLabel.textContent = 'Active';
    } else if (isDone) {
      els.thumbSummaryText.textContent = 'All Cached';
      if (els.thumbStatusLabel) els.thumbStatusLabel.textContent = 'Complete';
    } else {
      els.thumbSummaryText.textContent = 'Idle';
      if (els.thumbStatusLabel) els.thumbStatusLabel.textContent = 'Idle';
    }

    if (els.toggleThumbPauseBtn) {
      els.toggleThumbPauseBtn.textContent = isPaused ? 'Resume' : 'Pause';
      els.toggleThumbPauseBtn.className = isPaused 
        ? 'px-2 py-0.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-[10px] font-semibold transition'
        : 'px-2 py-0.5 bg-slate-800 hover:bg-slate-700 text-emerald-300 hover:text-white border border-slate-700 rounded text-[10px] font-semibold transition';
    }

    if (els.thumbCurrentFile) {
      if (isGenerating && data.current_file) {
        els.thumbCurrentFile.textContent = data.current_file;
        els.thumbCurrentFile.title = data.current_file;
      } else if (isPaused) {
        els.thumbCurrentFile.textContent = 'Generation paused';
        els.thumbCurrentFile.title = '';
      } else if (isDone) {
        els.thumbCurrentFile.textContent = 'All thumbnails cached on SSD';
        els.thumbCurrentFile.title = '';
      } else {
        els.thumbCurrentFile.textContent = 'Idle';
        els.thumbCurrentFile.title = '';
      }
    }

    const pct = typeof data.percent === 'number' ? data.percent : 0;
    if (els.thumbPercentText) els.thumbPercentText.textContent = `${pct.toFixed(1)}%`;
    if (els.thumbProgressBar) els.thumbProgressBar.style.width = `${pct}%`;
    if (els.thumbCountsText) {
      const cached = (data.cached_count || 0).toLocaleString();
      const total = (data.total_files || 0).toLocaleString();
      els.thumbCountsText.textContent = `${cached} / ${total} cached`;
    }

    updateHeaderTaskBadge();
  }

  // Toggle Thumbnail Pause / Resume
  async function toggleThumbPause() {
    try {
      if (els.toggleThumbPauseBtn) els.toggleThumbPauseBtn.disabled = true;
      const res = await fetch('/api/backup/thumbnails/pause', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        showToast(data.message || (data.is_paused ? 'Thumbnail generation paused' : 'Thumbnail generation resumed'), 'info');
        pollThumbnailStatus();
      }
    } catch (err) {
      console.error('Error toggling thumbnail pause:', err);
      showToast('Failed to toggle thumbnail pause', 'error');
    } finally {
      if (els.toggleThumbPauseBtn) els.toggleThumbPauseBtn.disabled = false;
    }
  }

  // Trigger Thumbnail Generation for Current Folder
  async function triggerFolderThumbnails() {
    try {
      if (els.generateFolderThumbsBtn) {
        els.generateFolderThumbsBtn.disabled = true;
        if (els.thumbBtnText) els.thumbBtnText.textContent = 'Queueing...';
      }
      const res = await fetch('/api/backup/thumbnails/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_path: state.currentPath }),
      });
      if (res.ok) {
        const data = await res.json();
        showToast(data.message || 'Queued thumbnails for current folder', 'success');
        pollThumbnailStatus();
      }
    } catch (err) {
      console.error('Error triggering folder thumbnails:', err);
      showToast('Failed to trigger thumbnail generation', 'error');
    } finally {
      if (els.generateFolderThumbsBtn) {
        els.generateFolderThumbsBtn.disabled = false;
        applyFiltersAndSort();
      }
    }
  }

  // Update Media Cards in Grid with Live Transcode Progress
  function updateCardsTranscodeStatus(data) {
    const active = data.active;
    const queued = data.queued || [];
    const history = data.history || [];

    // Check for completed conversions in history
    history.forEach(item => {
      if (item.status === 'completed' && item.source_path && item.target_path) {
        const file = state.allFiles.find(f => f.path === item.source_path);
        if (file) {
          file.name = item.target_filename;
          file.path = item.target_path;
          file.ext = '.mp4';
          file.needs_transcode = false;
          if (item.target_size) {
            file.size = item.target_size;
            file.size_formatted = item.target_size_formatted;
          }
          file.thumb_url = `/api/backup/thumbnail?path=${encodeURIComponent(item.target_path)}`;
          file.media_url = `/api/backup/media?path=${encodeURIComponent(item.target_path)}`;

          // Update card in DOM
          const card = Array.from(els.mediaGrid.children).find(c => c.dataset.path === item.source_path);
          if (card) {
            card.dataset.path = item.target_path;
            const overlay = card.querySelector('.transcode-overlay');
            if (overlay) overlay.classList.add('hidden');
            const queuedBadge = card.querySelector('.queued-badge');
            if (queuedBadge) queuedBadge.classList.add('hidden');

            const nameEl = card.querySelector('.card-filename');
            if (nameEl) {
              nameEl.textContent = item.target_filename;
              nameEl.title = item.target_filename;
            }
            const sizeEl = card.querySelector('.card-filesize');
            if (sizeEl && item.target_size_formatted) {
              sizeEl.textContent = item.target_size_formatted;
            }
            const badgeEl = card.querySelector('.card-ext-badge');
            if (badgeEl) {
              badgeEl.textContent = 'MP4';
              badgeEl.className = 'card-ext-badge absolute top-2 left-2 px-1.5 py-0.5 rounded bg-black/70 backdrop-blur-md text-[9px] font-mono font-bold text-emerald-400 border border-emerald-500/30';
            }
            const img = card.querySelector('img');
            if (img) {
              img.src = file.thumb_url;
            }
          }
        }
      }
    });

    // Update active vs queued indicators on cards
    Array.from(els.mediaGrid.children).forEach(card => {
      const cpath = card.dataset.path;
      if (!cpath) return;

      const overlay = card.querySelector('.transcode-overlay');
      const queuedBadge = card.querySelector('.queued-badge');

      if (active && (active.source_path === cpath || active.filename === card.querySelector('.card-filename')?.textContent)) {
        if (overlay) {
          overlay.classList.remove('hidden');
          overlay.querySelector('.transcode-progress-bar').style.width = `${active.percent || 0}%`;
          overlay.querySelector('.transcode-percent').textContent = `${(active.percent || 0).toFixed(1)}%`;
          overlay.querySelector('.transcode-fps').textContent = active.fps ? `${active.fps} fps` : '';
          overlay.querySelector('.transcode-status-text').textContent = active.method === 'remux' ? 'Remuxing...' : 'Converting...';
        }
        if (queuedBadge) queuedBadge.classList.add('hidden');
      } else {
        const isQueued = queued.some(q => q.source_path === cpath || q.filename === card.querySelector('.card-filename')?.textContent);
        if (isQueued) {
          if (overlay) overlay.classList.add('hidden');
          if (queuedBadge) queuedBadge.classList.remove('hidden');
        } else {
          if (overlay) overlay.classList.add('hidden');
          if (queuedBadge) queuedBadge.classList.add('hidden');
        }
      }
    });
  }

  // Infinite Scroll Observer Setup
  function setupInfiniteScroll() {
    if (state.observer) state.observer.disconnect();

    state.observer = new IntersectionObserver((entries) => {
      const first = entries[0];
      if (first.isIntersecting && state.displayedCount < state.filteredFiles.length) {
        els.loadingMoreSpinner.classList.remove('hidden');
        setTimeout(() => {
          const nextBatch = 50;
          renderCards(state.displayedCount, nextBatch);
          state.displayedCount += nextBatch;
          els.loadingMoreSpinner.classList.add('hidden');
          if (state.latestTranscodeData) {
            updateCardsTranscodeStatus(state.latestTranscodeData);
          }
        }, 100);
      }
    }, { rootMargin: '300px' });

    state.observer.observe(els.infiniteScrollSentinel);
  }

  // Event Listeners Initialization
  function initEvents() {
    // Mobile Drawer Toggle
    els.toggleSidebarBtn.addEventListener('click', () => setSidebarOpen(true));
    els.sidebarBackdrop.addEventListener('click', () => setSidebarOpen(false));

    // Tasks dropdown toggle
    const setTransferDropdownOpen = (open) => {
      if (open) {
        els.transferDropdown.classList.remove('hidden');
        if (els.transferBackdrop) els.transferBackdrop.classList.remove('hidden');
      } else {
        els.transferDropdown.classList.add('hidden');
        if (els.transferBackdrop) els.transferBackdrop.classList.add('hidden');
      }
    };

    els.transferBadgeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isClosed = els.transferDropdown.classList.contains('hidden');
      setTransferDropdownOpen(isClosed);
    });

    if (els.closeTransferDropdownBtn) {
      els.closeTransferDropdownBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        setTransferDropdownOpen(false);
      });
    }

    if (els.transferBackdrop) {
      els.transferBackdrop.addEventListener('click', () => {
        setTransferDropdownOpen(false);
      });
    }

    document.addEventListener('click', (e) => {
      if (!e.target.closest('#transferDropdown') && !e.target.closest('#transferBadgeBtn')) {
        setTransferDropdownOpen(false);
      }
    });

    // Pause / Resume Transcode
    els.toggleTranscodePauseBtn.addEventListener('click', async () => {
      try {
        const res = await fetch('/api/transcode/pause', { method: 'POST' });
        if (res.ok) {
          pollTranscodeStatus();
        }
      } catch (err) {
        console.error('Error toggling pause:', err);
      }
    });

    // Pause / Resume Thumbnail Generator
    if (els.toggleThumbPauseBtn) {
      els.toggleThumbPauseBtn.addEventListener('click', toggleThumbPause);
    }

    // Cache Current Folder Thumbnails Button
    if (els.generateFolderThumbsBtn) {
      els.generateFolderThumbsBtn.addEventListener('click', triggerFolderThumbnails);
    }

    // Search Scope Toggle
    if (els.scopeCurrentBtn) {
      els.scopeCurrentBtn.addEventListener('click', () => setSearchScope('current'));
    }
    if (els.scopeGlobalBtn) {
      els.scopeGlobalBtn.addEventListener('click', () => setSearchScope('global'));
    }

    // SQLite Index Sync Button
    if (els.syncIndexBtn) {
      els.syncIndexBtn.addEventListener('click', triggerIndexSync);
    }

    // Refresh Button (force re-scans & updates folder index)
    els.refreshFolderBtn.addEventListener('click', () => {
      loadFolder(state.currentPath, false, true);
      showToast('Re-indexing folder from pCloud...', 'info');
    });

    // Convert Non-MP4s Button
    els.convertAllNonMp4Btn.addEventListener('click', async () => {
      try {
        els.convertAllNonMp4Btn.disabled = true;
        els.convertBtnText.textContent = 'Queueing...';
        const res = await fetch('/api/backup/transcode/enqueue', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ folder_path: state.currentPath }),
        });
        const data = await res.json();
        showToast(`Queued ${data.enqueued_count} videos for MP4 conversion`, 'success');
        pollTranscodeStatus();
      } catch (err) {
        showToast('Failed to queue conversion', 'error');
      } finally {
        els.convertAllNonMp4Btn.disabled = false;
        els.convertBtnText.textContent = 'Convert to MP4';
      }
    });

    // Folder Search Filter
    els.folderSearchInput.addEventListener('input', (e) => {
      state.folderSearchQuery = e.target.value;
      renderRootFolderList();
    });

    // File Search Filter
    els.fileSearchInput.addEventListener('input', (e) => {
      state.searchQuery = e.target.value;
      if (state.searchQuery) {
        els.clearFileSearchBtn.classList.remove('hidden');
      } else {
        els.clearFileSearchBtn.classList.add('hidden');
      }
      if (state.searchScope === 'global') {
        clearTimeout(globalSearchDebounceTimer);
        globalSearchDebounceTimer = setTimeout(() => {
          performGlobalSearch();
        }, 250);
      } else {
        applyFiltersAndSort();
      }
    });

    els.clearFileSearchBtn.addEventListener('click', () => {
      els.fileSearchInput.value = '';
      state.searchQuery = '';
      els.clearFileSearchBtn.classList.add('hidden');
      if (state.searchScope === 'global') {
        performGlobalSearch();
      } else {
        applyFiltersAndSort();
      }
    });

    // Sort Dropdown
    els.sortSelect.addEventListener('change', (e) => {
      state.activeSort = e.target.value;
      applyFiltersAndSort();
    });

    // Filter Buttons (All, Videos, Images)
    const setFilter = (type) => {
      state.activeFilter = type;
      [els.filterAllBtn, els.filterVideosBtn, els.filterImagesBtn].forEach(btn => {
        btn.className = 'px-2.5 sm:px-3 py-0.5 sm:py-1 rounded-md font-medium text-slate-400 hover:text-white transition whitespace-nowrap';
      });

      if (type === 'all') {
        els.filterAllBtn.className = 'px-2.5 sm:px-3 py-0.5 sm:py-1 rounded-md font-medium transition bg-emerald-600 text-white shadow-sm whitespace-nowrap';
      } else if (type === 'video') {
        els.filterVideosBtn.className = 'px-2.5 sm:px-3 py-0.5 sm:py-1 rounded-md font-medium transition bg-emerald-600 text-white shadow-sm whitespace-nowrap';
      } else if (type === 'image') {
        els.filterImagesBtn.className = 'px-2.5 sm:px-3 py-0.5 sm:py-1 rounded-md font-medium transition bg-emerald-600 text-white shadow-sm whitespace-nowrap';
      }

      applyFiltersAndSort();
    };

    els.filterAllBtn.addEventListener('click', () => setFilter('all'));
    els.filterVideosBtn.addEventListener('click', () => setFilter('video'));
    els.filterImagesBtn.addEventListener('click', () => setFilter('image'));

    // Rename Modal Actions
    els.confirmRenameBtn.addEventListener('click', submitRename);
    els.cancelRenameBtn.addEventListener('click', closeRenameModal);
    els.closeRenameModalBtn.addEventListener('click', closeRenameModal);
    els.newFileNameInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') submitRename();
      if (e.key === 'Escape') closeRenameModal();
    });

    // Delete Modal Actions
    els.confirmDeleteBtn.addEventListener('click', submitDelete);
    els.cancelDeleteBtn.addEventListener('click', closeDeleteModal);

    // Convert All Library Modal & Queue Actions
    if (els.convertAllLibraryBtn) els.convertAllLibraryBtn.addEventListener('click', openConvertAllModal);
    if (els.closeConvertAllModalBtn) els.closeConvertAllModalBtn.addEventListener('click', closeConvertAllModal);
    if (els.cancelConvertAllModalBtn) els.cancelConvertAllModalBtn.addEventListener('click', closeConvertAllModal);
    if (els.confirmConvertAllModalBtn) els.confirmConvertAllModalBtn.addEventListener('click', submitConvertAll);
    if (els.cancelAllTranscodesBtn) els.cancelAllTranscodesBtn.addEventListener('click', cancelAllTranscodes);

    // Browser Back / Forward Handling
    window.addEventListener('popstate', (e) => {
      const folderParam = new URLSearchParams(window.location.search).get('folder');
      loadFolder(folderParam || '', false);
    });
  }

  // App Initialization
  async function init() {
    initEvents();
    setupInfiniteScroll();

    // Check if URL has ?folder=...
    const urlParams = new URLSearchParams(window.location.search);
    const initialFolder = urlParams.get('folder');

    await loadFolder(initialFolder || '', false);
    pollTransfersStatus();
    pollTranscodeStatus();
    pollIndexStatus();
    pollThumbnailStatus();
  }

  // Expose callbacks globally
  window.libraryApp = {
    openRenameModal,
    openDeleteModal,
    cancelTranscode,
    openConvertAllModal,
    cancelAllTranscodes,
  };

  // Start app when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
