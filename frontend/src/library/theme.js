import { icon } from '../shared/icons.js';
import { TILE_SCALES, els, state } from './state.js';
import { showToast } from './utils.js';
import { setAnimatedPreviews, triggerAllAnimatedPreviews } from './animations.js';
import { closeDuplicatesModal, closeIgnoredDuplicatesModal } from './duplicates.js';
import { loadFolder, setSidebarOpen } from './folders.js';
import { applyFiltersAndSort } from './gallery.js';
import { applyTileScale, syncTileScaleUI } from './layout.js';
import { closeMediaInfoModal } from './lightbox.js';
import { debouncedGlobalSearch, performGlobalSearch } from './search.js';
import { pollSystemStatus, renderMobileUrlDownloadsStatus } from './tasks.js';
import { closeThumbnailChooser } from './thumbnail_chooser.js';
import { submitMobileUrlDownload, syncMobileDownloadsUI } from './uploads.js';
import { closeBatchUnpackModal, closeBatchZipModal, closeEmptyFoldersModal } from './zip.js';

// Theme customizer, mobile controls sheet and breadcrumb sheet.
// Extracted from the former monolithic library index.js.

// ==========================================
// APPEARANCE & THEME CUSTOMIZER
// ==========================================
function openThemeModal() {
  if (!els.themeCustomizerModal) return;
  renderThemeModalPickers();
  document.body.classList.add('modal-scroll-lock');
  els.themeCustomizerModal.classList.remove('hidden');
  els.themeCustomizerModal.classList.add('flex');
}
function closeThemeModal() {
  if (!els.themeCustomizerModal) return;
  document.body.classList.remove('modal-scroll-lock');
  els.themeCustomizerModal.classList.add('hidden');
  els.themeCustomizerModal.classList.remove('flex');
}
function renderThemeModalPickers() {
  if (!window.THEME_MANAGER) return;
  const cur = window.THEME_MANAGER.getCurrent();
  const accents = window.THEME_MANAGER.getAccents();
  const bases = window.THEME_MANAGER.getBases();

  // Render accents
  if (els.themeAccentPicker) {
    els.themeAccentPicker.innerHTML = accents.map(acc => {
      const isSel = acc.key === cur.accent;
      return `
        <button type="button" data-accent="${acc.key}" class="theme-accent-btn group flex flex-col items-center gap-1.5 p-2 rounded-xl border transition cursor-pointer select-none ${isSel ? 'border-emerald-500 bg-emerald-500/10 shadow-sm' : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'}">
          <div class="relative w-7 h-7 sm:w-8 sm:h-8 rounded-full shadow-md flex items-center justify-center transition-transform group-hover:scale-110" style="background-color: ${acc.accent};">
            ${isSel ? '<span class="text-white text-xs font-bold drop-shadow">✓</span>' : ''}
          </div>
          <span class="text-[11px] font-medium ${isSel ? 'text-white font-semibold' : 'text-slate-400 group-hover:text-slate-300'}">${acc.name}</span>
        </button>
      `;
    }).join('');

    els.themeAccentPicker.querySelectorAll('.theme-accent-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const accKey = btn.dataset.accent;
        const currentBase = window.THEME_MANAGER.getCurrent().base;
        window.THEME_MANAGER.setTheme(accKey, currentBase);
        renderThemeModalPickers();
        showToast(`🎨 Accent changed to ${accKey.charAt(0).toUpperCase() + accKey.slice(1)}`, 'success');
      });
    });
  }

  // Render base contrast options
  if (els.themeBasePicker) {
    els.themeBasePicker.innerHTML = bases.map(b => {
      const isSel = b.key === cur.base;
      return `
        <button type="button" data-base="${b.key}" class="theme-base-btn text-left p-3 rounded-xl border transition cursor-pointer select-none ${isSel ? 'border-emerald-500 bg-emerald-500/10 shadow-sm' : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'}">
          <div class="flex items-center justify-between mb-1">
            <span class="text-xs font-bold text-white flex items-center gap-1.5">
              <span>${b.emoji}</span>
              <span>${b.name}</span>
            </span>
            ${isSel ? '<span class="text-emerald-400 text-xs font-bold">✓</span>' : ''}
          </div>
          <p class="text-[11px] text-slate-400 leading-tight">${b.desc}</p>
        </button>
      `;
    }).join('');

    els.themeBasePicker.querySelectorAll('.theme-base-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const baseKey = btn.dataset.base;
        const currentAcc = window.THEME_MANAGER.getCurrent().accent;
        window.THEME_MANAGER.setTheme(currentAcc, baseKey);
        renderThemeModalPickers();
        showToast(`🌌 Theme changed to ${btn.querySelector('span:last-child').textContent}`, 'success');
      });
    });
  }

  // Double tap toggle
  if (els.mobileDoubleTapSeekToggle) {
    els.mobileDoubleTapSeekToggle.checked = localStorage.getItem('lib_touch_double_tap_seek') !== 'false';
  }
}
function initThemeCustomizerEvents() {
  if (els.themeModalBtn) {
    els.themeModalBtn.addEventListener('click', openThemeModal);
  }
  if (els.closeThemeModalBtn) {
    els.closeThemeModalBtn.addEventListener('click', closeThemeModal);
  }
  if (els.applyThemeModalCloseBtn) {
    els.applyThemeModalCloseBtn.addEventListener('click', closeThemeModal);
  }
  if (els.themeCustomizerModal) {
    els.themeCustomizerModal.addEventListener('click', (e) => {
      if (e.target === els.themeCustomizerModal) closeThemeModal();
    });
  }
  if (els.resetThemeDefaultsBtn) {
    els.resetThemeDefaultsBtn.addEventListener('click', () => {
      if (window.THEME_MANAGER) {
        window.THEME_MANAGER.setTheme('amber', 'stone');
      }
      localStorage.setItem('lib_touch_double_tap_seek', 'true');
      if (els.mobileDoubleTapSeekToggle) els.mobileDoubleTapSeekToggle.checked = true;
      renderThemeModalPickers();
      showToast('🎨 Theme reset to default (Amber · Midnight Stone)', 'info');
    });
  }
  if (els.mobileDoubleTapSeekToggle) {
    els.mobileDoubleTapSeekToggle.addEventListener('change', (e) => {
      localStorage.setItem('lib_touch_double_tap_seek', e.target.checked ? 'true' : 'false');
      showToast(e.target.checked ? 'Double-tap seek enabled (±5s)' : 'Double-tap seek disabled', 'info');
    });
  }
}
function updateMobileControlsBadge() {
  if (!els.mobileControlsBadge) return;
  const hasSearch = !!(state.searchQuery && state.searchQuery.trim().length > 0);
  const hasFilter = state.activeFilter !== 'all';
  const hasDups = state.duplicates && state.duplicates.totalGroups > 0;

  const isActive = hasSearch || hasFilter || hasDups;
  els.mobileControlsBadge.classList.toggle('hidden', !isActive);
}
function syncDuplicatesCount(count) {
  state.duplicates.totalGroups = count;
  if (els.duplicatesHeaderBadge) {
    if (count > 0) {
      els.duplicatesHeaderBadge.textContent = count;
      els.duplicatesHeaderBadge.classList.remove('hidden');
    } else {
      els.duplicatesHeaderBadge.classList.add('hidden');
    }
  }
  if (els.mobileDupCountBadge) {
    els.mobileDupCountBadge.textContent = count;
  }
  updateMobileControlsBadge();
}
function openMobileBreadcrumbsModal() {
  if (!els.mobileBreadcrumbsModal || !els.mobileBreadcrumbsList) return;
  if (!state.breadcrumbs || state.breadcrumbs.length === 0) {
    els.mobileBreadcrumbsList.innerHTML = `<div class="p-4 text-center text-slate-500">No folder path available</div>`;
  } else {
    els.mobileBreadcrumbsList.innerHTML = state.breadcrumbs.map((crumb, idx) => {
      const isCurrent = idx === state.breadcrumbs.length - 1;
      const isRoot = idx === 0;
      const icon = crumb.is_zip || (crumb.path && crumb.path.toLowerCase().endsWith('.zip')) ? '📦' : (isRoot ? '🏠' : '📁');
      return `
        <button 
          type="button" 
          data-crumb-path="${encodeURIComponent(crumb.path)}"
          class="w-full flex items-center justify-between p-2.5 rounded-xl transition ${isCurrent ? 'bg-emerald-600/20 text-emerald-300 font-bold border border-emerald-500/30' : 'text-slate-300 hover:bg-slate-800'}"
        >
          <span class="flex items-center gap-2 truncate">
            <span>${icon}</span>
            <span class="truncate">${crumb.name}</span>
          </span>
          ${isCurrent ? '<span class="text-[10px] text-emerald-400 font-mono">Current</span>' : '<span class="text-slate-500 text-xs">›</span>'}
        </button>
      `;
    }).join('');

    els.mobileBreadcrumbsList.querySelectorAll('button[data-crumb-path]').forEach(btn => {
      btn.addEventListener('click', () => {
        const path = decodeURIComponent(btn.dataset.crumbPath);
        closeMobileBreadcrumbsModal();
        loadFolder(path);
      });
    });
  }

  els.mobileBreadcrumbsModal.classList.remove('hidden');
  els.mobileBreadcrumbsModal.classList.add('flex');
  document.body.classList.add('modal-scroll-lock');
}
function closeMobileBreadcrumbsModal() {
  if (!els.mobileBreadcrumbsModal) return;
  els.mobileBreadcrumbsModal.classList.add('hidden');
  els.mobileBreadcrumbsModal.classList.remove('flex');
  document.body.classList.remove('modal-scroll-lock');
}
function syncThemeSheetUI() {
  if (!window.THEME_MANAGER) return;
  const cur = window.THEME_MANAGER.getCurrent();
  const currentAccent = cur.accent || 'amber';
  const currentBase = cur.base || 'stone';

  document.querySelectorAll('.mobile-accent-choice').forEach(btn => {
    const isSelected = btn.dataset.themeAccent === currentAccent;
    btn.classList.toggle('border-emerald-500', isSelected);
    btn.classList.toggle('bg-slate-900', isSelected);
    btn.classList.toggle('shadow-sm', isSelected);
  });

  document.querySelectorAll('.mobile-base-choice').forEach(btn => {
    const isSelected = btn.dataset.themeBase === currentBase;
    btn.classList.toggle('border-emerald-500', isSelected);
    btn.classList.toggle('bg-slate-900', isSelected);
    btn.classList.toggle('shadow-sm', isSelected);
  });
}
function openMobileControlsModal(tab = 'search') {
  if (!els.mobileControlsModal) return;
  els.mobileControlsModal.classList.remove('hidden');
  els.mobileControlsModal.classList.add('flex');
  document.body.classList.add('modal-scroll-lock');

  // Sync input values
  if (els.mobileSearchInput) els.mobileSearchInput.value = state.searchQuery || '';
  if (els.mobileClearSearchBtn) {
    els.mobileClearSearchBtn.classList.toggle('hidden', !state.searchQuery);
  }
  if (els.mobileSortSelect) els.mobileSortSelect.value = state.activeSort || 'name_asc';

  // Sync counts
  if (els.mobileCountAllBadge && els.countAllBadge) els.mobileCountAllBadge.textContent = els.countAllBadge.textContent;
  if (els.mobileCountVideosBadge && els.countVideosBadge) els.mobileCountVideosBadge.textContent = els.countVideosBadge.textContent;
  if (els.mobileCountImagesBadge && els.countImagesBadge) els.mobileCountImagesBadge.textContent = els.countImagesBadge.textContent;

  // Sync double-tap seek toggle
  if (els.mobileSheetDoubleTapSeekToggle) {
    els.mobileSheetDoubleTapSeekToggle.checked = localStorage.getItem('lib_touch_double_tap_seek') !== 'false';
  }

  syncTileScaleUI();
  syncThemeSheetUI();
  updateMobileControlsBadge();

  // Switch to initial tab
  if (tab === 'display' && els.mobileTabDisplayBtn) els.mobileTabDisplayBtn.click();
  else if (tab === 'actions' && els.mobileTabActionsBtn) els.mobileTabActionsBtn.click();
  else if (tab === 'downloads' && els.mobileTabDownloadsBtn) els.mobileTabDownloadsBtn.click();
  else if (tab === 'system' && els.mobileTabSystemBtn) els.mobileTabSystemBtn.click();
  else if (els.mobileTabSearchBtn) els.mobileTabSearchBtn.click();
}
function closeMobileControlsModal() {
  if (!els.mobileControlsModal) return;
  els.mobileControlsModal.classList.add('hidden');
  els.mobileControlsModal.classList.remove('flex');
  document.body.classList.remove('modal-scroll-lock');
}
function initMobileControlsSheet() {
  // Open & Close
  if (els.mobileControlsBtn) {
    els.mobileControlsBtn.addEventListener('click', () => openMobileControlsModal('search'));
  }
  if (els.closeMobileControlsBtn) {
    els.closeMobileControlsBtn.addEventListener('click', closeMobileControlsModal);
  }
  if (els.doneMobileControlsBtn) {
    els.doneMobileControlsBtn.addEventListener('click', closeMobileControlsModal);
  }
  if (els.mobileControlsModal) {
    els.mobileControlsModal.addEventListener('click', (e) => {
      if (e.target === els.mobileControlsModal) closeMobileControlsModal();
    });
  }

  // Breadcrumbs Popover
  if (els.mobileFolderTitleBtn) {
    els.mobileFolderTitleBtn.addEventListener('click', openMobileBreadcrumbsModal);
  }
  if (els.closeMobileBreadcrumbsBtn) {
    els.closeMobileBreadcrumbsBtn.addEventListener('click', closeMobileBreadcrumbsModal);
  }
  if (els.mobileBreadcrumbsModal) {
    els.mobileBreadcrumbsModal.addEventListener('click', (e) => {
      if (e.target === els.mobileBreadcrumbsModal) closeMobileBreadcrumbsModal();
    });
  }

  // Mobile Sidebar Drawer Toggle
  if (els.mobileSidebarToggleBtn) {
    els.mobileSidebarToggleBtn.addEventListener('click', () => {
      const isOpen = els.sidebarDrawer && !els.sidebarDrawer.classList.contains('-translate-x-full');
      setSidebarOpen(!isOpen);
    });
  }

  // Tab Navigation Switchers
  const switchMobileTab = (tabName) => {
    const tabs = [
      { name: 'search', btn: els.mobileTabSearchBtn, panel: els.mobileTabSearchPanel },
      { name: 'display', btn: els.mobileTabDisplayBtn, panel: els.mobileTabDisplayPanel },
      { name: 'actions', btn: els.mobileTabActionsBtn, panel: els.mobileTabActionsPanel },
      { name: 'downloads', btn: els.mobileTabDownloadsBtn, panel: els.mobileTabDownloadsPanel },
      { name: 'system', btn: els.mobileTabSystemBtn, panel: els.mobileTabSystemPanel }
    ];

    tabs.forEach(t => {
      const isActive = t.name === tabName;
      if (t.btn) {
        if (isActive) {
          t.btn.className = 'w-full min-w-0 px-1 py-2 rounded-lg text-xs font-semibold transition bg-emerald-600 text-white shadow-sm flex items-center justify-center gap-1 leading-tight';
        } else {
          t.btn.className = 'w-full min-w-0 px-1 py-2 rounded-lg text-xs font-medium text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 transition flex items-center justify-center gap-1 leading-tight';
        }
      }
      if (t.panel) {
        if (isActive) {
          t.panel.classList.remove('hidden');
        } else {
          t.panel.classList.add('hidden');
        }
      }
    });

    // The Downloads tab always reflects the current folder and the latest
    // job payload, so it is ready the moment it becomes visible.
    if (tabName === 'downloads') {
      syncMobileDownloadsUI();
      if (state.latestUrlDownloadsData) renderMobileUrlDownloadsStatus(state.latestUrlDownloadsData);
      pollSystemStatus(true);
    }
  };

  if (els.mobileTabSearchBtn) els.mobileTabSearchBtn.addEventListener('click', () => switchMobileTab('search'));
  if (els.mobileTabDisplayBtn) els.mobileTabDisplayBtn.addEventListener('click', () => switchMobileTab('display'));
  if (els.mobileTabActionsBtn) els.mobileTabActionsBtn.addEventListener('click', () => switchMobileTab('actions'));
  if (els.mobileTabDownloadsBtn) els.mobileTabDownloadsBtn.addEventListener('click', () => switchMobileTab('downloads'));
  if (els.mobileTabSystemBtn) els.mobileTabSystemBtn.addEventListener('click', () => switchMobileTab('system'));

  // Tab 4: Downloads (inline URL form; status list renders from polling)
  if (els.mobileDlStartBtn) {
    els.mobileDlStartBtn.addEventListener('click', submitMobileUrlDownload);
  }
  if (els.mobileDlUrlInput) {
    els.mobileDlUrlInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        submitMobileUrlDownload();
      }
    });
  }

  // Tab 1: Search & Filter Inputs
  if (els.mobileSearchInput) {
    els.mobileSearchInput.addEventListener('input', (e) => {
      const val = e.target.value.trim();
      state.searchQuery = val;
      if (els.fileSearchInput) els.fileSearchInput.value = e.target.value;
      if (val) {
        if (els.mobileClearSearchBtn) els.mobileClearSearchBtn.classList.remove('hidden');
        if (els.clearFileSearchBtn) els.clearFileSearchBtn.classList.remove('hidden');
      } else {
        if (els.mobileClearSearchBtn) els.mobileClearSearchBtn.classList.add('hidden');
        if (els.clearFileSearchBtn) els.clearFileSearchBtn.classList.add('hidden');
      }

      if (state.searchScope === 'global') {
        debouncedGlobalSearch();
      } else {
        applyFiltersAndSort();
      }
      updateMobileControlsBadge();
    });
  }

  if (els.mobileClearSearchBtn) {
    els.mobileClearSearchBtn.addEventListener('click', () => {
      if (els.mobileSearchInput) els.mobileSearchInput.value = '';
      if (els.fileSearchInput) els.fileSearchInput.value = '';
      state.searchQuery = '';
      state.searchTruncated = false;
      if (els.searchTruncationBanner) els.searchTruncationBanner.classList.add('hidden');
      els.mobileClearSearchBtn.classList.add('hidden');
      if (els.clearFileSearchBtn) els.clearFileSearchBtn.classList.add('hidden');
      if (state.searchScope === 'global') {
        performGlobalSearch();
      } else {
        applyFiltersAndSort();
      }
      updateMobileControlsBadge();
    });
  }

  // Search Scope in mobile sheet
  const setMobileScope = (scope) => {
    state.searchScope = scope;
    if (els.mobileScopeCurrentBtn && els.mobileScopeGlobalBtn) {
      if (scope === 'current') {
        els.mobileScopeCurrentBtn.className = 'py-2 rounded-lg font-semibold transition bg-emerald-600 text-white shadow-sm flex items-center justify-center gap-1.5';
        els.mobileScopeGlobalBtn.className = 'py-2 rounded-lg font-medium text-slate-400 hover:text-white transition flex items-center justify-center gap-1.5';
      } else {
        els.mobileScopeGlobalBtn.className = 'py-2 rounded-lg font-semibold transition bg-emerald-600 text-white shadow-sm flex items-center justify-center gap-1.5';
        els.mobileScopeCurrentBtn.className = 'py-2 rounded-lg font-medium text-slate-400 hover:text-white transition flex items-center justify-center gap-1.5';
      }
    }
    if (els.scopeCurrentBtn && els.scopeGlobalBtn) {
      if (scope === 'current') {
        els.scopeCurrentBtn.className = 'px-2 sm:px-2.5 py-1 rounded-md transition bg-emerald-600 text-white font-semibold shadow-sm whitespace-nowrap';
        els.scopeGlobalBtn.className = 'px-2 sm:px-2.5 py-1 rounded-md transition text-slate-400 hover:text-white whitespace-nowrap';
      } else {
        els.scopeGlobalBtn.className = 'px-2 sm:px-2.5 py-1 rounded-md transition bg-emerald-600 text-white font-semibold shadow-sm whitespace-nowrap';
        els.scopeCurrentBtn.className = 'px-2 sm:px-2.5 py-1 rounded-md transition text-slate-400 hover:text-white whitespace-nowrap';
      }
    }
    if (state.searchQuery) {
      if (scope === 'global') performGlobalSearch();
      else applyFiltersAndSort();
    }
  };

  if (els.mobileScopeCurrentBtn) els.mobileScopeCurrentBtn.addEventListener('click', () => setMobileScope('current'));
  if (els.mobileScopeGlobalBtn) els.mobileScopeGlobalBtn.addEventListener('click', () => setMobileScope('global'));

  // Sort Select in mobile sheet
  if (els.mobileSortSelect) {
    els.mobileSortSelect.addEventListener('change', (e) => {
      state.activeSort = e.target.value;
      if (els.sortSelect) els.sortSelect.value = e.target.value;
      applyFiltersAndSort();
    });
  }

  // Tab 2: Display & Theme
  // Derive each button's scale from TILE_SCALES by position so the XS→XL
  // buttons can never drift out of sync with the preset list (the old
  // hardcoded XL of 1.5 was not a valid preset, so tapping it did nothing
  // and reverted on reload).
  const mobileScaleBtns = [
    els.mobileTileSizeBtnXS,
    els.mobileTileSizeBtnS,
    els.mobileTileSizeBtnM,
    els.mobileTileSizeBtnL,
    els.mobileTileSizeBtnXL
  ];
  mobileScaleBtns.forEach((btn, idx) => {
    if (btn) {
      btn.addEventListener('click', () => {
        state.tileScale = TILE_SCALES[idx];
        applyTileScale();
      });
    }
  });

  // Theme Accent Choices in Sheet
  document.querySelectorAll('.mobile-accent-choice').forEach(btn => {
    btn.addEventListener('click', () => {
      const accent = btn.dataset.themeAccent;
      if (window.THEME_MANAGER && accent) {
        const cur = window.THEME_MANAGER.getCurrent();
        window.THEME_MANAGER.setTheme(accent, cur.base || 'stone');
        syncThemeSheetUI();
      }
    });
  });

  // Theme Base Choices in Sheet
  document.querySelectorAll('.mobile-base-choice').forEach(btn => {
    btn.addEventListener('click', () => {
      const base = btn.dataset.themeBase;
      if (window.THEME_MANAGER && base) {
        const cur = window.THEME_MANAGER.getCurrent();
        window.THEME_MANAGER.setTheme(cur.accent || 'amber', base);
        syncThemeSheetUI();
      }
    });
  });

  // Double-Tap Seek Toggle in Sheet
  if (els.mobileSheetDoubleTapSeekToggle) {
    els.mobileSheetDoubleTapSeekToggle.addEventListener('change', (e) => {
      localStorage.setItem('lib_touch_double_tap_seek', e.target.checked ? 'true' : 'false');
      if (els.mobileDoubleTapSeekToggle) els.mobileDoubleTapSeekToggle.checked = e.target.checked;
      showToast(e.target.checked ? 'Double-tap seek enabled (±5s)' : 'Double-tap seek disabled', 'info');
    });
  }

  // Animated Previews Toggle in Sheet
  if (els.mobileSheetAnimatedPreviewsToggle) {
    els.mobileSheetAnimatedPreviewsToggle.addEventListener('change', (e) => {
      setAnimatedPreviews(e.target.checked);
    });
  }

  // Tab 3: Folder Actions Triggers
  if (els.mobileUploadFilesTrigger) {
    els.mobileUploadFilesTrigger.addEventListener('click', () => {
      closeMobileControlsModal();
      if (els.hiddenUploadFileInput) els.hiddenUploadFileInput.click();
    });
  }
  if (els.mobileUploadFolderTrigger) {
    els.mobileUploadFolderTrigger.addEventListener('click', () => {
      closeMobileControlsModal();
      if (els.hiddenUploadFolderInput) els.hiddenUploadFolderInput.click();
    });
  }
  if (els.mobileUrlDownloadTrigger) {
    els.mobileUrlDownloadTrigger.addEventListener('click', () => {
      switchMobileTab('downloads');
    });
  }
  if (els.mobileNewFolderTrigger) {
    els.mobileNewFolderTrigger.addEventListener('click', () => {
      closeMobileControlsModal();
      if (els.newFolderBtn) els.newFolderBtn.click();
    });
  }
  if (els.mobileRefreshFolderTrigger) {
    els.mobileRefreshFolderTrigger.addEventListener('click', () => {
      closeMobileControlsModal();
      if (els.refreshFolderBtn) els.refreshFolderBtn.click();
    });
  }
  if (els.mobileCacheThumbsTrigger) {
    els.mobileCacheThumbsTrigger.addEventListener('click', () => {
      closeMobileControlsModal();
      if (els.generateFolderThumbsBtn) els.generateFolderThumbsBtn.click();
    });
  }
  if (els.mobileGenerateAllAnimatedTrigger) {
    els.mobileGenerateAllAnimatedTrigger.addEventListener('click', () => {
      closeMobileControlsModal();
      triggerAllAnimatedPreviews();
    });
  }
  if (els.mobileConvertMp4Trigger) {
    els.mobileConvertMp4Trigger.addEventListener('click', () => {
      closeMobileControlsModal();
      if (els.convertAllNonMp4Btn) els.convertAllNonMp4Btn.click();
    });
  }
  if (els.mobileOptimizeWebpTrigger) {
    els.mobileOptimizeWebpTrigger.addEventListener('click', () => {
      closeMobileControlsModal();
      if (els.optimizeFolderWebpBtn) els.optimizeFolderWebpBtn.click();
    });
  }
  if (els.mobileCompressZipTrigger) {
    els.mobileCompressZipTrigger.addEventListener('click', () => {
      closeMobileControlsModal();
      if (els.compressFolderZipBtn) els.compressFolderZipBtn.click();
    });
  }
  if (els.mobileUnpackZipTrigger) {
    els.mobileUnpackZipTrigger.addEventListener('click', () => {
      closeMobileControlsModal();
      if (els.unpackFolderZipBtn) els.unpackFolderZipBtn.click();
    });
  }

  // Tab 4: System Status Triggers
  // Mobile header one-tap tasks button opens the shared background tasks
  // modal directly (reuses the desktop pill's toggle handler).
  if (els.mobileTasksBtn) {
    els.mobileTasksBtn.addEventListener('click', () => {
      if (els.transferBadgeBtn) els.transferBadgeBtn.click();
    });
  }
  if (els.mobileOpenDupBtn) {
    els.mobileOpenDupBtn.addEventListener('click', () => {
      closeMobileControlsModal();
      if (els.openDuplicatesBtn) els.openDuplicatesBtn.click();
    });
  }
  if (els.mobileSyncIndexBtn) {
    els.mobileSyncIndexBtn.addEventListener('click', () => {
      if (els.syncIndexBtn) els.syncIndexBtn.click();
    });
  }
}
// Swipe-to-dismiss for mobile full-height sheets. Drag is only initiated
// from the grabber handle or the card header background (never from buttons,
// inputs, or other interactive controls inside the sheet), so timeline
// scrubbers, sliders, and list scrolling keep working untouched.
function initSheetDrag(closeTasksSheet = () => {}) {
  if (!window.matchMedia('(max-width: 767px)').matches) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const closeFns = {
    transferDropdown: () => closeTasksSheet(false),
    mobileControlsModal: closeMobileControlsModal,
    themeCustomizerModal: closeThemeModal,
    duplicatesModal: closeDuplicatesModal,
    ignoredDuplicatesModal: closeIgnoredDuplicatesModal,
    batchZipModal: closeBatchZipModal,
    batchUnpackModal: closeBatchUnpackModal,
    emptyFoldersModal: closeEmptyFoldersModal,
    mediaInfoModal: closeMediaInfoModal,
    thumbnailChooserModal: closeThumbnailChooser,
  };

  const DISMISS_THRESHOLD = 120;
  document.querySelectorAll('.ml-sheet').forEach((wrap) => {
    const card = wrap.querySelector('.ml-sheet-card');
    const close = closeFns[wrap.id];
    if (!card || !close) return;

    const grabber = card.querySelector('[data-sheet-grabber]');
    const header = grabber ? grabber.nextElementSibling : null;
    let startY = 0;
    let dragging = false;

    const onPointerDown = (e) => {
      if (dragging) return;
      const el = e.target;
      const isInteractive = el.closest && el.closest('button, input, select, textarea, a, label, [role="button"]');
      const inHeader = header && !isInteractive && (el === header || (header.contains && header.contains(el)));
      if (!(grabber && el.closest('[data-sheet-grabber]')) && !inHeader) return;
      startY = e.clientY;
      dragging = true;
      card.classList.add('ml-dragging');
      try { card.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    };

    const onPointerMove = (e) => {
      if (!dragging) return;
      const dy = Math.max(0, e.clientY - startY);
      card.style.transform = `translateY(${dy}px)`;
    };

    const onPointerUp = (e) => {
      if (!dragging) return;
      dragging = false;
      const dy = Math.max(0, (e.clientY || 0) - startY);
      card.classList.remove('ml-dragging');
      card.classList.add('ml-snap-back');
      if (dy > DISMISS_THRESHOLD) {
        card.style.transform = 'translateY(105%)';
        setTimeout(() => {
          card.classList.remove('ml-snap-back');
          card.style.transform = '';
          close();
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
  });
}

export { closeMobileBreadcrumbsModal, closeMobileControlsModal, closeThemeModal, initMobileControlsSheet, initSheetDrag, initThemeCustomizerEvents, openMobileBreadcrumbsModal, openMobileControlsModal, openThemeModal, renderThemeModalPickers, syncDuplicatesCount, syncThemeSheetUI, updateMobileControlsBadge };
