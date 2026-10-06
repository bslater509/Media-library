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
    dupHashPollTimer: null,
    latestDupHashData: null,
    libBackupPollTimer: null,
    latestLibBackupData: null,
    duplicates: {
      isOpen: false,
      mode: 'content',
      groups: [],
      currentIndex: 0,
      totalGroups: 0,
      reclaimableFormatted: '0 GB',
      verifiedGroupsCount: 0,
      verifiedReclaimableFormatted: '0 B',
      autoDeleteOnMatch: (localStorage.getItem('lib_auto_delete_duplicates') === 'true'),
    },
  };

  // DOM Elements
  const els = {
    // Duplicates Review Elements
    openDuplicatesBtn: document.getElementById('openDuplicatesBtn'),
    duplicatesHeaderBadge: document.getElementById('duplicatesHeaderBadge'),
    duplicatesModal: document.getElementById('duplicatesModal'),
    closeDuplicatesModalBtn: document.getElementById('closeDuplicatesModalBtn'),
    dupModeContentBtn: document.getElementById('dupModeContentBtn'),
    dupModeVisualBtn: document.getElementById('dupModeVisualBtn'),
    openIgnoredDuplicatesBtn: document.getElementById('openIgnoredDuplicatesBtn'),
    ignoredDuplicatesCount: document.getElementById('ignoredDuplicatesCount'),
    ignoreCurrentGroupBtn: document.getElementById('ignoreCurrentGroupBtn'),
    ignoredDuplicatesModal: document.getElementById('ignoredDuplicatesModal'),
    closeIgnoredDuplicatesModalBtn: document.getElementById('closeIgnoredDuplicatesModalBtn'),
    closeIgnoredDuplicatesFooterBtn: document.getElementById('closeIgnoredDuplicatesFooterBtn'),
    ignoredDuplicatesList: document.getElementById('ignoredDuplicatesList'),
    ignoredDuplicatesFooterCount: document.getElementById('ignoredDuplicatesFooterCount'),
    duplicatesGroupPill: document.getElementById('duplicatesGroupPill'),
    duplicatesSubtitle: document.getElementById('duplicatesSubtitle'),
    duplicatesVerificationBadge: document.getElementById('duplicatesVerificationBadge'),
    duplicatesGroupSizeBadge: document.getElementById('duplicatesGroupSizeBadge'),
    duplicateBgHashBadge: document.getElementById('duplicateBgHashBadge'),
    dupBgHashIcon: document.getElementById('dupBgHashIcon'),
    dupBgHashText: document.getElementById('dupBgHashText'),
    dupBgHashPauseBtn: document.getElementById('dupBgHashPauseBtn'),
    autoDeleteAllVerifiedBtn: document.getElementById('autoDeleteAllVerifiedBtn'),
    autoDeleteBadgeCount: document.getElementById('autoDeleteBadgeCount'),
    autoDeleteOnBrowseToggle: document.getElementById('autoDeleteOnBrowseToggle'),
    prevDuplicateGroupBtn: document.getElementById('prevDuplicateGroupBtn'),
    nextDuplicateGroupBtn: document.getElementById('nextDuplicateGroupBtn'),
    duplicateGroupCounter: document.getElementById('duplicateGroupCounter'),
    duplicateCardsContainer: document.getElementById('duplicateCardsContainer'),
    duplicatesTotalReclaimable: document.getElementById('duplicatesTotalReclaimable'),
    duplicatesTotalSets: document.getElementById('duplicatesTotalSets'),
    refreshDuplicatesBtn: document.getElementById('refreshDuplicatesBtn'),

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
    optimizeFolderWebpBtn: document.getElementById('optimizeFolderWebpBtn'),
    webpBtnText: document.getElementById('webpBtnText'),
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
    indexSummaryText: document.getElementById('indexSummaryText'),
    triggerIndexSyncDropdownBtn: document.getElementById('triggerIndexSyncDropdownBtn'),
    indexSyncDropdownIcon: document.getElementById('indexSyncDropdownIcon'),
    indexCurrentFolder: document.getElementById('indexCurrentFolder'),
    indexPercentText: document.getElementById('indexPercentText'),
    indexProgressBar: document.getElementById('indexProgressBar'),
    indexCountsText: document.getElementById('indexCountsText'),
    indexFilesText: document.getElementById('indexFilesText'),
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
    // Duplicate Hash Verifier Controls
    hashSummaryText: document.getElementById('hashSummaryText'),
    toggleHashPauseBtn: document.getElementById('toggleHashPauseBtn'),
    hashProgressBox: document.getElementById('hashProgressBox'),
    hashCurrentFile: document.getElementById('hashCurrentFile'),
    hashPercentText: document.getElementById('hashPercentText'),
    hashProgressBar: document.getElementById('hashProgressBar'),
    hashCountsText: document.getElementById('hashCountsText'),
    hashStatusLabel: document.getElementById('hashStatusLabel'),
    // WebP Optimizer Controls
    webpSummaryText: document.getElementById('webpSummaryText'),
    toggleWebpPauseBtn: document.getElementById('toggleWebpPauseBtn'),
    webpProgressBox: document.getElementById('webpProgressBox'),
    webpCurrentFile: document.getElementById('webpCurrentFile'),
    webpPercentText: document.getElementById('webpPercentText'),
    webpProgressBar: document.getElementById('webpProgressBar'),
    webpCountsText: document.getElementById('webpCountsText'),
    webpSavedText: document.getElementById('webpSavedText'),
    // Library Folder Backup Controls
    libBackupSummaryText: document.getElementById('libBackupSummaryText'),
    triggerLibBackupBtn: document.getElementById('triggerLibBackupBtn'),
    libBackupIcon: document.getElementById('libBackupIcon'),
    triggerLibRestoreBtn: document.getElementById('triggerLibRestoreBtn'),
    libBackupProgressBox: document.getElementById('libBackupProgressBox'),
    libBackupStatusText: document.getElementById('libBackupStatusText'),
    libBackupPercentText: document.getElementById('libBackupPercentText'),
    libBackupProgressBar: document.getElementById('libBackupProgressBar'),
    libBackupCountsText: document.getElementById('libBackupCountsText'),
    libBackupSizeText: document.getElementById('libBackupSizeText'),
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

  function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function safeAttrEscape(str) {
    if (!str) return '';
    return encodeURIComponent(str).replace(/'/g, '%27');
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

  // Render Media Library Indexer Status (Header Pill + Dropdown Panel)
  function renderIndexStatus(data) {
    if (!data) return;
    const isCrawling = data.status === 'crawling';
    const scanned = data.scanned_folders || 0;
    const total = data.total_folders || 0;
    const filesCount = data.total_files || 0;
    const pct = typeof data.percent === 'number' ? data.percent : (total > 0 ? (scanned / total * 100) : 100);

    // Update Dropdown elements
    if (els.indexSummaryText) {
      if (isCrawling) {
        els.indexSummaryText.textContent = 'Indexing';
        els.indexSummaryText.className = 'text-[10px] text-amber-400 font-mono font-medium animate-pulse';
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
      } else {
        els.indexCurrentFolder.textContent = 'All folders indexed';
        els.indexCurrentFolder.title = '';
      }
    }

    if (els.indexPercentText) {
      els.indexPercentText.textContent = `${Math.min(100, Math.max(0, pct)).toFixed(0)}%`;
    }

    if (els.indexProgressBar) {
      els.indexProgressBar.style.width = `${Math.min(100, Math.max(0, pct))}%`;
      if (isCrawling) {
        els.indexProgressBar.className = 'bg-gradient-to-r from-amber-500 to-emerald-400 h-1.5 rounded-full transition-all duration-200';
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

    updateHeaderTaskBadge();
  }

  // Poll SQLite index crawler status
  async function pollIndexStatus() {
    let nextDelay = 8000;
    try {
      const res = await fetch('/api/backup/index/status');
      if (res.ok) {
        const data = await res.json();
        state.indexStatus = data.status;
        state.latestIndexData = data;
        renderIndexStatus(data);

        if (data.status === 'crawling') {
          nextDelay = 1200;
        } else {
          nextDelay = 8000;
        }
      }
    } catch (err) {
      console.error('Error polling index status:', err);
    } finally {
      clearTimeout(state.indexPollTimer);
      state.indexPollTimer = setTimeout(pollIndexStatus, nextDelay);
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

    // Check for unoptimized images to show Optimize WebP Button
    if (els.optimizeFolderWebpBtn) {
      const unoptimizedImages = state.allFiles.filter(f => f.type === 'image' && f.ext !== '.webp');
      if (unoptimizedImages.length > 0 && state.currentPath) {
        els.optimizeFolderWebpBtn.classList.remove('hidden');
        els.webpBtnText.textContent = `Optimize ${unoptimizedImages.length} WebP`;
      } else {
        els.optimizeFolderWebpBtn.classList.add('hidden');
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
              <button onclick="window.libraryApp.openRenameModal('${safeAttrEscape(f.path)}', '${safeAttrEscape(f.name)}')" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-xs font-semibold rounded-lg border border-slate-600 text-slate-200 transition">
                ✏️ Rename
              </button>
              <button onclick="window.libraryApp.openDeleteModal('${safeAttrEscape(f.path)}', '${safeAttrEscape(f.name)}', '${safeAttrEscape(f.size_formatted)}')" class="px-2.5 py-1 bg-red-600/80 hover:bg-red-600 text-xs font-semibold rounded-lg text-white transition">
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

    if (state.duplicates.isOpen) {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeDuplicatesModal();
        return;
      }
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        if (state.duplicates.currentIndex > 0) {
          state.duplicates.currentIndex--;
          renderCurrentDuplicateGroup();
        }
        return;
      }
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        if (state.duplicates.currentIndex < state.duplicates.groups.length - 1) {
          state.duplicates.currentIndex++;
          renderCurrentDuplicateGroup();
        }
        return;
      }
    }

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

  // ==========================================
  // Duplicates Review Flow
  // ==========================================

  function formatDate(mtime) {
    if (!mtime) return '--';
    try {
      const d = new Date(mtime * 1000);
      return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    } catch (e) {
      return '--';
    }
  }

  async function fetchDuplicateSummary() {
    try {
      const res = await fetch('/api/backup/duplicates?mode=content&limit=1');
      if (!res.ok) return;
      const data = await res.json();
      state.duplicates.totalGroups = data.total_groups || 0;
      state.duplicates.reclaimableFormatted = data.reclaimable_formatted || '0 GB';
      if (els.duplicatesHeaderBadge) {
        if (data.total_groups > 0) {
          els.duplicatesHeaderBadge.textContent = data.total_groups;
          els.duplicatesHeaderBadge.classList.remove('hidden');
        } else {
          els.duplicatesHeaderBadge.classList.add('hidden');
        }
      }
    } catch (e) {
      console.error('Error fetching duplicate summary:', e);
    }
  }

  function openDuplicatesModal() {
    state.duplicates.isOpen = true;
    if (els.duplicatesModal) {
      els.duplicatesModal.classList.remove('hidden');
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
    const wData = state.latestWebpData;
    const idxData = state.latestIndexData;
    const bkData = state.latestLibBackupData;

    const trActive = trData && (trData.active || (trData.queued && trData.queued.length > 0));
    const idxActive = idxData && idxData.status === 'crawling';
    const tActive = tData && (tData.active || (tData.queued && tData.queued.length > 0));
    const thActive = thData && thData.status === 'generating' && (thData.pending_count > 0);
    const wActive = wData && wData.status === 'converting' && (wData.pending_count > 0);
    const bkActive = bkData && (bkData.status === 'backing_up' || bkData.status === 'restoring');

    if (trActive) {
      els.transferBadgeBtn.classList.remove('hidden');
      const active = trData.active;
      const queued = trData.queued || [];
      els.transferCountText.textContent = active 
        ? `Moving ${Math.round(active.percent || 0)}%`
        : `${queued.length} transfer${queued.length > 1 ? 's' : ''}`;
      if (els.transferSpinner) els.transferSpinner.classList.remove('hidden');
    } else if (idxActive) {
      els.transferBadgeBtn.classList.remove('hidden');
      const pct = typeof idxData.percent === 'number' ? Math.round(idxData.percent) : 0;
      els.transferCountText.textContent = `Indexing ${pct}%`;
      if (els.transferSpinner) els.transferSpinner.classList.remove('hidden');
    } else if (bkActive) {
      els.transferBadgeBtn.classList.remove('hidden');
      const actionName = bkData.status === 'backing_up' ? 'Backup' : 'Restore';
      const pct = Math.round(bkData.progress_percent || 0);
      els.transferCountText.textContent = `${actionName} ${pct}%`;
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
    } else if (wActive) {
      els.transferBadgeBtn.classList.remove('hidden');
      els.transferCountText.textContent = `WebP ${Math.round(wData.percent || 0)}%`;
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

  // Poll Duplicate Hash Pre-Verification Status
  async function pollDuplicateHashStatus() {
    let nextDelay = 3500;
    try {
      const res = await fetch('/api/backup/duplicates/hash-status');
      if (res.ok) {
        const data = await res.json();
        state.latestDupHashData = data;
        renderDuplicateHashStatus(data);

        if (data.status === 'hashing') {
          nextDelay = 1500;
        } else if (data.status === 'paused') {
          nextDelay = 5000;
        } else {
          nextDelay = 8000;
        }
      }
    } catch (err) {
      console.error('Error checking duplicate hash status:', err);
    } finally {
      clearTimeout(state.dupHashPollTimer);
      state.dupHashPollTimer = setTimeout(pollDuplicateHashStatus, nextDelay);
    }
  }

  // Render Duplicate Hash Pre-Verification Status
  function renderDuplicateHashStatus(data) {
    if (!els.hashSummaryText) return;
    const isPaused = data.is_paused;
    const isHashing = data.status === 'hashing';
    const isDone = (data.pending_count === 0 && data.total_candidates > 0) || (data.status === 'idle' && data.total_candidates > 0 && data.pending_count === 0);

    if (isPaused) {
      els.hashSummaryText.textContent = 'Paused';
      if (els.hashStatusLabel) els.hashStatusLabel.textContent = 'Paused';
    } else if (isHashing) {
      els.hashSummaryText.textContent = 'Verifying';
      if (els.hashStatusLabel) els.hashStatusLabel.textContent = 'Active';
    } else if (isDone) {
      els.hashSummaryText.textContent = '100% Pre-Hashed';
      if (els.hashStatusLabel) els.hashStatusLabel.textContent = 'Complete';
    } else {
      els.hashSummaryText.textContent = 'Idle';
      if (els.hashStatusLabel) els.hashStatusLabel.textContent = 'Idle';
    }

    if (els.toggleHashPauseBtn) {
      els.toggleHashPauseBtn.textContent = isPaused ? 'Resume' : 'Pause';
      els.toggleHashPauseBtn.className = isPaused 
        ? 'px-2 py-0.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-[10px] font-semibold transition'
        : 'px-2 py-0.5 bg-slate-800 hover:bg-slate-700 text-emerald-300 hover:text-white border border-slate-700 rounded text-[10px] font-semibold transition';
    }

    if (els.hashCurrentFile) {
      if (isHashing && data.current_file) {
        els.hashCurrentFile.textContent = data.current_file;
        els.hashCurrentFile.title = data.current_file;
      } else if (isPaused) {
        els.hashCurrentFile.textContent = 'Hash pre-verification paused';
        els.hashCurrentFile.title = '';
      } else if (isDone) {
        els.hashCurrentFile.textContent = `All ${data.total_candidates} candidate duplicates verified in SQLite`;
        els.hashCurrentFile.title = '';
      } else {
        els.hashCurrentFile.textContent = 'Idle';
        els.hashCurrentFile.title = '';
      }
    }

    const pct = data.percent || 0;
    if (els.hashPercentText) els.hashPercentText.textContent = `${pct}%`;
    if (els.hashProgressBar) els.hashProgressBar.style.width = `${pct}%`;
    if (els.hashCountsText) els.hashCountsText.textContent = `${data.hashed_count || 0} / ${data.total_candidates || 0}`;

    // Live badge inside Duplicates Review Modal
    if (els.duplicateBgHashBadge) {
      if (data.total_candidates > 0) {
        els.duplicateBgHashBadge.classList.remove('hidden');
        if (isDone) {
          if (els.dupBgHashIcon) els.dupBgHashIcon.className = 'w-1.5 h-1.5 rounded-full bg-emerald-400';
          if (els.dupBgHashText) els.dupBgHashText.textContent = `Pre-hashed: 100% (${data.total_candidates})`;
          if (els.dupBgHashPauseBtn) els.dupBgHashPauseBtn.classList.add('hidden');
        } else {
          if (els.dupBgHashIcon) els.dupBgHashIcon.className = isPaused ? 'w-1.5 h-1.5 rounded-full bg-amber-400' : 'w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse';
          if (els.dupBgHashText) els.dupBgHashText.textContent = isPaused ? `Paused: ${pct}%` : `Pre-hashing: ${pct}% (${data.hashed_count}/${data.total_candidates})`;
          if (els.dupBgHashPauseBtn) {
            els.dupBgHashPauseBtn.classList.remove('hidden');
            els.dupBgHashPauseBtn.textContent = isPaused ? 'Resume' : 'Pause';
          }
        }
      }
    }
  }

  // Poll WebP Optimizer Status
  async function pollWebpStatus() {
    let nextDelay = 3500;
    try {
      const res = await fetch('/api/backup/webp/status');
      if (res.ok) {
        const data = await res.json();
        state.latestWebpData = data;
        renderWebpStatus(data);
        updateHeaderTaskBadge();

        if (data.status === 'converting') {
          nextDelay = 1500;
        } else if (data.status === 'paused') {
          nextDelay = 5000;
        } else {
          nextDelay = 8000;
        }
      }
    } catch (err) {
      console.error('Error checking WebP status:', err);
    } finally {
      clearTimeout(state.webpPollTimer);
      state.webpPollTimer = setTimeout(pollWebpStatus, nextDelay);
    }
  }

  // Render WebP Optimizer Status
  function renderWebpStatus(data) {
    if (!els.webpSummaryText) return;
    const isPaused = data.is_paused || data.status === 'paused';
    const isConverting = data.status === 'converting';
    const isDone = data.pending_count === 0 && data.total_images > 0;

    if (isPaused) {
      els.webpSummaryText.textContent = 'Paused';
      els.webpSummaryText.className = 'text-[10px] text-amber-400 font-mono font-bold';
    } else if (isConverting) {
      els.webpSummaryText.textContent = `Optimizing (${data.concurrency || 1}w)`;
      els.webpSummaryText.className = 'text-[10px] text-teal-400 font-mono font-bold animate-pulse';
    } else if (isDone) {
      els.webpSummaryText.textContent = '100% WebP';
      els.webpSummaryText.className = 'text-[10px] text-emerald-400 font-mono font-bold';
    } else {
      els.webpSummaryText.textContent = 'Idle';
      els.webpSummaryText.className = 'text-[10px] text-slate-400 font-mono';
    }

    if (els.toggleWebpPauseBtn) {
      els.toggleWebpPauseBtn.textContent = isPaused ? 'Resume' : 'Pause';
      els.toggleWebpPauseBtn.className = isPaused 
        ? 'px-2 py-0.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-[10px] font-semibold transition'
        : 'px-2 py-0.5 bg-slate-800 hover:bg-slate-700 text-teal-300 hover:text-white border border-slate-700 rounded text-[10px] font-semibold transition';
    }

    if (els.webpCurrentFile) {
      if (isConverting && data.current_file) {
        els.webpCurrentFile.textContent = data.current_file;
        els.webpCurrentFile.title = data.current_file;
      } else if (isPaused) {
        els.webpCurrentFile.textContent = 'WebP optimization paused';
        els.webpCurrentFile.title = '';
      } else if (isDone) {
        els.webpCurrentFile.textContent = `All ${data.total_images} images processed`;
        els.webpCurrentFile.title = '';
      } else {
        els.webpCurrentFile.textContent = 'Idle';
        els.webpCurrentFile.title = '';
      }
    }

    const pct = data.percent || 0;
    if (els.webpPercentText) els.webpPercentText.textContent = `${pct}%`;
    if (els.webpProgressBar) els.webpProgressBar.style.width = `${pct}%`;
    if (els.webpCountsText) {
      const conv = data.converted_count || 0;
      const skipped = data.skipped_count || 0;
      if (skipped > 0) {
        els.webpCountsText.textContent = `${conv} converted (${skipped} kept original) / ${data.total_images || 0}`;
      } else {
        els.webpCountsText.textContent = `${conv} / ${data.total_images || 0} converted`;
      }
    }
    if (els.webpSavedText) {
      const isNeg = (data.saved_bytes || 0) < 0;
      els.webpSavedText.textContent = `${isNeg ? 'Net' : 'Saved'}: ${data.saved_formatted || '0 B'}`;
    }
  }

  // Poll Library Folder Snapshot Backup Status
  async function pollLibBackupStatus() {
    let nextDelay = 8000;
    try {
      const res = await fetch('/api/backup/library-backup/status');
      if (res.ok) {
        const data = await res.json();
        state.latestLibBackupData = data;
        renderLibBackupStatus(data);
        updateHeaderTaskBadge();

        if (data.status === 'backing_up' || data.status === 'restoring') {
          nextDelay = 1200;
        } else {
          nextDelay = 8000;
        }
      }
    } catch (err) {
      console.error('Error checking library backup status:', err);
    } finally {
      clearTimeout(state.libBackupPollTimer);
      state.libBackupPollTimer = setTimeout(pollLibBackupStatus, nextDelay);
    }
  }

  // Render Library Folder Backup Status
  function renderLibBackupStatus(data) {
    if (!els.libBackupSummaryText) return;
    const isBackingUp = data.status === 'backing_up';
    const isRestoring = data.status === 'restoring';
    const hasBackup = !!data.backup_exists;

    if (isBackingUp) {
      els.libBackupSummaryText.textContent = 'Backing up...';
      els.libBackupSummaryText.className = 'text-[10px] text-emerald-400 font-mono font-bold animate-pulse';
      if (els.libBackupIcon) els.libBackupIcon.classList.add('animate-spin');
    } else if (isRestoring) {
      els.libBackupSummaryText.textContent = 'Restoring...';
      els.libBackupSummaryText.className = 'text-[10px] text-amber-400 font-mono font-bold animate-pulse';
      if (els.libBackupIcon) els.libBackupIcon.classList.remove('animate-spin');
    } else if (data.status === 'error') {
      els.libBackupSummaryText.textContent = 'Error';
      els.libBackupSummaryText.className = 'text-[10px] text-rose-400 font-mono font-bold';
      if (els.libBackupIcon) els.libBackupIcon.classList.remove('animate-spin');
    } else if (hasBackup) {
      els.libBackupSummaryText.textContent = data.last_backup_str ? `Saved: ${data.last_backup_str}` : 'Saved';
      els.libBackupSummaryText.className = 'text-[10px] text-slate-400 font-mono';
      if (els.libBackupIcon) els.libBackupIcon.classList.remove('animate-spin');
    } else {
      els.libBackupSummaryText.textContent = 'No Backup';
      els.libBackupSummaryText.className = 'text-[10px] text-slate-500 font-mono';
      if (els.libBackupIcon) els.libBackupIcon.classList.remove('animate-spin');
    }

    if (els.triggerLibBackupBtn) {
      els.triggerLibBackupBtn.disabled = isBackingUp || isRestoring;
      if (isBackingUp || isRestoring) {
        els.triggerLibBackupBtn.classList.add('opacity-50', 'pointer-events-none');
      } else {
        els.triggerLibBackupBtn.classList.remove('opacity-50', 'pointer-events-none');
      }
    }

    if (els.triggerLibRestoreBtn) {
      els.triggerLibRestoreBtn.disabled = isBackingUp || isRestoring || !hasBackup;
      if (isBackingUp || isRestoring || !hasBackup) {
        els.triggerLibRestoreBtn.classList.add('opacity-50', 'pointer-events-none');
      } else {
        els.triggerLibRestoreBtn.classList.remove('opacity-50', 'pointer-events-none');
      }
    }

    if (els.libBackupStatusText) {
      if (isBackingUp || isRestoring) {
        els.libBackupStatusText.textContent = data.current_step || (isBackingUp ? 'Saving snapshot...' : 'Restoring data...');
        els.libBackupStatusText.title = data.current_step || '';
      } else if (data.status === 'error') {
        els.libBackupStatusText.textContent = `Error: ${data.error_message || 'Failed'}`;
        els.libBackupStatusText.title = data.error_message || '';
      } else if (hasBackup) {
        els.libBackupStatusText.textContent = `Snapshot stored in ${data.backup_path || '.media_library_backup'}`;
        els.libBackupStatusText.title = data.backup_path || '';
      } else {
        els.libBackupStatusText.textContent = 'No library snapshot saved yet';
        els.libBackupStatusText.title = '';
      }
    }

    const pct = (isBackingUp || isRestoring) ? (data.progress_percent || 0) : 100;
    if (els.libBackupPercentText) {
      els.libBackupPercentText.textContent = `${Math.round(pct)}%`;
      els.libBackupPercentText.className = (isBackingUp || isRestoring) 
        ? 'text-emerald-400 font-mono text-[10px] font-bold shrink-0 animate-pulse'
        : 'text-emerald-400 font-mono text-[10px] font-bold shrink-0';
    }
    if (els.libBackupProgressBar) {
      els.libBackupProgressBar.style.width = `${pct}%`;
    }

    if (els.libBackupCountsText) {
      if (hasBackup) {
        const fCount = (data.file_count || 0).toLocaleString();
        const tCount = (data.thumb_count || 0).toLocaleString();
        els.libBackupCountsText.textContent = `${fCount} files • ${tCount} thumbs`;
      } else {
        els.libBackupCountsText.textContent = 'Click "Backup Now" to create';
      }
    }

    if (els.libBackupSizeText) {
      if (hasBackup) {
        els.libBackupSizeText.textContent = data.total_size_formatted || '0 B';
      } else {
        els.libBackupSizeText.textContent = '0 B';
      }
    }
  }

  // Trigger manual library snapshot backup
  async function triggerLibBackup() {
    try {
      if (els.libBackupIcon) els.libBackupIcon.classList.add('animate-spin');
      showToast('Starting snapshot backup to library folder...', 'info');
      const res = await fetch('/api/backup/library-backup/save', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        showToast('Library backup in progress', 'success');
      } else {
        showToast(data.message || 'Backup already running', 'info');
      }
      pollLibBackupStatus();
    } catch (err) {
      showToast('Failed to trigger library backup: ' + err.message, 'error');
    }
  }

  // Trigger library snapshot restore
  async function triggerLibRestore() {
    const meta = state.latestLibBackupData;
    let detailMsg = '';
    if (meta && meta.last_backup_str) {
      detailMsg = `\n\nBackup details:\n• Created: ${meta.last_backup_str}\n• Files: ${(meta.file_count || 0).toLocaleString()}\n• Thumbnails: ${(meta.thumb_count || 0).toLocaleString()}\n• Total size: ${meta.total_size_formatted || 'Unknown'}`;
    }
    const confirmMsg = `Are you sure you want to restore the media index and thumbnails from the library folder backup?${detailMsg}\n\nThis will safely reload the SQLite database and unpack all thumbnails.`;
    if (!window.confirm(confirmMsg)) return;

    try {
      showToast('Starting restore from library backup...', 'info');
      const res = await fetch('/api/backup/library-backup/restore', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        showToast('Restoration started in background', 'success');
      } else {
        showToast(data.message || 'Operation already in progress', 'info');
      }
      pollLibBackupStatus();
    } catch (err) {
      showToast('Failed to trigger library restore: ' + err.message, 'error');
    }
  }

  // Toggle WebP Pause
  async function toggleWebpPause() {
    try {
      const res = await fetch('/api/backup/webp/pause', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        if (state.latestWebpData) {
          state.latestWebpData.is_paused = data.is_paused;
          state.latestWebpData.status = data.is_paused ? 'paused' : 'converting';
          renderWebpStatus(state.latestWebpData);
        }
      }
    } catch (err) {
      console.error('Error toggling WebP pause:', err);
    }
  }

  // Trigger WebP Optimization for Current Folder
  async function triggerFolderWebp() {
    try {
      if (els.optimizeFolderWebpBtn) {
        els.optimizeFolderWebpBtn.disabled = true;
        if (els.webpBtnText) els.webpBtnText.textContent = 'Queueing...';
      }
      const res = await fetch('/api/backup/webp/optimize-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_path: state.currentPath }),
      });
      if (res.ok) {
        const data = await res.json();
        showToast(data.message || 'Queued folder images for WebP optimization', 'success');
        pollWebpStatus();
      }
    } catch (err) {
      console.error('Error triggering folder WebP:', err);
      showToast('Failed to trigger WebP optimization', 'error');
    } finally {
      if (els.optimizeFolderWebpBtn) {
        els.optimizeFolderWebpBtn.disabled = false;
        applyFiltersAndSort();
      }
    }
  }

  // Toggle Duplicate Hash Pause / Resume
  async function toggleDuplicateHashPause() {
    try {
      const res = await fetch('/api/backup/duplicates/hash-pause', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        showToast(data.message || (data.is_paused ? 'Hash verification paused' : 'Hash verification resumed'), 'info');
        pollDuplicateHashStatus();
      }
    } catch (err) {
      console.error(err);
      showToast('Error toggling hash verification', 'error');
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

    // Pause / Resume WebP Optimizer
    if (els.toggleWebpPauseBtn) {
      els.toggleWebpPauseBtn.addEventListener('click', toggleWebpPause);
    }

    // Optimize Current Folder WebP Button
    if (els.optimizeFolderWebpBtn) {
      els.optimizeFolderWebpBtn.addEventListener('click', triggerFolderWebp);
    }

    // Search Scope Toggle
    if (els.scopeCurrentBtn) {
      els.scopeCurrentBtn.addEventListener('click', () => setSearchScope('current'));
    }
    if (els.scopeGlobalBtn) {
      els.scopeGlobalBtn.addEventListener('click', () => setSearchScope('global'));
    }

    // SQLite Index Sync Buttons (Header Pill & Dropdown Panel)
    if (els.syncIndexBtn) {
      els.syncIndexBtn.addEventListener('click', triggerIndexSync);
    }
    if (els.triggerIndexSyncDropdownBtn) {
      els.triggerIndexSyncDropdownBtn.addEventListener('click', triggerIndexSync);
    }

    // Library Folder Backup Buttons
    if (els.triggerLibBackupBtn) {
      els.triggerLibBackupBtn.addEventListener('click', triggerLibBackup);
    }
    if (els.triggerLibRestoreBtn) {
      els.triggerLibRestoreBtn.addEventListener('click', triggerLibRestore);
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

    // Duplicates Review Modal Events
    if (els.openDuplicatesBtn) els.openDuplicatesBtn.addEventListener('click', openDuplicatesModal);
    if (els.closeDuplicatesModalBtn) els.closeDuplicatesModalBtn.addEventListener('click', closeDuplicatesModal);
    if (els.dupModeContentBtn) els.dupModeContentBtn.addEventListener('click', () => setDuplicatesMode('content'));
    if (els.dupModeVisualBtn) els.dupModeVisualBtn.addEventListener('click', () => setDuplicatesMode('visual'));
    if (els.openIgnoredDuplicatesBtn) els.openIgnoredDuplicatesBtn.addEventListener('click', openIgnoredDuplicatesModal);
    if (els.closeIgnoredDuplicatesModalBtn) els.closeIgnoredDuplicatesModalBtn.addEventListener('click', closeIgnoredDuplicatesModal);
    if (els.closeIgnoredDuplicatesFooterBtn) els.closeIgnoredDuplicatesFooterBtn.addEventListener('click', closeIgnoredDuplicatesModal);
    if (els.autoDeleteAllVerifiedBtn) els.autoDeleteAllVerifiedBtn.addEventListener('click', (e) => autoDeleteAllVerified(e.currentTarget));
    if (els.ignoreCurrentGroupBtn) els.ignoreCurrentGroupBtn.addEventListener('click', (e) => ignoreCurrentDuplicateMatch(e.currentTarget));
    if (els.prevDuplicateGroupBtn) els.prevDuplicateGroupBtn.addEventListener('click', () => {
      if (state.duplicates.currentIndex > 0) {
        state.duplicates.currentIndex--;
        renderCurrentDuplicateGroup();
      }
    });
    if (els.nextDuplicateGroupBtn) els.nextDuplicateGroupBtn.addEventListener('click', () => {
      if (state.duplicates.currentIndex < state.duplicates.groups.length - 1) {
        state.duplicates.currentIndex++;
        renderCurrentDuplicateGroup();
      }
    });
    if (els.refreshDuplicatesBtn) els.refreshDuplicatesBtn.addEventListener('click', () => loadDuplicateGroups(true));
    if (els.toggleHashPauseBtn) els.toggleHashPauseBtn.addEventListener('click', toggleDuplicateHashPause);
    if (els.dupBgHashPauseBtn) els.dupBgHashPauseBtn.addEventListener('click', toggleDuplicateHashPause);

    // Auto-Delete On Browse Toggle
    if (els.autoDeleteOnBrowseToggle) {
      els.autoDeleteOnBrowseToggle.checked = !!state.duplicates.autoDeleteOnMatch;
      els.autoDeleteOnBrowseToggle.addEventListener('change', (e) => {
        state.duplicates.autoDeleteOnMatch = e.target.checked;
        localStorage.setItem('lib_auto_delete_duplicates', e.target.checked ? 'true' : 'false');
        if (state.duplicates.autoDeleteOnMatch && state.duplicates.isOpen) {
          const group = state.duplicates.groups[state.duplicates.currentIndex];
          if (group) verifyCurrentGroupHashes(group);
        }
      });
    }

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
    pollDuplicateHashStatus();
    pollWebpStatus();
    pollLibBackupStatus();
    fetchDuplicateSummary();
    updateIgnoredCountBadge();
  }

  // Expose callbacks globally
  window.libraryApp = {
    openRenameModal,
    openDeleteModal,
    cancelTranscode,
    openConvertAllModal,
    cancelAllTranscodes,
    openDuplicatesModal,
    resolveDuplicateSet,
    deleteSingleDuplicateFile,
    autoDeleteAllVerified,
    ignoreCurrentDuplicateMatch,
    openIgnoredDuplicatesModal,
    closeIgnoredDuplicatesModal,
    unignoreDuplicatePair,
  };

  // Start app when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
