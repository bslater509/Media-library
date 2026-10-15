import "../../../static/styles.css";
import { hydrateIcons, icon } from '../shared/icons.js';
import { computeTaskSummary, describePrimaryTask, formatTaskCount } from '../shared/tasks_badge.js';
import { cancelAllAnimPreviews, pauseAllGalleryAnimations, resumeVisibleGalleryAnimations, setAnimatedPreviews, syncAnimatedPreviewsUI, toggleAnimPause, triggerAllAnimatedPreviews, triggerFolderAnimatedPreviews, triggerSelectedAnimatedPreviews, updateBrowserCacheBadge } from './animations.js';
import { autoDeleteAllVerified, closeDuplicatesModal, closeIgnoredDuplicatesModal, deleteSingleDuplicateFile, fetchDuplicateSummary, ignoreCurrentDuplicateMatch, loadDuplicateGroups, openDuplicatesModal, openIgnoredDuplicatesModal, renderCurrentDuplicateGroup, resolveDuplicateSet, setDuplicatesMode, unignoreDuplicatePair, updateIgnoredCountBadge, verifyCurrentGroupHashes } from './duplicates.js';
import { loadFavoritesView, loadFolder, renderSidebarTree, setSidebarOpen, toggleFolderExpand } from './folders.js';
import { applyFiltersAndSort, setupInfiniteScroll, toggleFavorite, updateFavoritesBadgeCount } from './gallery.js';
import { relayoutMediaGrid, relayoutSubfolders, stepTileScale, syncTileScaleUI } from './layout.js';
import { captureCurrentFrameAsPoster, closeMediaInfoModal, downloadMediaFile, openFancybox, openMediaInfoModal, openMediaInfoModalByPath, openMoveModalFromLightbox, setLightboxVideoSpeed, toggleCurrentLightboxFavorite, toggleLightboxVideoLoop } from './lightbox.js';
import { applyBulkRename, bulkFavoriteToggle, bulkWebpOptimize, clearSelection, closeBulkRenameModal, closeContextMenu, closeDeleteModal, closeMoveModal, closeNewFolderModal, closeRenameModal, doMove, movePickerUp, openBulkDeleteModal, openBulkMove, openBulkRenameModal, openDeleteModal, openMoveModal, openNewFolderModal, openRenameModal, previewBulkRename, renderBulkRenameParams, runContextAction, selectedItems, submitDelete, submitMove, submitNewFolder, submitRename, toggleSelectAll } from './management.js';
import { debouncedGlobalSearch, forceIndexPrune, performGlobalSearch, setSearchScope, triggerIndexSync } from './search.js';
import { clearBrowserAnimCache, getBrowserAnimCacheStats, initServiceWorker, precacheFolderAnimatedPreviews } from './service_worker.js';
import { toggleSheetList } from './sheet.js';
import { SIDEBAR_TREE_KEY, TILE_SCALES, TILE_SCALE_KEY, TILE_SCALE_LABELS, els, loadExpandedFolders, loadTileScale, nameCollator, persistAnimatedThumbs, persistExpandedFolders, state } from './state.js';
import { cancelAllTranscodes, cancelTranscode, closeConvertAllModal, openConvertAllModal, pollSystemStatus, pollTranscodeStatus, submitConvertAll, toggleDuplicateHashPause, updateConvertAllBadge } from './tasks.js';
import { closeThemeModal, initMobileControlsSheet, initSheetDrag, initThemeCustomizerEvents, openThemeModal, updateMobileControlsBadge } from './theme.js';
import { applySelectedThumbnail, closeThumbnailChooser, onThumbChooserScrub, openThumbnailChooser, useCurrentThumbChooserPosition } from './thumbnail_chooser.js';
import { initUploadEvents, uploadState } from './uploads.js';
import { urlDownloadRowHtml, urlDownloadRowsHtml, urlDownloadsActivityCount, urlDownloadsSummaryText } from './url_downloads.js';
import { escapeHtml, formatBitrate, formatBytes, formatDuration, formatTimestamp, getAspectRatio, getResolutionLabel, safeAttrEscape, shouldPlayAnimatedThumb, showToast } from './utils.js';
import { toggleThumbPause, toggleWebpPause, triggerFolderThumbnails, triggerFolderWebp, triggerLibBackup, triggerLibRestore } from './workers.js';
import { cancelBatchUnpack, cancelBatchZip, closeBatchUnpackModal, closeBatchZipModal, closeCompressZipModal, closeEmptyFoldersModal, closeUnpackZipModal, openBatchUnpackModal, openBatchZipModal, openCompressZipModal, openEmptyFoldersModal, openUnpackZipModal, renderBatchUnpackCandidates, renderBatchZipCandidates, renderEmptyFoldersCandidates, submitBatchUnpack, submitBatchZip, submitCompressZip, submitDeleteEmptyFolders, submitUnpackZip, toggleBatchUnpackPause, toggleBatchZipPause } from './zip.js';


// ---------------------------------------------------------------------------
// Entrypoint: global keyboard shortcuts, event wiring and bootstrap.
// Feature logic lives in the sibling modules imported above.
// ---------------------------------------------------------------------------

// Keyboard Shortcuts Handler
document.addEventListener('keydown', (e) => {
  if (els.thumbnailChooserModal && !els.thumbnailChooserModal.classList.contains('hidden')) {
    if (e.key === 'Escape') {
      e.preventDefault();
      closeThumbnailChooser();
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      applySelectedThumbnail();
      return;
    }
  }

  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;

  if (els.mediaInfoModal && !els.mediaInfoModal.classList.contains('hidden')) {
    if (e.key === 'Escape') {
      e.preventDefault();
      closeMediaInfoModal();
      return;
    }
  }

  if (els.themeCustomizerModal && !els.themeCustomizerModal.classList.contains('hidden')) {
    if (e.key === 'Escape') {
      e.preventDefault();
      closeThemeModal();
      return;
    }
  }

  if (els.sidebarDrawer && !els.sidebarDrawer.classList.contains('-translate-x-full')) {
    if (e.key === 'Escape') {
      e.preventDefault();
      setSidebarOpen(false);
      return;
    }
  }

  // Ctrl/Cmd+A selects (or deselects) every item in the current filtered view.
  if ((e.ctrlKey || e.metaKey) && (e.key === 'a' || e.key === 'A')
      && !state.duplicates.isOpen && !state.fancyboxInstance
      && state.filteredFiles.length > 0) {
    e.preventDefault();
    toggleSelectAll();
    return;
  }

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

    const { path, name, size_formatted, file } = activeSlide.customData;
    const activeVideo = document.querySelector('.fancybox__slide.is-selected video') ||
                        document.querySelector('.fancybox__container video');

    if (e.key === 'r' || e.key === 'R') {
      e.preventDefault();
      openRenameModal(path, name);
    } else if (e.key === 'd' || e.key === 'D') {
      e.preventDefault();
      openDeleteModal(path, name, size_formatted);
    } else if (e.key === 'f' || e.key === 'F') {
      e.preventDefault();
      toggleCurrentLightboxFavorite();
    } else if (e.key === 'i' || e.key === 'I') {
      e.preventDefault();
      if (file) openMediaInfoModal(file);
      else openMediaInfoModalByPath(path);
    } else if (e.key === ' ' && activeVideo) {
      e.preventDefault();
      if (activeVideo.paused) activeVideo.play().catch(() => {});
      else activeVideo.pause();
    } else if ((e.key === 'm' || e.key === 'M') && activeVideo) {
      e.preventDefault();
      activeVideo.muted = !activeVideo.muted;
      try {
        localStorage.setItem('lib_player_muted', String(activeVideo.muted));
      } catch (err) {}
      showToast(activeVideo.muted ? 'Muted' : 'Unmuted', 'info');
    } else if ((e.key === 'l' || e.key === 'L') && activeVideo) {
      e.preventDefault();
      toggleLightboxVideoLoop();
    } else if ((e.key === 'p' || e.key === 'P') && activeVideo) {
      e.preventDefault();
      captureCurrentFrameAsPoster();
    } else if (e.key === '[' && activeVideo) {
      e.preventDefault();
      const speeds = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0];
      const cur = activeVideo.playbackRate || 1.0;
      const prev = speeds.slice().reverse().find(s => s < cur - 0.01) || speeds[0];
      setLightboxVideoSpeed(prev);
    } else if (e.key === ']' && activeVideo) {
      e.preventDefault();
      const speeds = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0];
      const cur = activeVideo.playbackRate || 1.0;
      const next = speeds.find(s => s > cur + 0.01) || speeds[speeds.length - 1];
      setLightboxVideoSpeed(next);
    } else if (e.key === ',' && activeVideo) {
      e.preventDefault();
      const step = activeVideo.paused ? 0.05 : 5.0;
      activeVideo.currentTime = Math.max(0, activeVideo.currentTime - step);
    } else if (e.key === '.' && activeVideo) {
      e.preventDefault();
      const step = activeVideo.paused ? 0.05 : 5.0;
      activeVideo.currentTime = Math.min(activeVideo.duration || 999999, activeVideo.currentTime + step);
    }
  } else if (!state.duplicates.isOpen) {
    if ((e.key === 't' || e.key === 'T') && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      if (els.themeCustomizerModal && !els.themeCustomizerModal.classList.contains('hidden')) {
        closeThemeModal();
      } else {
        openThemeModal();
      }
    }
  }
});

// ==========================================
// Event Listeners Initialization
function initEvents() {
  // Delegated sidebar tree click handler (expand toggle + node navigation)
  if (els.rootFolderList) {
    els.rootFolderList.addEventListener('click', (e) => {
      const toggle = e.target.closest('button[data-toggle-path]');
      if (toggle) {
        toggleFolderExpand(decodeURIComponent(toggle.dataset.togglePath));
        return;
      }
      const btn = e.target.closest('button[data-path]');
      if (btn) {
        const folderPath = decodeURIComponent(btn.dataset.path);
        loadFolder(folderPath);
      }
    });
  }

  // Mobile Drawer Toggle & Close
  if (els.toggleSidebarBtn) {
    els.toggleSidebarBtn.addEventListener('click', () => {
      const isOpen = els.sidebarDrawer && !els.sidebarDrawer.classList.contains('-translate-x-full');
      setSidebarOpen(!isOpen);
    });
  }
  if (els.closeSidebarBtn) {
    els.closeSidebarBtn.addEventListener('click', () => setSidebarOpen(false));
  }
  if (els.sidebarBackdrop) {
    els.sidebarBackdrop.addEventListener('click', () => setSidebarOpen(false));
  }
  if (els.sidebarFavoritesBtn) {
    els.sidebarFavoritesBtn.addEventListener('click', () => {
      loadFavoritesView();
      if (window.innerWidth < 768) setSidebarOpen(false);
    });
  }

  // Tasks tab switcher
  const switchTasksTab = (tabName, persist = true) => {
    const tabs = ['active', 'workers', 'tools'];
    const target = tabs.includes(tabName) ? tabName : 'active';
    // Only persist explicit user selections; the automatic "open on Active
    // because work is running" choice must not clobber the saved preference.
    if (persist) {
      try { localStorage.setItem('tasks_active_tab', target); } catch (e) {}
    }

    const btnActive = document.getElementById('tasksTabBtnActive');
    const btnWorkers = document.getElementById('tasksTabBtnWorkers');
    const btnTools = document.getElementById('tasksTabBtnTools');
    const paneActive = document.getElementById('tasksPaneActive');
    const paneWorkers = document.getElementById('tasksPaneWorkers');
    const paneTools = document.getElementById('tasksPaneTools');

    const setTabStyle = (btn, isActive) => {
      if (!btn) return;
      if (isActive) {
        btn.className = 'tasks-tab-btn py-2.5 px-2.5 sm:px-3 border-b-2 font-medium text-xs sm:text-sm flex items-center gap-1.5 transition text-emerald-400 border-emerald-400 cursor-pointer shrink-0';
      } else {
        btn.className = 'tasks-tab-btn py-2.5 px-2.5 sm:px-3 border-b-2 font-medium text-xs sm:text-sm flex items-center gap-1.5 transition text-slate-400 border-transparent hover:text-slate-200 cursor-pointer shrink-0';
      }
      btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
    };

    setTabStyle(btnActive, target === 'active');
    setTabStyle(btnWorkers, target === 'workers');
    setTabStyle(btnTools, target === 'tools');

    if (paneActive) paneActive.classList.toggle('hidden', target !== 'active');
    if (paneWorkers) paneWorkers.classList.toggle('hidden', target !== 'workers');
    if (paneTools) paneTools.classList.toggle('hidden', target !== 'tools');
    if (target === 'tools') {
      updateBrowserCacheBadge();
      updateConvertAllBadge();
    }
  };
  window.switchTasksTab = switchTasksTab;

  // Tasks modal toggle with smart tab selection
  const setTransferDropdownOpen = (open) => {
    if (open) {
      els.transferDropdown.classList.remove('hidden');
      if (els.transferBackdrop) els.transferBackdrop.classList.remove('hidden');
      updateBrowserCacheBadge();

      // Smart tab selection: default to 'active' if tasks are queued/active, else last saved or 'workers'
      const trData = state.latestTransferData;
      const libOpsData = state.latestLibraryOpsData;
      const tData = state.latestTranscodeData;
      const animData = state.latestAnimThumbData;
      const urlDlData = state.latestUrlDownloadsData;
      const hasActiveQueues = (trData && (trData.active || (trData.queued && trData.queued.length > 0))) ||
        (libOpsData && (libOpsData.active || (libOpsData.queued && libOpsData.queued.length > 0))) ||
        (tData && (tData.active || (tData.queued && tData.queued.length > 0))) ||
        (animData && (animData.active || (animData.queued && animData.queued.length > 0))) ||
        urlDownloadsActivityCount(urlDlData) > 0 ||
        (uploadState.activeUploads || 0) > 0 ||
        uploadState.queue.some((f) => f.status === 'pending');

      if (hasActiveQueues) {
        switchTasksTab('active', false);
      } else {
        let saved = 'workers';
        try { saved = localStorage.getItem('tasks_active_tab') || 'workers'; } catch (e) {}
        switchTasksTab(saved);
      }
    } else {
      els.transferDropdown.classList.add('hidden');
      if (els.transferBackdrop) els.transferBackdrop.classList.add('hidden');
    }
  };

  els.transferBadgeBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const isClosed = els.transferDropdown.classList.contains('hidden');
    setTransferDropdownOpen(isClosed);
    if (isClosed) {
      pollSystemStatus(true);
    }
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

  if (els.transferDropdown) {
    els.transferDropdown.addEventListener('click', (e) => {
      if (e.target === els.transferDropdown) {
        setTransferDropdownOpen(false);
      }
    });
  }

  // Footer "Close" button goes through the same close path as the header ✕
  // so the dimming backdrop overlay is dismissed too.
  if (els.transferDropdown) {
    els.transferDropdown.querySelectorAll('[data-tasks-modal-close]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        setTransferDropdownOpen(false);
      });
    });
  }

  const btnActive = document.getElementById('tasksTabBtnActive');
  const btnWorkers = document.getElementById('tasksTabBtnWorkers');
  const btnTools = document.getElementById('tasksTabBtnTools');
  const jumpToWorkers = document.getElementById('jumpToWorkersBtn');
  if (btnActive) btnActive.addEventListener('click', () => switchTasksTab('active'));
  if (btnWorkers) btnWorkers.addEventListener('click', () => switchTasksTab('workers'));
  if (btnTools) btnTools.addEventListener('click', () => switchTasksTab('tools'));
  if (jumpToWorkers) jumpToWorkers.addEventListener('click', () => switchTasksTab('workers'));

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && els.transferDropdown && !els.transferDropdown.classList.contains('hidden')) {
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

  // Generate Current Folder Animated Previews Button
  if (els.generateFolderAnimatedBtn) {
    els.generateFolderAnimatedBtn.addEventListener('click', triggerFolderAnimatedPreviews);
  }

  // Generate All Animated Previews Buttons
  if (els.generateAllAnimatedBtn) {
    els.generateAllAnimatedBtn.addEventListener('click', triggerAllAnimatedPreviews);
  }
  if (els.triggerAllAnimWorkerBtn) {
    els.triggerAllAnimWorkerBtn.addEventListener('click', triggerAllAnimatedPreviews);
  }
  if (els.triggerQueueAllAnimBtn) {
    els.triggerQueueAllAnimBtn.addEventListener('click', triggerAllAnimatedPreviews);
  }

  // Pause / Resume Animated Preview Generator
  if (els.toggleAnimPauseBtn) {
    els.toggleAnimPauseBtn.addEventListener('click', toggleAnimPause);
  }
  if (els.toggleAnimWorkerPauseBtn) {
    els.toggleAnimWorkerPauseBtn.addEventListener('click', toggleAnimPause);
  }

  // Cancel All Animated Previews Button
  if (els.cancelAllAnimBtn) {
    els.cancelAllAnimBtn.addEventListener('click', cancelAllAnimPreviews);
  }

  // Pause / Resume WebP Optimizer
  if (els.toggleWebpPauseBtn) {
    els.toggleWebpPauseBtn.addEventListener('click', toggleWebpPause);
  }

  // Optimize Current Folder WebP Button
  if (els.optimizeFolderWebpBtn) {
    els.optimizeFolderWebpBtn.addEventListener('click', triggerFolderWebp);
  }

  // Compress Image Folder to ZIP
  if (els.compressFolderZipBtn) {
    els.compressFolderZipBtn.addEventListener('click', openCompressZipModal);
  }
  if (els.cancelCompressZipBtn) {
    els.cancelCompressZipBtn.addEventListener('click', closeCompressZipModal);
  }
  if (els.closeCompressZipXBtn) {
    els.closeCompressZipXBtn.addEventListener('click', closeCompressZipModal);
  }
  if (els.compressZipModal) {
    els.compressZipModal.addEventListener('click', (e) => {
      if (e.target === els.compressZipModal) closeCompressZipModal();
    });
  }
  if (els.confirmCompressZipBtn) {
    els.confirmCompressZipBtn.addEventListener('click', submitCompressZip);
  }

  // Unpack ZIP to Folder
  if (els.unpackFolderZipBtn) {
    els.unpackFolderZipBtn.addEventListener('click', openUnpackZipModal);
  }
  if (els.cancelUnpackZipBtn) {
    els.cancelUnpackZipBtn.addEventListener('click', closeUnpackZipModal);
  }
  if (els.closeUnpackZipXBtn) {
    els.closeUnpackZipXBtn.addEventListener('click', closeUnpackZipModal);
  }
  if (els.unpackZipModal) {
    els.unpackZipModal.addEventListener('click', (e) => {
      if (e.target === els.unpackZipModal) closeUnpackZipModal();
    });
  }
  if (els.confirmUnpackZipBtn) {
    els.confirmUnpackZipBtn.addEventListener('click', submitUnpackZip);
  }

  // Batch ZIP Modal & Controls
  if (els.openBatchZipBtn) {
    els.openBatchZipBtn.addEventListener('click', openBatchZipModal);
  }
  if (els.closeBatchZipModalBtn) {
    els.closeBatchZipModalBtn.addEventListener('click', closeBatchZipModal);
  }
  if (els.cancelBatchZipModalBtn) {
    els.cancelBatchZipModalBtn.addEventListener('click', closeBatchZipModal);
  }
  if (els.batchZipModal) {
    els.batchZipModal.addEventListener('click', (e) => {
      if (e.target === els.batchZipModal) closeBatchZipModal();
    });
  }
  if (els.confirmBatchZipBtn) {
    els.confirmBatchZipBtn.addEventListener('click', submitBatchZip);
  }
  if (els.toggleBatchZipPauseBtn) {
    els.toggleBatchZipPauseBtn.addEventListener('click', toggleBatchZipPause);
  }
  if (els.cancelBatchZipBtn) {
    els.cancelBatchZipBtn.addEventListener('click', cancelBatchZip);
  }
  if (els.batchZipFilterInput) {
    els.batchZipFilterInput.addEventListener('input', (e) => {
      state.batchZipFilterQuery = e.target.value;
      renderBatchZipCandidates();
    });
  }
  if (els.batchZipSelectAllToggle) {
    els.batchZipSelectAllToggle.addEventListener('change', (e) => {
      if (e.target.checked) {
        state.batchZipSelectedPaths = new Set(state.batchZipCandidates.map(c => c.path));
      } else {
        state.batchZipSelectedPaths.clear();
      }
      renderBatchZipCandidates();
    });
  }

  // Batch Unpack Modal & Controls
  if (els.openBatchUnpackBtn) {
    els.openBatchUnpackBtn.addEventListener('click', openBatchUnpackModal);
  }
  if (els.closeBatchUnpackModalBtn) {
    els.closeBatchUnpackModalBtn.addEventListener('click', closeBatchUnpackModal);
  }
  if (els.cancelBatchUnpackModalBtn) {
    els.cancelBatchUnpackModalBtn.addEventListener('click', closeBatchUnpackModal);
  }
  if (els.batchUnpackModal) {
    els.batchUnpackModal.addEventListener('click', (e) => {
      if (e.target === els.batchUnpackModal) closeBatchUnpackModal();
    });
  }
  if (els.confirmBatchUnpackBtn) {
    els.confirmBatchUnpackBtn.addEventListener('click', submitBatchUnpack);
  }
  if (els.toggleBatchUnpackPauseBtn) {
    els.toggleBatchUnpackPauseBtn.addEventListener('click', toggleBatchUnpackPause);
  }
  if (els.cancelBatchUnpackBtn) {
    els.cancelBatchUnpackBtn.addEventListener('click', cancelBatchUnpack);
  }
  if (els.batchUnpackFilterInput) {
    els.batchUnpackFilterInput.addEventListener('input', (e) => {
      state.batchUnpackFilterQuery = e.target.value;
      renderBatchUnpackCandidates();
    });
  }
  if (els.batchUnpackSelectAllToggle) {
    els.batchUnpackSelectAllToggle.addEventListener('change', (e) => {
      if (e.target.checked) {
        state.batchUnpackSelectedPaths = new Set(state.batchUnpackCandidates.map(c => c.path));
      } else {
        state.batchUnpackSelectedPaths.clear();
      }
      renderBatchUnpackCandidates();
    });
  }

  // Delete Empty Folders Modal & Controls
  if (els.cleanDbBtn) {
    els.cleanDbBtn.addEventListener('click', async () => {
      try {
        els.cleanDbBtn.disabled = true;
        const originalText = els.cleanDbBtn.innerHTML;
        els.cleanDbBtn.innerHTML = `${icon('loader-circle', 'w-3.5 h-3.5 animate-spin')}<span>Starting...</span>`;
        const res = await fetch('/api/system/clean-db', { method: 'POST' });
        if (res.ok) {
          showToast('Database deep clean and thumbnail sweep started in the background.', 'success');
        } else {
          showToast('Failed to start deep clean', 'error');
        }
        setTimeout(() => {
          els.cleanDbBtn.disabled = false;
          els.cleanDbBtn.innerHTML = originalText;
        }, 2000);
      } catch (e) {
        console.error(e);
      }
    });
  }

  if (els.deleteEmptyFoldersBtn) {
    els.deleteEmptyFoldersBtn.addEventListener('click', openEmptyFoldersModal);
  }
  if (els.closeEmptyFoldersModalBtn) {
    els.closeEmptyFoldersModalBtn.addEventListener('click', closeEmptyFoldersModal);
  }
  if (els.cancelEmptyFoldersModalBtn) {
    els.cancelEmptyFoldersModalBtn.addEventListener('click', closeEmptyFoldersModal);
  }
  if (els.emptyFoldersModal) {
    els.emptyFoldersModal.addEventListener('click', (e) => {
      if (e.target === els.emptyFoldersModal) closeEmptyFoldersModal();
    });
  }
  if (els.confirmEmptyFoldersBtn) {
    els.confirmEmptyFoldersBtn.addEventListener('click', submitDeleteEmptyFolders);
  }
  if (els.emptyFoldersFilterInput) {
    els.emptyFoldersFilterInput.addEventListener('input', (e) => {
      state.emptyFoldersFilterQuery = e.target.value;
      renderEmptyFoldersCandidates();
    });
  }
  if (els.emptyFoldersSelectAllToggle) {
    els.emptyFoldersSelectAllToggle.addEventListener('change', (e) => {
      if (e.target.checked) {
        state.emptyFoldersSelectedPaths = new Set(state.emptyFoldersCandidates.map(c => c.path));
      } else {
        state.emptyFoldersSelectedPaths.clear();
      }
      renderEmptyFoldersCandidates();
    });
  }

  // Browser Animated Previews Cache Controls
  if (els.clearBrowserCacheBtn) {
    els.clearBrowserCacheBtn.addEventListener('click', async () => {
      try {
        els.clearBrowserCacheBtn.disabled = true;
        const res = await clearBrowserAnimCache();
        if (res.success) {
          showToast('Browser animated preview cache cleared.', 'success');
        } else {
          showToast('Browser cache is not supported or already empty.', 'info');
        }
        await updateBrowserCacheBadge();
      } catch (e) {
        console.error(e);
        showToast('Failed to clear browser cache', 'error');
      } finally {
        els.clearBrowserCacheBtn.disabled = false;
      }
    });
  }

  if (els.precacheFolderAnimBtn) {
    els.precacheFolderAnimBtn.addEventListener('click', async () => {
      if (!state.allFiles || state.allFiles.length === 0) {
        showToast('No media in current folder to cache', 'info');
        return;
      }
      try {
        els.precacheFolderAnimBtn.disabled = true;
        if (els.precacheFolderAnimBtnText) {
          els.precacheFolderAnimBtnText.textContent = 'Caching...';
        }
        showToast('Pre-caching animated previews for current folder...', 'info');

        const result = await precacheFolderAnimatedPreviews(state.allFiles, (progress) => {
          if (els.precacheFolderAnimBtnText) {
            els.precacheFolderAnimBtnText.textContent = `${progress.current}/${progress.total}`;
          }
        });

        if (result.total === 0) {
          showToast('No videos found in current folder', 'info');
        } else {
          showToast(`Pre-cached ${result.added} animated preview(s) (${result.total} video(s)) in browser storage.`, 'success');
        }
        await updateBrowserCacheBadge();
      } catch (e) {
        console.error(e);
        showToast('Pre-caching encountered an error', 'error');
      } finally {
        els.precacheFolderAnimBtn.disabled = false;
        if (els.precacheFolderAnimBtnText) {
          els.precacheFolderAnimBtnText.textContent = 'Pre-cache Folder';
        }
      }
    });
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
  if (els.indexForcePruneBtn) {
    els.indexForcePruneBtn.addEventListener('click', forceIndexPrune);
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

  // Folder Search Filter (filters currently loaded tree nodes)
  els.folderSearchInput.addEventListener('input', (e) => {
    state.folderSearchQuery = e.target.value;
    renderSidebarTree();
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
      debouncedGlobalSearch();
    } else {
      applyFiltersAndSort();
    }
  });

  els.clearFileSearchBtn.addEventListener('click', () => {
    els.fileSearchInput.value = '';
    state.searchQuery = '';
    state.searchTruncated = false;
    if (els.searchTruncationBanner) els.searchTruncationBanner.classList.add('hidden');
    els.clearFileSearchBtn.classList.add('hidden');
    if (state.searchScope === 'global') {
      performGlobalSearch();
    } else {
      applyFiltersAndSort();
    }
  });

  if (els.dismissSearchTruncationBtn) {
    els.dismissSearchTruncationBtn.addEventListener('click', () => {
      if (els.searchTruncationBanner) els.searchTruncationBanner.classList.add('hidden');
    });
  }

  // Sort Dropdown
  els.sortSelect.addEventListener('change', (e) => {
    state.activeSort = e.target.value;
    applyFiltersAndSort();
  });

  // Thumbnail size +/-
  if (els.tileSizeDownBtn) els.tileSizeDownBtn.addEventListener('click', () => stepTileScale(-1));
  if (els.tileSizeUpBtn) els.tileSizeUpBtn.addEventListener('click', () => stepTileScale(1));
  syncTileScaleUI();

  // Animated Previews Toggle Button
  if (els.toggleAnimatedPreviewsBtn) {
    els.toggleAnimatedPreviewsBtn.addEventListener('click', () => {
      setAnimatedPreviews(!state.animatedThumbs);
    });
  }
  syncAnimatedPreviewsUI();

  // Filter Buttons (All, Videos, Images, Favorites)
  const setFilter = (type) => {
    state.activeFilter = type;
    [els.filterAllBtn, els.filterVideosBtn, els.filterImagesBtn, els.filterFavoritesBtn].forEach(btn => {
      if (btn) btn.className = 'px-2.5 sm:px-3 py-0.5 sm:py-1 rounded-md font-medium text-slate-400 hover:text-white transition whitespace-nowrap';
    });
    [els.mobileFilterAllBtn, els.mobileFilterVideosBtn, els.mobileFilterImagesBtn, els.mobileFilterFavoritesBtn].forEach(btn => {
      if (btn) btn.className = 'py-2 rounded-lg font-medium text-slate-400 hover:text-white transition flex items-center justify-center gap-1';
    });

    if (type === 'all') {
      if (els.filterAllBtn) els.filterAllBtn.className = 'px-2.5 sm:px-3 py-0.5 sm:py-1 rounded-md font-medium transition bg-emerald-600 text-white shadow-sm whitespace-nowrap';
      if (els.mobileFilterAllBtn) els.mobileFilterAllBtn.className = 'py-2 rounded-lg font-semibold transition bg-emerald-600 text-white shadow-sm flex items-center justify-center gap-1';
    } else if (type === 'video') {
      if (els.filterVideosBtn) els.filterVideosBtn.className = 'px-2.5 sm:px-3 py-0.5 sm:py-1 rounded-md font-medium transition bg-emerald-600 text-white shadow-sm whitespace-nowrap';
      if (els.mobileFilterVideosBtn) els.mobileFilterVideosBtn.className = 'py-2 rounded-lg font-semibold transition bg-emerald-600 text-white shadow-sm flex items-center justify-center gap-1';
    } else if (type === 'image') {
      if (els.filterImagesBtn) els.filterImagesBtn.className = 'px-2.5 sm:px-3 py-0.5 sm:py-1 rounded-md font-medium transition bg-emerald-600 text-white shadow-sm whitespace-nowrap';
      if (els.mobileFilterImagesBtn) els.mobileFilterImagesBtn.className = 'py-2 rounded-lg font-semibold transition bg-emerald-600 text-white shadow-sm flex items-center justify-center gap-1';
    } else if (type === 'favorite') {
      if (els.filterFavoritesBtn) els.filterFavoritesBtn.className = 'px-2.5 sm:px-3 py-0.5 sm:py-1 rounded-md font-semibold transition bg-amber-500 text-slate-950 shadow-sm whitespace-nowrap';
      if (els.mobileFilterFavoritesBtn) els.mobileFilterFavoritesBtn.className = 'py-2 rounded-lg font-semibold transition bg-amber-500 text-slate-950 shadow-sm flex items-center justify-center gap-1';
    }

    applyFiltersAndSort();
    updateMobileControlsBadge();
  };

  els.filterAllBtn.addEventListener('click', () => setFilter('all'));
  els.filterVideosBtn.addEventListener('click', () => setFilter('video'));
  els.filterImagesBtn.addEventListener('click', () => setFilter('image'));
  if (els.filterFavoritesBtn) els.filterFavoritesBtn.addEventListener('click', () => setFilter('favorite'));
  if (els.mobileFilterFavoritesBtn) els.mobileFilterFavoritesBtn.addEventListener('click', () => setFilter('favorite'));

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

  // New Folder Actions
  if (els.newFolderBtn) els.newFolderBtn.addEventListener('click', openNewFolderModal);
  if (els.confirmNewFolderBtn) els.confirmNewFolderBtn.addEventListener('click', submitNewFolder);
  if (els.cancelNewFolderBtn) els.cancelNewFolderBtn.addEventListener('click', closeNewFolderModal);
  if (els.closeNewFolderModalBtn) els.closeNewFolderModalBtn.addEventListener('click', closeNewFolderModal);
  if (els.newFolderNameInput) {
    els.newFolderNameInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') submitNewFolder();
      if (e.key === 'Escape') closeNewFolderModal();
    });
  }

  // Move Actions
  if (els.confirmMoveBtn) els.confirmMoveBtn.addEventListener('click', submitMove);
  if (els.cancelMoveBtn) els.cancelMoveBtn.addEventListener('click', closeMoveModal);
  if (els.closeMoveModalBtn) els.closeMoveModalBtn.addEventListener('click', closeMoveModal);
  if (els.moveUpBtn) els.moveUpBtn.addEventListener('click', movePickerUp);
  if (els.moveConflictCancelBtn) els.moveConflictCancelBtn.addEventListener('click', () => els.moveConflictModal.classList.add('hidden'));
  if (els.moveConflictSkipBtn) els.moveConflictSkipBtn.addEventListener('click', () => {
    const p = state.pendingMove;
    if (p) doMove(p.paths, p.destination_folder, 'skip');
  });
  if (els.moveConflictRenameBtn) els.moveConflictRenameBtn.addEventListener('click', () => {
    const p = state.pendingMove;
    if (p) doMove(p.paths, p.destination_folder, 'auto_rename');
  });

  // Context Menu
  if (els.contextMenu) {
    els.contextMenu.querySelectorAll('button[data-cm]').forEach(btn => {
      btn.addEventListener('click', () => runContextAction(btn.dataset.cm));
    });
  }
  // Swallow the synthetic click that follows a long-press so it neither opens
  // the lightbox nor immediately dismisses the context menu.
  document.addEventListener('click', (e) => {
    if (state.suppressClickUntil && Date.now() < state.suppressClickUntil) {
      e.stopPropagation();
      e.preventDefault();
    }
  }, true);

  document.addEventListener('click', (e) => {
    if (!e.target.closest('#contextMenu')) closeContextMenu();
  });
  document.addEventListener('contextmenu', (e) => {
    if (!e.target.closest('#mediaGrid') && !e.target.closest('#subfolderChips')) closeContextMenu();
  });

  // Delete acknowledgement checkbox gates the confirm button
  if (els.deleteConfirmAckInput) {
    els.deleteConfirmAckInput.addEventListener('change', () => {
      els.confirmDeleteBtn.disabled = !els.deleteConfirmAckInput.checked;
    });
  }

  // Selection toolbar & bulk actions
  if (els.selectionClearBtn) els.selectionClearBtn.addEventListener('click', clearSelection);
  if (els.selectAllBtn) els.selectAllBtn.addEventListener('click', toggleSelectAll);
  if (els.selectionFavoriteBtn) els.selectionFavoriteBtn.addEventListener('click', bulkFavoriteToggle);
  if (els.selectionMoveBtn) els.selectionMoveBtn.addEventListener('click', () => {
    const items = selectedItems().map(f => ({ path: f.path, name: f.name, kind: 'file' }));
    if (items.length) openBulkMove(items);
  });
  if (els.selectionRenameBtn) els.selectionRenameBtn.addEventListener('click', openBulkRenameModal);
  if (els.selectionAnimBtn) els.selectionAnimBtn.addEventListener('click', triggerSelectedAnimatedPreviews);
  if (els.selectionWebpBtn) els.selectionWebpBtn.addEventListener('click', bulkWebpOptimize);
  if (els.selectionDeleteBtn) els.selectionDeleteBtn.addEventListener('click', openBulkDeleteModal);

  if (els.applyBulkRenameBtn) els.applyBulkRenameBtn.addEventListener('click', applyBulkRename);
  if (els.cancelBulkRenameBtn) els.cancelBulkRenameBtn.addEventListener('click', closeBulkRenameModal);
  if (els.closeBulkRenameModalBtn) els.closeBulkRenameModalBtn.addEventListener('click', closeBulkRenameModal);
  if (els.bulkRenameMode) {
    els.bulkRenameMode.addEventListener('change', () => { renderBulkRenameParams(); previewBulkRename(); });
  }
  if (els.bulkRenameModal) {
    els.bulkRenameModal.addEventListener('input', (e) => {
      if (e.target.closest('.bulk-param')) previewBulkRename();
    });
    els.bulkRenameModal.addEventListener('change', (e) => {
      if (e.target.closest('.bulk-param')) previewBulkRename();
    });
  }

  // Convert All Library Modal & Queue Actions
  if (els.convertAllLibraryBtn) els.convertAllLibraryBtn.addEventListener('click', openConvertAllModal);
  if (els.closeConvertAllModalBtn) els.closeConvertAllModalBtn.addEventListener('click', closeConvertAllModal);
  if (els.cancelConvertAllModalBtn) els.cancelConvertAllModalBtn.addEventListener('click', closeConvertAllModal);
  if (els.confirmConvertAllModalBtn) els.confirmConvertAllModalBtn.addEventListener('click', submitConvertAll);
  if (els.cancelAllTranscodesBtn) els.cancelAllTranscodesBtn.addEventListener('click', cancelAllTranscodes);

  // Media Info Modal Events
  if (els.closeInfoModalBtn) els.closeInfoModalBtn.addEventListener('click', closeMediaInfoModal);
  if (els.infoModalCloseBtn) els.infoModalCloseBtn.addEventListener('click', closeMediaInfoModal);
  if (els.mediaInfoModal) {
    els.mediaInfoModal.addEventListener('click', (e) => {
      if (e.target === els.mediaInfoModal) closeMediaInfoModal();
    });
  }
  if (els.infoModalCopyPathBtn) {
    els.infoModalCopyPathBtn.addEventListener('click', () => {
      if (state.currentInfoFile && state.currentInfoFile.path) {
        navigator.clipboard.writeText(state.currentInfoFile.path).then(() => {
          showToast('Path copied to clipboard', 'success');
        }).catch(() => {
          showToast('Failed to copy path', 'error');
        });
      }
    });
  }
  if (els.infoModalPosterBtn) {
    els.infoModalPosterBtn.addEventListener('click', () => {
      if (!state.currentInfoFile) return;
      const targetPath = state.currentInfoFile.path;
      closeMediaInfoModal();
      const idx = state.filteredFiles ? state.filteredFiles.findIndex(f => f.path === targetPath) : -1;
      if (idx >= 0) {
        openFancybox(idx);
        showToast("Scrub to desired frame and click 'Set Poster' (or press P)", 'info');
      }
    });
  }
  if (els.infoModalDownloadBtn) {
    els.infoModalDownloadBtn.addEventListener('click', () => {
      if (state.currentInfoFile) {
        downloadMediaFile(state.currentInfoFile);
      }
    });
  }
  if (els.infoModalMoveBtn) {
    els.infoModalMoveBtn.addEventListener('click', () => {
      if (state.currentInfoFile) {
        const file = state.currentInfoFile;
        closeMediaInfoModal();
        openMoveModal({ path: file.path, name: file.name, kind: 'file' });
      }
    });
  }

  // Thumbnail Chooser Modal Events
  if (els.closeThumbChooserBtn) els.closeThumbChooserBtn.addEventListener('click', closeThumbnailChooser);
  if (els.thumbChooserCancelBtn) els.thumbChooserCancelBtn.addEventListener('click', closeThumbnailChooser);
  if (els.thumbChooserApplyBtn) els.thumbChooserApplyBtn.addEventListener('click', applySelectedThumbnail);
  if (els.thumbChooserQuickApplyBtn) els.thumbChooserQuickApplyBtn.addEventListener('click', applySelectedThumbnail);
  if (els.thumbChooserUseCurrentBtn) els.thumbChooserUseCurrentBtn.addEventListener('click', useCurrentThumbChooserPosition);
  if (els.thumbChooserScrubber) {
    els.thumbChooserScrubber.addEventListener('input', onThumbChooserScrub);
  }
  if (els.thumbnailChooserModal) {
    els.thumbnailChooserModal.addEventListener('click', (e) => {
      if (e.target === els.thumbnailChooserModal) closeThumbnailChooser();
    });
  }

  // Duplicates Review Modal Events
  if (els.openDuplicatesBtn) els.openDuplicatesBtn.addEventListener('click', openDuplicatesModal);
  if (els.closeDuplicatesModalBtn) els.closeDuplicatesModalBtn.addEventListener('click', closeDuplicatesModal);
  if (els.duplicatesModal) {
    els.duplicatesModal.addEventListener('click', (e) => {
      if (e.target === els.duplicatesModal) closeDuplicatesModal();
    });
  }
  if (els.dupModeContentBtn) els.dupModeContentBtn.addEventListener('click', () => setDuplicatesMode('content'));
  if (els.dupModeVisualBtn) els.dupModeVisualBtn.addEventListener('click', () => setDuplicatesMode('visual'));
  if (els.openIgnoredDuplicatesBtn) els.openIgnoredDuplicatesBtn.addEventListener('click', openIgnoredDuplicatesModal);
  if (els.closeIgnoredDuplicatesModalBtn) els.closeIgnoredDuplicatesModalBtn.addEventListener('click', closeIgnoredDuplicatesModal);
  if (els.closeIgnoredDuplicatesFooterBtn) els.closeIgnoredDuplicatesFooterBtn.addEventListener('click', closeIgnoredDuplicatesModal);
  if (els.ignoredDuplicatesModal) {
    els.ignoredDuplicatesModal.addEventListener('click', (e) => {
      if (e.target === els.ignoredDuplicatesModal) closeIgnoredDuplicatesModal();
    });
  }
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

  // Re-distribute the row-ordered masonry when the responsive column count changes
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      relayoutMediaGrid();
      relayoutSubfolders();
    }, 150);
  });

  // Pause gallery animations when browser tab is inactive / minimized
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      pauseAllGalleryAnimations();
    } else {
      resumeVisibleGalleryAnimations();
    }
  });

  // Scroll to Top floating button handler & Mobile Single-Line Header auto-hide
  let lastScrollTop = 0;
  if (els.mainStage) {
    els.mainStage.addEventListener('scroll', () => {
      const currentScrollTop = els.mainStage.scrollTop;

      if (els.scrollToTopBtn) {
        if (currentScrollTop > 350) {
          els.scrollToTopBtn.classList.remove('opacity-0', 'pointer-events-none', 'translate-y-3');
          els.scrollToTopBtn.classList.add('opacity-100', 'pointer-events-auto', 'translate-y-0');
        } else {
          els.scrollToTopBtn.classList.add('opacity-0', 'pointer-events-none', 'translate-y-3');
          els.scrollToTopBtn.classList.remove('opacity-100', 'pointer-events-auto', 'translate-y-0');
        }
      }

      // Mobile Single-Line Header auto-hide on scroll-down, reveal on scroll-up
      if (window.innerWidth < 768 && els.mobileHeader) {
        if (currentScrollTop <= 20) {
          els.mobileHeader.classList.remove('-translate-y-full');
        } else {
          const delta = currentScrollTop - lastScrollTop;
          if (delta > 8) {
            els.mobileHeader.classList.add('-translate-y-full');
          } else if (delta < -8) {
            els.mobileHeader.classList.remove('-translate-y-full');
          }
        }
      }
      lastScrollTop = Math.max(0, currentScrollTop);
    }, { passive: true });

    if (els.scrollToTopBtn) {
      els.scrollToTopBtn.addEventListener('click', () => {
        if (els.mobileHeader) els.mobileHeader.classList.remove('-translate-y-full');
        els.mainStage.scrollTo({ top: 0, behavior: 'smooth' });
      });
    }
  }

  // Theme Customizer & Mobile Gestures
  initThemeCustomizerEvents();
  initMobileControlsSheet();

  // Delegated “Show all N” expanders (tasks sheet queue lists)
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-expand-list]');
    if (!btn || !btn.dataset.total) return;
    toggleSheetList(btn.dataset.expandList, Number(btn.dataset.total), Number(btn.dataset.slice || 10));
  });

  // Swipe-to-dismiss drag handles for mobile full-height sheets
  initSheetDrag(() => setTransferDropdownOpen(false));
}

// App Initialization
async function init() {
  // Static template markup carries emoji placeholders for its icons; swap in
  // the shared inline SVG set before anything renders.
  hydrateIcons(document);
  initEvents();
  setupInfiniteScroll();
  initServiceWorker();
  updateBrowserCacheBadge();

  // Check if URL has ?folder=...
  const urlParams = new URLSearchParams(window.location.search);
  const initialFolder = urlParams.get('folder');

  await loadFolder(initialFolder || '', false);
  pollSystemStatus();
  fetchDuplicateSummary();
  updateIgnoredCountBadge();
  updateFavoritesBadgeCount();
}

// Hook into events
initUploadEvents();

// Expose callbacks globally
window.libraryApp = {
  loadFavoritesView,
  toggleFavorite,
  toggleCurrentLightboxFavorite,
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
  openCompressZipModal,
  closeCompressZipModal,
  openUnpackZipModal,
  closeUnpackZipModal,
  openBatchZipModal,
  closeBatchZipModal,
  toggleBatchZipPause,
  cancelBatchZip,
  openMediaInfoModal,
  closeMediaInfoModal,
  openMediaInfoModalByPath,
  downloadMediaFile,
  setLightboxVideoSpeed,
  toggleLightboxVideoLoop,
  captureCurrentFrameAsPoster,
  openThumbnailChooser,
  closeThumbnailChooser,
  applySelectedThumbnail,
  openMoveModalFromLightbox,
  openThemeModal,
  closeThemeModal,
  toggleAnimPause,
  cancelAllAnimPreviews,
  updateBrowserCacheBadge,
  clearBrowserAnimCache,
  precacheFolderAnimatedPreviews,
};

// Start app when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

