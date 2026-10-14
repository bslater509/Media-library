import "../../../static/styles.css";
import { showToast, escapeHtml, safeAttrEscape, formatBytes, formatDuration, getResolutionLabel, getAspectRatio, formatTimestamp, formatBitrate, shouldPlayAnimatedThumb } from './utils.js';
import { nameCollator, TILE_SCALES, TILE_SCALE_LABELS, TILE_SCALE_KEY, loadTileScale, SIDEBAR_TREE_KEY, loadExpandedFolders, persistExpandedFolders, persistAnimatedThumbs, state, els } from './state.js';
import { initServiceWorker, getBrowserAnimCacheStats, clearBrowserAnimCache, precacheFolderAnimatedPreviews } from './service_worker.js';
import { icon, hydrateIcons } from './icons.js';
import { urlDownloadRowsHtml, urlDownloadsActivityCount, urlDownloadsSummaryText } from './url_downloads.js';
import { computeTaskSummary, formatTaskCount, describePrimaryTask } from '../shared/tasks_badge.js';


  // Toast Notifications
  

  

  

  // Natural-ratio tiles are clamped to keep the grid scannable: portrait media
  // no taller than 2:3 and landscape no wider than 3:2. Unknown dimensions fall
  // back to 1:1 and simply fill their justified box with object-cover.
  const MIN_TILE_RATIO = 2 / 3;
  const MAX_TILE_RATIO = 3 / 2;
  function tileRatio(width, height) {
    const w = Number(width);
    const h = Number(height);
    if (!w || !h || w <= 0 || h <= 0) return 1;
    let ratio = w / h;
    if (ratio < MIN_TILE_RATIO) ratio = MIN_TILE_RATIO;
    if (ratio > MAX_TILE_RATIO) ratio = MAX_TILE_RATIO;
    return ratio;
  }

  // “Show all N” expanders for capped queue lists (used by the tasks sheet).
  // Each render function slices its list to `sliceCount` rows and appends a
  // toggle button; expanding keeps the full list flowing in the single scroll
  // region. Expansion state lives in-session so re-renders (polling) keep it.
  const expandedSheetLists = new Set();
  function sheetListSlice(listKey, total, sliceCount) {
    return expandedSheetLists.has(listKey) ? total : Math.min(total, sliceCount);
  }
  function renderListExpander(listKey, total, sliceCount) {
    if (total <= sliceCount) return '';
    const expanded = expandedSheetLists.has(listKey);
    return `<button type="button" class="ml-list-expander" data-expand-list="${listKey}" data-total="${total}" data-slice="${sliceCount}" aria-expanded="${expanded}">${expanded ? 'Show fewer ↑' : `Show all ${total} ↓`}</button>`;
  }
  function toggleSheetList(listKey, total, sliceCount) {
    if (expandedSheetLists.has(listKey)) expandedSheetLists.delete(listKey);
    else expandedSheetLists.add(listKey);
    pollSystemStatus(true);
  }

  // Responsive density used only to derive the justified target row height.
  function mediaColumnCount() {
    const w = window.innerWidth;
    if (w < 640) return 2;
    if (w < 768) return 3;
    if (w < 1024) return 4;
    if (w < 1280) return 5;
    return 6;
  }

  function subfolderColumnCount() {
    const w = window.innerWidth;
    if (w < 640) return 2;
    if (w < 768) return 3;
    if (w < 1024) return 4;
    return 5;
  }

  function gridGapPx() {
    return window.innerWidth >= 640 ? 16 : 10;
  }

  function gridWidth() {
    const w = els.mediaGrid ? els.mediaGrid.clientWidth : 0;
    return w > 0 ? w : Math.max(320, window.innerWidth - 32);
  }

  const MIN_ROW_HEIGHT = 96;
  const MAX_ROW_HEIGHT = 640;
  function targetRowHeight(width, gap, columns, scale) {
    const base = (width - gap * (columns - 1)) / columns;
    const h = base * (scale || 1);
    return Math.min(MAX_ROW_HEIGHT, Math.max(MIN_ROW_HEIGHT, h));
  }

  // Row-ordered "justified rows": tiles keep their natural (clamped) aspect
  // ratio, but every tile in a row shares one row height so the row fills the
  // container width exactly. Reading order therefore runs strictly left to
  // right, then down to the next row. A trailing partial row keeps the target
  // height and is left-aligned until more items arrive.
  // `measurer(item)` returns the tile's aspect ratio.
  function layoutJustifiedRows(items, width, gap, targetHeight, measurer) {
    const rows = [];
    if (!items.length) return rows;
    let current = [];
    let ratios = [];
    let sumRatio = 0;
    const flush = (height, partial) => {
      rows.push({ items: current, ratios, height, partial });
      current = [];
      ratios = [];
      sumRatio = 0;
    };
    for (const item of items) {
      const ratio = measurer(item) || 1;
      current.push(item);
      ratios.push(ratio);
      sumRatio += ratio;
      if (width > 0) {
        const rowHeight = (width - gap * (current.length - 1)) / sumRatio;
        if (rowHeight <= targetHeight) flush(rowHeight, false);
      }
    }
    if (current.length) flush(targetHeight, true);
    return rows;
  }

  // Re-flow tiles when the responsive density or container width changes.
  let _lastMediaColumnCount = mediaColumnCount();
  let _lastMediaGridWidth = gridWidth();
  function relayoutMediaGrid() {
    const n = mediaColumnCount();
    const width = gridWidth();
    if (n === _lastMediaColumnCount && width === _lastMediaGridWidth) return;
    _lastMediaColumnCount = n;
    _lastMediaGridWidth = width;
    if (!state.filteredFiles || state.filteredFiles.length === 0) return;
    const count = Math.max(state.displayedCount, Math.min(state.filteredFiles.length, 50));
    state._gridPendingRowEl = null;
    state._gridPendingStart = 0;
    renderCards(0, count);
    if (state.latestTranscodeData) updateCardsTranscodeStatus(state.latestTranscodeData);
  }

  function relayoutSubfolders() {
    if (state.subfolders && state.subfolders.length > 0) renderSubfolders();
  }

  // Reflect the current thumbnail-size preset on the +/- control and mobile sheet.
  function syncTileScaleUI() {
    const idx = Math.max(0, TILE_SCALES.indexOf(state.tileScale));
    if (els.tileSizeLabel) els.tileSizeLabel.textContent = TILE_SCALE_LABELS[idx] || 'M';
    if (els.tileSizeDownBtn) {
      els.tileSizeDownBtn.disabled = idx === 0;
      els.tileSizeDownBtn.classList.toggle('opacity-30', idx === 0);
      els.tileSizeDownBtn.classList.toggle('pointer-events-none', idx === 0);
    }
    if (els.tileSizeUpBtn) {
      els.tileSizeUpBtn.disabled = idx === TILE_SCALES.length - 1;
      els.tileSizeUpBtn.classList.toggle('opacity-30', idx === TILE_SCALES.length - 1);
      els.tileSizeUpBtn.classList.toggle('pointer-events-none', idx === TILE_SCALES.length - 1);
    }
    if (els.mobileTileSizeValue) {
      els.mobileTileSizeValue.textContent = TILE_SCALE_LABELS[idx] || 'M';
    }
    const mobileScaleBtns = [
      els.mobileTileSizeBtnXS,
      els.mobileTileSizeBtnS,
      els.mobileTileSizeBtnM,
      els.mobileTileSizeBtnL,
      els.mobileTileSizeBtnXL
    ];
    mobileScaleBtns.forEach((btn, bIdx) => {
      if (!btn) return;
      if (bIdx === idx) {
        btn.className = 'py-2 rounded-lg font-mono font-semibold transition bg-emerald-600 text-white shadow-sm';
      } else {
        btn.className = 'py-2 rounded-lg font-mono font-semibold transition text-slate-400 hover:text-white';
      }
    });
  }

  // Apply a new thumbnail-size preset: persist it and re-flow the current view.
  function applyTileScale() {
    try { localStorage.setItem(TILE_SCALE_KEY, String(state.tileScale)); } catch (e) { /* ignore */ }
    syncTileScaleUI();
    if (state.filteredFiles && state.filteredFiles.length > 0) {
      state._gridPendingRowEl = null;
      state._gridPendingStart = 0;
      renderCards(0, state.displayedCount);
      if (state.latestTranscodeData) updateCardsTranscodeStatus(state.latestTranscodeData);
    }
    relayoutSubfolders();
  }

  function stepTileScale(dir) {
    const idx = TILE_SCALES.indexOf(state.tileScale);
    const next = Math.min(TILE_SCALES.length - 1, Math.max(0, (idx < 0 ? 2 : idx) + dir));
    if (TILE_SCALES[next] === state.tileScale) return;
    state.tileScale = TILE_SCALES[next];
    applyTileScale();
  }

  // Reflect animated previews toggle state on toolbar button and mobile sheet
  function syncAnimatedPreviewsUI() {
    if (els.toggleAnimatedPreviewsBtn) {
      if (state.animatedThumbs) {
        els.toggleAnimatedPreviewsBtn.className = 'px-2 sm:px-2.5 py-0.5 sm:py-1 rounded-md font-semibold text-violet-300 bg-violet-950/60 border border-violet-500/40 shadow-sm transition whitespace-nowrap flex items-center gap-1';
      } else {
        els.toggleAnimatedPreviewsBtn.className = 'px-2 sm:px-2.5 py-0.5 sm:py-1 rounded-md font-medium text-slate-400 hover:text-white transition whitespace-nowrap flex items-center gap-1';
      }
    }
    if (els.mobileSheetAnimatedPreviewsToggle) {
      els.mobileSheetAnimatedPreviewsToggle.checked = !!state.animatedThumbs;
    }
  }

  // Batch enqueue for on-demand animated thumbnails
  const pendingAnimEnqueue = new Set();
  let animEnqueueTimer = null;

  function enqueueAnimatedThumbnail(path) {
    if (!path) return;
    pendingAnimEnqueue.add(path);
    if (!animEnqueueTimer) {
      animEnqueueTimer = setTimeout(flushAnimEnqueue, 250);
    }
  }

  async function flushAnimEnqueue() {
    animEnqueueTimer = null;
    if (pendingAnimEnqueue.size === 0) return;
    const paths = Array.from(pendingAnimEnqueue);
    pendingAnimEnqueue.clear();
    try {
      await fetch('/api/backup/thumbnails/batch-animated', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths, priority: true }),
      });
      pollSystemStatus(true);
    } catch (err) {
      console.warn('Failed to enqueue animated thumbnails:', err);
    }
  }

  // Dedicated IntersectionObserver to ensure animated WebP previews ONLY play when on screen
  function setupAnimObserver() {
    if (state.animObserver) return state.animObserver;
    if (typeof IntersectionObserver === 'undefined') return null;

    try {
      state.animObserver = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          const card = entry.target;
          if (!card || card.dataset.fileType !== 'video') return;
          card.dataset.isVisible = entry.isIntersecting ? 'true' : 'false';
          syncCardAnimationState(card);
        });
      }, {
        root: null, // viewport (clips against scrollable ancestors such as #mainStage)
        rootMargin: '0px', // strictly when on screen
        threshold: 0,
      });
    } catch (e) {
      console.warn('Could not initialize animObserver:', e);
      state.animObserver = null;
    }
    return state.animObserver;
  }

  function observeAnimatedCard(card) {
    if (!card || card.dataset.fileType !== 'video') return;
    const obs = setupAnimObserver();
    if (obs) {
      obs.observe(card);
    } else {
      // Fallback if IntersectionObserver is not available: mark visible
      card.dataset.isVisible = 'true';
      syncCardAnimationState(card);
    }
  }

  function isElementInViewport(el) {
    if (!el || typeof el.getBoundingClientRect !== 'function') return true;
    try {
      const rect = el.getBoundingClientRect();
      const vHeight = window.innerHeight || (document.documentElement ? document.documentElement.clientHeight : 1000);
      const vWidth = window.innerWidth || (document.documentElement ? document.documentElement.clientWidth : 1000);
      return (
        rect.bottom > 0 &&
        rect.right > 0 &&
        rect.top < vHeight &&
        rect.left < vWidth
      );
    } catch (e) {
      return true;
    }
  }

  // Desktop (hover-capable + fine pointer) previews animate on hover; touch
  // devices keep scroll-into-view. Capability is a pointer trait rather than a
  // width breakpoint, so a narrow desktop window still previews on hover.
  function hasHoverPointer() {
    try {
      return window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    } catch (e) {
      return false;
    }
  }

  function syncCardAnimationState(card) {
    if (!card || card.dataset.fileType !== 'video') return;
    const img = card.querySelector('.card-thumb');
    if (!img) return;
    const path = card.dataset.path;
    if (!path) return;

    const badge = card.querySelector('.card-anim-badge');
    const spinner = card.querySelector('.card-anim-spinner');

    const revertToStatic = () => {
      if (img.dataset.playingAnim === 'true') {
        const fallback = img.dataset.fallbackThumb || '';
        if (fallback) img.src = fallback;
        img.dataset.playingAnim = 'false';
      }
    };

    // Pause animations when lightbox is open or tab is hidden
    if (state.fancyboxInstance || (typeof document !== 'undefined' && document.hidden)) {
      revertToStatic();
      return;
    }

    const isVisible = card.dataset.isVisible === 'true' ||
      (card.dataset.isVisible === undefined && isElementInViewport(card));

    const shouldPlay = shouldPlayAnimatedThumb({
      isVideo: true,
      hasHoverPointer: hasHoverPointer(),
      isHovered: card.dataset.isHovered === 'true',
      isVisible,
      animatedThumbs: state.animatedThumbs,
    });

    if (!shouldPlay) {
      // Idle: static thumbnail, no badges.
      revertToStatic();
      if (badge) badge.classList.add('hidden');
      if (spinner) {
        spinner.classList.add('hidden');
        spinner.classList.remove('animate-spin');
      }
      card.removeAttribute('data-anim-pending');
      return;
    }

    const file = state.allFiles ? state.allFiles.find((f) => f.path === path) : null;
    const hasAnimated = file ? Boolean(file.has_animated) : (card.dataset.hasAnimated === 'true');

    if (hasAnimated) {
      const animUrl = file && file.animated_url
        ? file.animated_url
        : (card.dataset.animatedUrl || `/api/backup/thumbnails/animated?path=${encodeURIComponent(path)}`);

      if (img.dataset.playingAnim !== 'true' || img.src !== animUrl) {
        img.src = animUrl;
        img.dataset.playingAnim = 'true';
      }
      if (badge) {
        badge.classList.remove('hidden');
        badge.title = 'Looping animated preview';
      }
      if (spinner) {
        spinner.classList.add('hidden');
        spinner.classList.remove('animate-spin');
      }
      card.removeAttribute('data-anim-pending');
    } else {
      // Not yet generated: show static fallback with spinning indicator and enqueue
      const fallback = img.dataset.fallbackThumb || (file ? file.thumb_url : img.src);
      if (fallback && img.dataset.playingAnim === 'true') {
        img.src = fallback;
      }
      img.dataset.playingAnim = 'false';
      if (badge) {
        badge.classList.remove('hidden');
        badge.title = 'Generating animated preview...';
      }
      if (spinner) {
        spinner.classList.remove('hidden');
        spinner.classList.add('animate-spin');
      }
      card.dataset.animPending = 'true';
      enqueueAnimatedThumbnail(path);
    }
  }

  function pauseAllGalleryAnimations() {
    const cards = els.mediaGrid ? els.mediaGrid.querySelectorAll('.media-card[data-file-type="video"]') : [];
    cards.forEach((card) => {
      const img = card.querySelector('.card-thumb');
      if (img && img.dataset.playingAnim === 'true') {
        const fallback = img.dataset.fallbackThumb || '';
        if (fallback) img.src = fallback;
        img.dataset.playingAnim = 'false';
      }
    });
  }

  function resumeVisibleGalleryAnimations() {
    const cards = els.mediaGrid ? els.mediaGrid.querySelectorAll('.media-card[data-file-type="video"]') : [];
    cards.forEach((card) => {
      syncCardAnimationState(card);
    });
  }

  // Reactive tile updater when background worker finishes animated previews
  function updateCardsAnimatedStatus(data) {
    if (!data || !data.recent_completed || data.recent_completed.length === 0) return;
    const completedSet = new Set(data.recent_completed);

    // Update in-memory file objects
    if (state.allFiles) {
      state.allFiles.forEach(f => {
        if (completedSet.has(f.path)) {
          f.has_animated = true;
        }
      });
    }

    // Update visible media cards that match completed items
    const cards = els.mediaGrid ? els.mediaGrid.querySelectorAll('.media-card[data-file-type="video"]') : [];
    cards.forEach(card => {
      const path = card.dataset.path;
      if (!path || !completedSet.has(path)) return;

      card.dataset.hasAnimated = 'true';
      const vTag = Date.now();
      card.dataset.animatedUrl = `/api/backup/thumbnails/animated?path=${encodeURIComponent(path)}&v=${vTag}`;
      card.removeAttribute('data-anim-pending');
      syncCardAnimationState(card);
    });
  }

  // Update existing video tiles on screen when toggling animated previews
  function updateCardsAnimatedThumbnails() {
    const cards = els.mediaGrid ? els.mediaGrid.querySelectorAll('.media-card[data-file-type="video"]') : [];
    cards.forEach(card => {
      syncCardAnimationState(card);
    });
  }

  // Apply animated previews toggle: persist and update active media tiles
  function setAnimatedPreviews(enabled) {
    state.animatedThumbs = !!enabled;
    persistAnimatedThumbs(state.animatedThumbs);
    syncAnimatedPreviewsUI();
    updateCardsAnimatedThumbnails();
    showToast(state.animatedThumbs ? 'Animated previews enabled' : 'Animated previews disabled', 'info');
  }
  window.setAnimatedPreviews = setAnimatedPreviews;

  // Mobile Sidebar Drawer
  function setSidebarOpen(open) {
    if (!els.sidebarDrawer || !els.sidebarBackdrop) return;
    if (open) {
      els.sidebarDrawer.classList.remove('-translate-x-full');
      els.sidebarBackdrop.classList.remove('hidden');
    } else {
      els.sidebarDrawer.classList.add('-translate-x-full');
      els.sidebarBackdrop.classList.add('hidden');
    }
  }

  // Load Folder Contents
  async function loadFolder(targetPath, pushHistory = true, forceRefresh = false, silent = false) {
    try {
      state.searchTruncated = false;
      if (els.searchTruncationBanner) els.searchTruncationBanner.classList.add('hidden');
      if (pushHistory) {
        state.indexRetryCount = 0;
        clearTimeout(state.indexRetryTimer);
        if (els.mainStage) {
          els.mainStage.scrollTop = 0;
        }
      }
      if (!silent) {
        els.emptyState.classList.add('hidden');
        els.mediaGrid.innerHTML = `
          <div class="w-full py-16 flex flex-col items-center justify-center gap-3 text-slate-400">
            <span class="w-7 h-7 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin"></span>
            <span class="text-xs">Loading media files...</span>
          </div>
        `;
      }

      let url = targetPath 
        ? `/api/backup/browse?path=${encodeURIComponent(targetPath)}` 
        : `/api/backup/browse`;
      if (forceRefresh) {
        url += (url.includes('?') ? '&' : '?') + 'refresh=true';
      }

      const res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) {
        throw new Error(`Failed to load directory (${res.status})`);
      }

      const data = await res.json();
      state.basePath = data.base_path;
      state.currentPath = data.current_path;
      state.isZip = data.is_zip || false;
      state.zipExtracting = data.zip_extracting || false;
      state.rootFolders = data.root_folders || [];
      state.subfolders = data.subfolders || [];
      state.breadcrumbs = data.breadcrumbs || [];
      state.allFiles = data.files || [];
      clearSelection();

      // Folder was not yet indexed: the server kicked off background indexing and
      // returned the cached view immediately. Re-poll (silently) until it's ready.
      if (data.indexing) {
        state.indexRetryCount += 1;
        clearTimeout(state.indexRetryTimer);
        if (state.indexRetryCount <= 20) {
          state.indexRetryTimer = setTimeout(() => {
            loadFolder(data.current_path, false, false, true);
          }, 700);
        }
      } else {
        state.indexRetryCount = 0;
        clearTimeout(state.indexRetryTimer);
      }

      // Update subtitle & badges
      els.headerPathSubtitle.textContent = data.current_path;
      els.headerPathSubtitle.title = data.current_path;

      // Update URL query string
      if (pushHistory) {
        const newUrl = data.is_root ? '/library' : `/library?folder=${encodeURIComponent(data.current_path)}`;
        history.pushState({ path: data.current_path }, '', newUrl);
      }

      // Handle zip extraction progress banner
      if (data.is_zip && data.zip_extracting) {
        if (els.zipExtractBanner) {
          els.zipExtractBanner.classList.remove('hidden');
          const pct = Math.round(data.zip_extract_percent || 0);
          els.zipExtractPercent.textContent = `${pct}%`;
          els.zipExtractProgressBar.style.width = `${pct}%`;
        }
      } else {
        if (els.zipExtractBanner) {
          els.zipExtractBanner.classList.add('hidden');
        }
      }

      // Render components
      syncTreeToCurrentPath();
      renderSidebarTree();
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

  // ---------------------------------------------------------------------------
  // Sidebar Folder Tree (lazy-loading, expandable, persisted)
  // ---------------------------------------------------------------------------

  async function fetchFolderChildren(path) {
    const res = await fetch(`/api/backup/folders?path=${encodeURIComponent(path)}`);
    if (!res.ok) throw new Error(`Failed to load subfolders (${res.status})`);
    return await res.json();
  }

  // Ensure a folder's children are loaded into state.folderChildren[path].
  function ensureFolderChildren(path) {
    const entry = state.folderChildren[path] || { children: [], loaded: false, loading: false, retries: 0 };
    state.folderChildren[path] = entry;
    if (entry.loaded || entry.loading) return;

    entry.loading = true;
    (async () => {
      try {
        const data = await fetchFolderChildren(path);
        entry.children = data.subfolders || [];
        if (data.indexing && entry.retries < 20) {
          entry.retries += 1;
          entry.loading = false;
          setTimeout(() => ensureFolderChildren(path), 700);
        } else {
          entry.loaded = true;
          entry.loading = false;
        }
      } catch (err) {
        entry.loading = false;
        console.error('Error loading folder children for tree:', err);
      }
      renderSidebarTree();
    })();
  }

  function toggleFolderExpand(path) {
    if (state.expandedFolders.has(path)) {
      state.expandedFolders.delete(path);
    } else {
      state.expandedFolders.add(path);
      ensureFolderChildren(path);
    }
    persistExpandedFolders(state.expandedFolders);
    renderSidebarTree();
  }

  // Expand and lazy-load the ancestor chain of the folder currently shown.
  function syncTreeToCurrentPath() {
    if (!state.basePath) return;

    // Roots live under the base path.
    const baseEntry = state.folderChildren[state.basePath] || { children: [], loaded: false, loading: false, retries: 0 };
    baseEntry.children = state.rootFolders || [];
    baseEntry.loaded = true;
    baseEntry.loading = false;
    state.folderChildren[state.basePath] = baseEntry;

    // The currently shown folder's children are already known from browse.
    if (state.currentPath && state.currentPath !== state.basePath) {
      state.folderChildren[state.currentPath] = {
        children: state.subfolders || [],
        loaded: true,
        loading: false,
        retries: 0,
      };
    }

    // Expand ancestors (skip index 0 = base; the last crumb is current, whose
    // children were set just above, so only intermediates need lazy loading).
    (state.breadcrumbs || []).forEach((crumb, idx) => {
      if (idx === 0) return;
      state.expandedFolders.add(crumb.path);
      if (idx < state.breadcrumbs.length - 1) {
        ensureFolderChildren(crumb.path);
      }
    });
    persistExpandedFolders(state.expandedFolders);
  }

  function hasMatchingDescendant(folder, q) {
    const entry = state.folderChildren[folder.path];
    if (!entry || !entry.loaded) return false;
    return entry.children.some(c =>
      c.name.toLowerCase().includes(q) || hasMatchingDescendant(c, q)
    );
  }

  function renderSidebarTree() {
    const container = els.rootFolderList;
    const base = state.basePath;
    const q = state.folderSearchQuery.toLowerCase().trim();

    const roots = (base && state.folderChildren[base] && state.folderChildren[base].loaded)
      ? state.folderChildren[base].children
      : (state.rootFolders || []);

    els.folderCountBadge.textContent = `${roots.length} folders`;

    if (roots.length === 0) {
      container.innerHTML = `<div class="text-center py-6 text-xs text-slate-500">No matching folders</div>`;
      return;
    }

    const currentNorm = state.currentPath.toLowerCase();
    const rows = [];

    const renderChildren = (list, depth) => {
      for (const folder of list) {
        const pathKey = folder.path;
        const entry = state.folderChildren[pathKey];
        const children = entry && entry.loaded ? entry.children : [];
        const childCount = folder.subfolder_count || 0;
        const hasChildren = childCount > 0 || (entry && entry.loaded && children.length > 0);

        const nameMatches = !q || folder.name.toLowerCase().includes(q);
        const descMatches = q ? hasMatchingDescendant(folder, q) : false;
        if (q && !nameMatches && !descMatches) continue;

        const isZip = folder.is_zip || (folder.path && folder.path.toLowerCase().endsWith('.zip'));
        const icon = isZip ? '📦' : '📁';
        const count = folder.file_count || 0;

        const isActive = currentNorm === folder.path.toLowerCase() || currentNorm.startsWith(folder.path.toLowerCase() + '/');
        const activeClass = isActive
          ? 'bg-emerald-600/20 text-emerald-300 font-semibold border-emerald-500/40 shadow-sm'
          : 'text-slate-300 hover:bg-slate-800/80 hover:text-white border-transparent';

        const isExpanded = q
          ? (descMatches ? true : state.expandedFolders.has(pathKey))
          : state.expandedFolders.has(pathKey);

        const chevron = hasChildren
          ? `<button
              data-toggle-path="${encodeURIComponent(pathKey)}"
              class="shrink-0 w-6 h-8 grid place-items-center rounded-md text-slate-500 hover:text-emerald-300 transition"
              title="${isExpanded ? 'Collapse' : 'Expand'}"
            >
              <svg class="w-3.5 h-3.5 transition-transform duration-150 ${isExpanded ? 'rotate-90' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7"></path></svg>
            </button>`
          : `<span class="shrink-0 w-6"></span>`;

        const countBadge = count > 0
          ? `<span class="text-[10px] font-mono px-1.5 py-0.5 rounded-full bg-slate-800 text-slate-400 group-hover:text-slate-300 border border-slate-700/60 shrink-0">${count}</span>`
          : '';

        rows.push(`
          <div class="flex items-center gap-0.5" style="padding-left:${depth * 14}px">
            ${chevron}
            <button
              data-path="${encodeURIComponent(pathKey)}"
              class="flex-1 min-w-0 text-left px-2 py-1.5 rounded-lg border text-sm flex items-center justify-between gap-2 transition group ${activeClass}"
            >
              <div class="flex items-center gap-2 min-w-0 flex-1">
                <span class="text-base shrink-0">${icon}</span>
                <span class="truncate">${escapeHtml(folder.name)}</span>
              </div>
              ${countBadge}
            </button>
          </div>
        `);

        if (hasChildren && isExpanded) {
          if (!entry || (!entry.loaded && !entry.loading)) {
            ensureFolderChildren(pathKey);
          }
          renderChildren(children, depth + 1);
        }
      }
    };

    renderChildren(roots, 0);
    container.innerHTML = rows.join('');
  }

  // Render Breadcrumb Navigation
  function renderBreadcrumbs() {
    // Sync single-line mobile header title & back button
    if (els.mobileFolderTitleText) {
      if (!state.breadcrumbs || state.breadcrumbs.length === 0) {
        els.mobileFolderTitleText.textContent = 'Media Library';
        if (els.mobileBackBtn) els.mobileBackBtn.classList.add('hidden');
      } else {
        const currentCrumb = state.breadcrumbs[state.breadcrumbs.length - 1];
        els.mobileFolderTitleText.textContent = currentCrumb.name || 'Media Library';
        if (els.mobileBackBtn) {
          if (state.breadcrumbs.length > 1) {
            els.mobileBackBtn.classList.remove('hidden');
            const parentCrumb = state.breadcrumbs[state.breadcrumbs.length - 2];
            els.mobileBackBtn.onclick = (e) => {
              e.preventDefault();
              loadFolder(parentCrumb.path);
            };
          } else {
            els.mobileBackBtn.classList.add('hidden');
          }
        }
      }
    }

    if (!state.breadcrumbs || state.breadcrumbs.length === 0) {
      els.breadcrumbNav.innerHTML = `<span class="text-slate-400 font-mono text-xs">/</span>`;
      return;
    }

    els.breadcrumbNav.innerHTML = state.breadcrumbs.map((crumb, idx) => {
      const isLast = idx === state.breadcrumbs.length - 1;
      const isRoot = idx === 0;
      const isZip = crumb.is_zip || (crumb.path && crumb.path.toLowerCase().endsWith('.zip'));
      const icon = isZip ? '📦' : (isRoot ? '📁' : '');

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

  // One folder/ZIP cover tile. The justified layout fixes its cover height and
  // width (via flexStyle) so covers in a row share one height and read in order.
  function subfolderCardHtml(sub, coverHeight, flexStyle) {
    const isZip = sub.is_zip || (sub.path && sub.path.toLowerCase().endsWith('.zip'));
    const icon = isZip ? '📦' : '📁';
    const count = sub.file_count || 0;
    const countLabel = isZip
      ? `${count} ${count === 1 ? 'item' : 'items'}`
      : `${count} ${count === 1 ? 'file' : 'files'}`;
    const coverUrl = sub.thumb_url || '';
    const coverImg = coverUrl
      ? `<img src="${coverUrl}" loading="lazy" alt=""
           class="absolute inset-0 w-full h-full object-cover transition duration-200 group-hover:scale-105"
           onerror="this.style.display='none'">`
      : '';
    const zipBadge = isZip
      ? `<span class="absolute top-1.5 left-1.5 z-10 text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-900/80 text-amber-300 border border-amber-500/30">ZIP</span>`
      : '';
    return `
      <button
        data-sub-path="${encodeURIComponent(sub.path)}"
        data-sub-name="${escapeHtml(sub.name)}"
        data-sub-iszip="${sub.is_zip ? '1' : '0'}"
        class="group flex flex-col rounded-xl overflow-hidden bg-slate-800/80 hover:bg-slate-700/80 border border-slate-700/70 hover:border-emerald-500/40 text-slate-200 transition active:scale-[0.98] shadow-sm text-left min-w-0"
        style="${flexStyle}"
      >
        <div class="subfolder-cover relative w-full bg-slate-900/60 overflow-hidden flex items-center justify-center" style="height: ${Math.round(coverHeight)}px">
          <span class="text-3xl opacity-80 group-hover:scale-110 transition">${icon}</span>
          ${coverImg}
          ${zipBadge}
        </div>
        <div class="flex flex-col gap-0.5 p-2.5 w-full min-w-0">
          <span class="truncate text-sm font-medium text-slate-100">${escapeHtml(sub.name)}</span>
          <span class="truncate whitespace-nowrap min-w-0 text-[11px] font-mono text-slate-400">${countLabel}</span>
        </div>
      </button>
    `;
  }

  // Render Subfolders as a responsive card grid
  function renderSubfolders() {
    if (!state.subfolders || state.subfolders.length === 0) {
      els.subfoldersContainer.classList.add('hidden');
      return;
    }

    els.subfoldersContainer.classList.remove('hidden');
    els.subfolderCountBadge.textContent = state.subfolders.length;

    const gap = gridGapPx();
    const width = els.subfolderChips.clientWidth || gridWidth();
    const targetHeight = targetRowHeight(width, gap, subfolderColumnCount(), state.tileScale);
    const rows = layoutJustifiedRows(state.subfolders, width, gap, targetHeight, s => tileRatio(s.width, s.height));

    els.subfolderChips.innerHTML = rows.map(row => {
      const cards = row.items.map((sub, i) => {
        const ratio = row.ratios[i] || 1;
        const cardWidth = Math.max(1, Math.round(ratio * row.height));
        const flexStyle = row.partial
          ? `flex: 0 0 ${cardWidth}px; width: ${cardWidth}px;`
          : `flex: ${ratio} 1 0; min-width: 0;`;
        return subfolderCardHtml(sub, row.height, flexStyle);
      }).join('');
      return `<div class="subfolder-row flex items-start w-full" style="gap: ${gap}px">${cards}</div>`;
    }).join('');

    els.subfolderChips.querySelectorAll('button[data-sub-path]').forEach(btn => {
      btn.addEventListener('click', () => {
        const subPath = decodeURIComponent(btn.dataset.subPath);
        loadFolder(subPath);
      });
      btn.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        openContextMenu(e.clientX, e.clientY, {
          path: decodeURIComponent(btn.dataset.subPath),
          name: btn.dataset.subName,
          kind: btn.dataset.subIszip === '1' ? 'zip' : 'folder',
        });
      });
      attachLongPress(btn, () => ({
        path: decodeURIComponent(btn.dataset.subPath),
        name: btn.dataset.subName,
        kind: btn.dataset.subIszip === '1' ? 'zip' : 'folder',
      }));
    });
  }

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

  // Apply Type Filter, Sort and Search
  function applyFiltersAndSort() {
    if (state.searchScope === 'global' && state.searchQuery.trim()) {
      performGlobalSearch();
      return;
    }

    state.searchTruncated = false;
    if (els.searchTruncationBanner) els.searchTruncationBanner.classList.add('hidden');

    const videoCount = state.allFiles.filter(f => f.type === 'video').length;
    const imageCount = state.allFiles.filter(f => f.type === 'image').length;

    els.countAllBadge.textContent = state.allFiles.length;
    els.countVideosBadge.textContent = videoCount;
    els.countImagesBadge.textContent = imageCount;
    if (els.mobileCountAllBadge) els.mobileCountAllBadge.textContent = state.allFiles.length;
    if (els.mobileCountVideosBadge) els.mobileCountVideosBadge.textContent = videoCount;
    if (els.mobileCountImagesBadge) els.mobileCountImagesBadge.textContent = imageCount;
    updateMobileControlsBadge();

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

    // Check for Compress to ZIP and Unpack ZIP Buttons
    if (els.compressFolderZipBtn && els.unpackFolderZipBtn) {
      if (state.isZip) {
        els.compressFolderZipBtn.classList.add('hidden');
        els.unpackFolderZipBtn.classList.remove('hidden');
      } else {
        els.unpackFolderZipBtn.classList.add('hidden');
        const hasVideos = state.allFiles.some(f => f.type === 'video');
        const hasImages = state.allFiles.some(f => f.type === 'image');
        if (state.currentPath && !state.isRoot && hasImages && !hasVideos) {
          els.compressFolderZipBtn.classList.remove('hidden');
          if (els.zipBtnText) {
            const imgCount = state.allFiles.filter(f => f.type === 'image').length;
            els.zipBtnText.textContent = `Compress ${imgCount} to ZIP`;
          }
        } else {
          els.compressFolderZipBtn.classList.add('hidden');
        }
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
          return nameCollator.compare(b.name, a.name);
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
          return nameCollator.compare(a.name, b.name);
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
    updateSelectAllBtn();
  }

  // Render a Batch of Media Cards
  function renderCards(start, count) {
    let sliceStart = start;
    if (start > 0) {
      // Re-flow the previously provisional trailing row together with the batch.
      if (state._gridPendingRowEl && state._gridPendingRowEl.parentNode) {
        state._gridPendingRowEl.remove();
        sliceStart = Math.min(state._gridPendingStart, start);
      }
      state._gridPendingRowEl = null;
    } else {
      els.mediaGrid.innerHTML = '';
      if (state.animObserver) {
        state.animObserver.disconnect();
      }
      state._gridPendingRowEl = null;
      state._gridPendingStart = 0;
    }

    const end = start + count;
    const slice = state.filteredFiles.slice(sliceStart, end);
    if (slice.length === 0) return;

    // Hover-capable desktops preview a video tile on hover, so tiles start on
    // the static poster regardless of the auto-play toggle; touch devices start
    // in whatever state the toggle requests.
    const hoverCapable = hasHoverPointer();

    const gap = gridGapPx();
    const width = gridWidth();
    const targetHeight = targetRowHeight(width, gap, mediaColumnCount(), state.tileScale);
    const rows = layoutJustifiedRows(slice, width, gap, targetHeight, f => tileRatio(f.width, f.height));

    let consumed = 0;
    rows.forEach((row) => {
      const rowEl = document.createElement('div');
      rowEl.className = 'media-row flex items-start w-full';
      rowEl.style.gap = `${gap}px`;

      row.items.forEach((file, itemIdx) => {
      const globalIdx = sliceStart + consumed + itemIdx;
      const ratio = row.ratios[itemIdx] || 1;
      const card = document.createElement('div');
      card.className = 'media-card group relative flex flex-col bg-slate-900 rounded-xl overflow-hidden border border-slate-800 hover:border-slate-600 transition shadow hover:shadow-lg';
      if (row.partial) {
        const w = Math.max(1, Math.round(ratio * row.height));
        card.style.flex = `0 0 ${w}px`;
        card.style.width = `${w}px`;
      } else {
        card.style.flex = `${ratio} 1 0`;
        card.style.minWidth = '0';
      }
      card.dataset.index = globalIdx;
      card.dataset.path = file.path;
      card.dataset.name = file.name;
      card.dataset.fileType = file.type;

      card.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        openContextMenu(e.clientX, e.clientY, { path: file.path, name: file.name, kind: 'file' });
      });
      attachLongPress(card, () => ({ path: file.path, name: file.name, kind: 'file' }));

      const isVid = file.type === 'video';
      const isAnimActive = isVid && (hoverCapable ? false : state.animatedThumbs);
      const hasAnimated = Boolean(file.has_animated);

      // Start all cards with lightweight static poster thumbnail; on touch the
      // animObserver promotes cards currently on screen to animated WebP, while
      // desktops promote a card only while it is hovered.
      const thumbSrc = file.thumb_url;
      const animUrl = file.animated_url || `/api/backup/thumbnails/animated?path=${encodeURIComponent(file.path)}`;

      const isAnimGenerating = isAnimActive && !hasAnimated;
      if (isAnimGenerating) {
        card.dataset.animPending = 'true';
      }

      card.dataset.hasAnimated = hasAnimated ? 'true' : 'false';
      card.dataset.animatedUrl = animUrl;

      const extLabel = file.ext.toUpperCase().replace('.', '');
      const resLabel = getResolutionLabel(file.width, file.height);
      const durLabel = isVid && file.duration ? formatDuration(file.duration) : '';
      const dimsLabel = (file.width && file.height) ? `${file.width}×${file.height}` : '';

      card.innerHTML = `
        <!-- Thumbnail / Player Trigger Area -->
        <div class="card-thumb-wrap relative w-full bg-slate-950 overflow-hidden cursor-pointer select-none" style="height: ${Math.round(row.height)}px">
          <img 
            src="${thumbSrc}" 
            data-fallback-thumb="${file.thumb_url}"
            alt="${file.name}" 
            loading="lazy" 
            class="card-thumb w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
            onerror="if (this.dataset.fallbackThumb && this.src !== this.dataset.fallbackThumb) { this.src = this.dataset.fallbackThumb; } else { this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'100\\' height=\\'100\\' viewBox=\\'0 0 24 24\\' fill=\\'none\\' stroke=\\'%2364748b\\' stroke-width=\\'2\\'><rect width=\\'18\\' height=\\'18\\' x=\\'3\\' y=\\'3\\' rx=\\'2\\'/><circle cx=\\'8.5\\' cy=\\'8.5\\' r=\\'1.5\\'/><path d=\\'m21 15-5-5L5 21\\'/></svg>'; }"
          />

          <!-- Video Play Icon & Badge -->
          ${isVid ? `
            <div class="absolute inset-0 flex items-center justify-center bg-black/25 group-hover:bg-black/10 transition">
              <div class="w-8 h-8 sm:w-10 sm:h-10 rounded-full bg-emerald-600/90 text-white flex items-center justify-center shadow-lg group-hover:scale-110 group-active:scale-95 transition">
                <svg class="w-4 h-4 sm:w-5 sm:h-5 ml-0.5 fill-current" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
              </div>
            </div>
          ` : ''}

          <!-- Top-Left Badges (Extension, Resolution, FPS, ANIM) -->
          <div class="absolute top-2 left-2 flex items-center gap-1 z-10 pointer-events-none">
            <span class="card-ext-badge px-1.5 py-0.5 rounded bg-black/75 backdrop-blur-md text-[9px] font-mono font-bold ${file.needs_transcode ? 'text-amber-400 border-amber-500/30' : (isVid ? 'text-emerald-400 border-emerald-500/30' : 'text-slate-300 border-slate-700')} border">
              ${extLabel}
            </span>
            ${isVid ? `
              <span class="card-anim-badge ${isAnimActive ? '' : 'hidden'} px-1 py-0.5 rounded bg-violet-950/80 backdrop-blur-md text-[8px] font-mono font-bold text-violet-300 border border-violet-500/30 shadow flex items-center gap-1"
                    title="${isAnimGenerating ? 'Generating animated preview...' : 'Looping animated preview'}">
                <span>ANIM</span>
                <span class="card-anim-spinner inline-block ${isAnimGenerating ? 'animate-spin' : 'hidden'} text-[8px]">⚙️</span>
              </span>
            ` : ''}
            ${resLabel ? `
              <span class="card-res-badge px-1.5 py-0.5 rounded bg-slate-900/80 backdrop-blur-md text-[9px] font-mono font-bold text-sky-300 border border-sky-500/30 shadow">
                ${resLabel}
              </span>
            ` : ''}
            ${isVid && file.fps && file.fps >= 50 ? `
              <span class="card-fps-badge px-1 py-0.5 rounded bg-slate-900/80 backdrop-blur-md text-[8px] font-mono font-semibold text-slate-300 border border-slate-700 shadow hidden xs:inline">
                ${Math.round(file.fps)}fps
              </span>
            ` : ''}
          </div>

          <!-- Bottom-Right Video Duration Badge -->
          ${durLabel ? `
            <span class="card-duration-badge absolute bottom-2 right-2 px-1.5 py-0.5 rounded bg-black/80 backdrop-blur-md text-[10px] font-mono font-bold text-white shadow pointer-events-none z-10 border border-white/10">
              ${durLabel}
            </span>
          ` : ''}

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
              data-action="info" 
              data-idx="${globalIdx}" 
              title="Media details & info" 
              class="p-1 rounded-md bg-slate-900/90 hover:bg-slate-800 text-slate-300 hover:text-white border border-slate-700 transition"
            >
              ℹ️
            </button>
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
          <div class="flex items-center gap-1.5 min-w-0">
            <input
              type="checkbox"
              data-select-idx="${globalIdx}"
              ${state.selectedPaths.has(file.path) ? 'checked' : ''}
              class="card-select shrink-0 w-3.5 h-3.5 rounded border-slate-600 bg-slate-950/70 text-emerald-500 cursor-pointer"
              title="Select item"
            />
            <span class="card-filename text-xs text-white font-medium truncate" title="${file.name}">
              ${file.name}
            </span>
          </div>
          <div class="flex items-center justify-between text-[10px] text-slate-400 font-mono gap-1">
            <span class="card-filesize truncate whitespace-nowrap min-w-0">${file.size_formatted}</span>
            ${dimsLabel ? `
              <span class="text-slate-400 text-[9px] truncate" title="${dimsLabel} px">
                ${dimsLabel}
              </span>
            ` : ''}
            <span class="shrink-0 text-[9px] uppercase px-1 rounded ${isVid ? 'text-emerald-400 bg-emerald-950/60' : 'text-slate-400 bg-slate-800'}">
              ${file.type}
            </span>
          </div>
        </div>
      `;

      // Click card to open Fancybox (ctrl/shift toggles/extends selection)
      card.querySelector('.card-thumb-wrap').addEventListener('click', (e) => {
        if (e.target.closest('button[data-action]')) return;
        if (e.ctrlKey || e.metaKey) {
          toggleSelect(file.path, globalIdx);
          return;
        }
        if (e.shiftKey) {
          if (state.lastSelectedIndex != null) {
            rangeSelect(state.lastSelectedIndex, globalIdx);
          } else {
            state.selectedPaths.add(file.path);
            state.lastSelectedIndex = globalIdx;
            updateSelectionToolbar();
            updateCardSelectStates();
          }
          return;
        }
        openFancybox(globalIdx);
      });

      // Selection checkbox
      const selectBox = card.querySelector('.card-select');
      if (selectBox) {
        selectBox.addEventListener('click', (e) => {
          e.stopPropagation();
          toggleSelect(file.path, globalIdx);
        });
      }

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

          if (action === 'info') {
            openMediaInfoModal(f);
          } else if (action === 'rename') {
            openRenameModal(f.path, f.name);
          } else if (action === 'delete') {
            openDeleteModal(f.path, f.name, f.size_formatted);
          }
        });
      });

        rowEl.appendChild(card);
        if (isVid) {
          observeAnimatedCard(card);
          if (hoverCapable) {
            // Only genuine mouse pointers drive hover previews; a finger tap on
            // a touch-capable laptop fires pointerenter with pointerType 'touch'.
            card.addEventListener('pointerenter', (e) => {
              if (e.pointerType && e.pointerType !== 'mouse') return;
              card.dataset.isHovered = 'true';
              syncCardAnimationState(card);
            });
            card.addEventListener('pointerleave', () => {
              card.dataset.isHovered = 'false';
              syncCardAnimationState(card);
            });
          }
        }
      });

      els.mediaGrid.appendChild(rowEl);
      if (row.partial) {
        state._gridPendingRowEl = rowEl;
        state._gridPendingStart = sliceStart + consumed;
      }
      consumed += row.items.length;
    });
  }

  // Parent folder of a media path (the lightbox shows it as the file's location).
  function parentDirectoryOf(path) {
    if (!path) return '';
    const idx = String(path).lastIndexOf('/');
    return idx > 0 ? String(path).slice(0, idx) : '/';
  }

  // ==========================================
  // LIGHTBOX OVERLAY BAR (reveal on activity, fade when idle)
  // ==========================================
  const LIGHTBOX_BAR_IDLE_MS = 3500;
  let lightboxBarIdleTimer = null;
  let lightboxBarHovered = false;

  function stopLightboxBarIdle() {
    if (lightboxBarIdleTimer) {
      clearTimeout(lightboxBarIdleTimer);
      lightboxBarIdleTimer = null;
    }
    lightboxBarHovered = false;
  }

  function revealLightboxBar(container) {
    if (!container) return;
    container.classList.remove('lb-bar-idle');
    if (lightboxBarIdleTimer) clearTimeout(lightboxBarIdleTimer);
    lightboxBarIdleTimer = setTimeout(() => {
      lightboxBarIdleTimer = null;
      if (lightboxBarHovered) return;
      container.classList.add('lb-bar-idle');
    }, LIGHTBOX_BAR_IDLE_MS);
  }

  function initLightboxBarIdle(container) {
    if (!container || container.dataset.lbBarIdleBound === 'true') return;
    container.dataset.lbBarIdleBound = 'true';

    const onActivity = () => revealLightboxBar(container);
    ['pointermove', 'pointerdown', 'touchstart', 'wheel'].forEach((evt) =>
      container.addEventListener(evt, onActivity, { passive: true }));
    container.addEventListener('keydown', onActivity);

    // Parked pointer on the bar keeps it up; only real mouse pointers count so a
    // finger tap never leaves the bar stuck visible.
    const onBar = (e) => Boolean(e.target && e.target.closest && e.target.closest('.lb-meta'));
    container.addEventListener('pointerover', (e) => {
      if (e.pointerType && e.pointerType !== 'mouse') return;
      if (!onBar(e)) return;
      lightboxBarHovered = true;
      revealLightboxBar(container);
    });
    container.addEventListener('pointerout', (e) => {
      if (e.pointerType && e.pointerType !== 'mouse') return;
      if (!onBar(e)) return;
      lightboxBarHovered = false;
      revealLightboxBar(container);
    });

    revealLightboxBar(container);
  }

  // Video slides float the bar above the browser's native control strip; image
  // slides rest on the bottom edge. Desktop captions are per slide (the markup
  // already carries the right class), phones reuse one shared caption, so only
  // that case has to follow the selected slide.
  function syncLightboxBar() {
    const container = document.querySelector('.fancybox__container');
    if (!container) return;
    // Fancybox's `init` event fires before the container is in the DOM, so the
    // idle wiring is (re)established from here as well.
    initLightboxBarIdle(container);
    if (!container.classList.contains('is-compact')) return;
    const isVideo = Boolean(document.querySelector('.fancybox__slide.is-selected.has-html5video'));
    container.querySelectorAll('.lb-meta').forEach((bar) => bar.classList.toggle('is-video', isVideo));
  }

  // Open Fancybox 5 Gallery
  function openFancybox(startIndex) {
    if (!window.Fancybox) {
      showToast('Fancybox gallery library loading...', 'info');
      return;
    }

    const items = state.filteredFiles.map((f, idx) => {
      const isVid = f.type === 'video';
      const resLabel = getResolutionLabel(f.width, f.height);
      const durLabel = isVid && f.duration ? formatDuration(f.duration) : '';
      const dirLabel = parentDirectoryOf(f.path);
      const dirName = dirLabel.split('/').filter(Boolean).pop() || dirLabel;
      return {
        src: f.media_url,
        thumb: f.thumb_url,
        type: isVid ? 'html5video' : 'image',
        caption: `
          <div class="lb-meta${isVid ? ' is-video' : ''}">
            <div class="lb-meta__info">
              <span class="lb-meta__name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>
              ${dirLabel ? `<span class="lb-meta__path" title="${escapeHtml(dirLabel)}">${escapeHtml(dirLabel)}</span>` : ''}
              ${dirLabel ? `<span class="lb-meta__dirname" title="${escapeHtml(dirLabel)}">${escapeHtml(dirName)}</span>` : ''}
              <span class="lb-chip">${icon('hard-drive', 'lb-chip__icon')}<span>${escapeHtml(f.size_formatted || '')}</span></span>
              ${resLabel ? `<span class="lb-chip lb-chip--accent">${icon('monitor', 'lb-chip__icon')}<span>${resLabel}</span></span>` : ''}
              ${durLabel ? `<span class="lb-chip lb-chip--dur">${icon('clock', 'lb-chip__icon')}<span>${durLabel}</span></span>` : ''}
            </div>
            <div class="lb-actions">
              ${isVid ? `
                <label class="lb-speed" title="Playback speed ([ / ])">
                  ${icon('gauge')}
                  <select onchange="window.libraryApp.setLightboxVideoSpeed(this.value)" aria-label="Playback speed" class="lightbox-speed-select lb-speed__select">
                    <option value="0.5">0.5x</option>
                    <option value="0.75">0.75x</option>
                    <option value="1" selected>1.0x</option>
                    <option value="1.25">1.25x</option>
                    <option value="1.5">1.5x</option>
                    <option value="2">2.0x</option>
                  </select>
                </label>
                <button type="button" onclick="window.libraryApp.toggleLightboxVideoLoop()" class="lb-btn lightbox-loop-btn" title="Toggle Loop (L)" aria-label="Toggle loop">
                  ${icon('repeat-2')}
                </button>
                <button type="button" onclick="window.libraryApp.captureCurrentFrameAsPoster()" class="lb-btn lightbox-poster-btn" title="Set current frame as thumbnail poster (P)" aria-label="Set poster frame">
                  ${icon('camera')}
                </button>
                <button type="button" onclick="window.libraryApp.openThumbnailChooser()" class="lb-btn lightbox-thumb-chooser-btn" title="Choose Video Thumbnail" aria-label="Choose video thumbnail">
                  ${icon('film')}
                </button>
              ` : ''}
              <button type="button" onclick="window.libraryApp.openMediaInfoModalByPath('${safeAttrEscape(f.path)}')" class="lb-btn" title="Media Details (I)" aria-label="Media details">
                ${icon('info')}
              </button>
              <a href="${f.media_url}" download="${safeAttrEscape(f.name)}" class="lb-btn" title="Download File" aria-label="Download file">
                ${icon('download')}
              </a>
              <button type="button" onclick="window.libraryApp.openMoveModalFromLightbox('${safeAttrEscape(f.path)}', '${safeAttrEscape(f.name)}')" class="lb-btn" title="Move File" aria-label="Move file">
                ${icon('folder-input')}
              </button>
              <button type="button" onclick="window.libraryApp.openRenameModal('${safeAttrEscape(f.path)}', '${safeAttrEscape(f.name)}')" class="lb-btn" title="Rename (R)" aria-label="Rename file">
                ${icon('pencil')}
              </button>
              <button type="button" onclick="window.libraryApp.openDeleteModal('${safeAttrEscape(f.path)}', '${safeAttrEscape(f.name)}', '${safeAttrEscape(f.size_formatted)}')" class="lb-btn lb-btn--danger" title="Delete (D)" aria-label="Delete file">
                ${icon('trash-2')}
              </button>
            </div>
          </div>
        `,
        customData: {
          path: f.path,
          name: f.name,
          size_formatted: f.size_formatted,
          index: idx,
          file: f,
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
          pauseAllGalleryAnimations();
          initLightboxBarIdle(fancybox.container);
        },
        'Carousel.change': () => {
          setTimeout(syncActiveLightboxVideo, 50);
          setTimeout(syncLightboxBar, 50);
        },
        'Carousel.ready': () => {
          setTimeout(syncActiveLightboxVideo, 50);
          setTimeout(syncLightboxBar, 50);
        },
        destroy: () => {
          state.fancyboxInstance = null;
          stopLightboxBarIdle();
          resumeVisibleGalleryAnimations();
        }
      }
    });
  }

  function syncActiveLightboxVideo() {
    const video = document.querySelector('.fancybox__slide.is-selected video') ||
                  document.querySelector('.fancybox__container video');
    if (!video) return;

    try {
      const savedVol = localStorage.getItem('lib_player_volume');
      if (savedVol !== null) video.volume = parseFloat(savedVol);
      const savedMuted = localStorage.getItem('lib_player_muted');
      if (savedMuted === 'true') video.muted = true;
    } catch (e) {}

    const savedSpeed = state.lightboxPlaybackSpeed || 1.0;
    video.playbackRate = savedSpeed;
    const speedSelect = document.querySelector('.lightbox-speed-select');
    if (speedSelect) speedSelect.value = String(savedSpeed);

    video.loop = Boolean(state.lightboxVideoLoop);
    document.querySelectorAll('.lightbox-loop-btn').forEach(btn => {
      btn.classList.toggle('is-active', video.loop);
    });

    video.onvolumechange = () => {
      try {
        localStorage.setItem('lib_player_volume', String(video.volume));
        localStorage.setItem('lib_player_muted', String(video.muted));
      } catch (e) {}
    };

    // Attach mobile double-tap seek handler
    setupVideoTouchSeek(video);
  }

  function setupVideoTouchSeek(video) {
    if (!video || video._touchSeekAttached) return;
    video._touchSeekAttached = true;

    const container = video.parentElement || video;
    if (getComputedStyle(container).position === 'static') {
      container.style.position = 'relative';
    }

    let lastTapTime = 0;
    let lastTapX = 0;
    let lastTapY = 0;

    video.addEventListener('touchend', (e) => {
      const isDoubleTapEnabled = localStorage.getItem('lib_touch_double_tap_seek') !== 'false';
      if (!isDoubleTapEnabled) return;

      const touch = e.changedTouches && e.changedTouches[0];
      if (!touch) return;

      const now = Date.now();
      const timeDiff = now - lastTapTime;
      const distDiff = Math.hypot(touch.clientX - lastTapX, touch.clientY - lastTapY);

      if (timeDiff > 40 && timeDiff < 320 && distDiff < 45) {
        // Double-tap detected
        const rect = video.getBoundingClientRect();
        const clickX = touch.clientX - rect.left;
        const ratio = clickX / rect.width;

        if (ratio <= 0.42) {
          e.preventDefault();
          e.stopPropagation();
          video.currentTime = Math.max(0, video.currentTime - 5);
          showMobileSeekRipple(container, 'left', '-5s');
        } else if (ratio >= 0.58) {
          e.preventDefault();
          e.stopPropagation();
          video.currentTime = Math.min(video.duration || 999999, video.currentTime + 5);
          showMobileSeekRipple(container, 'right', '+5s');
        }
        lastTapTime = 0;
      } else {
        lastTapTime = now;
        lastTapX = touch.clientX;
        lastTapY = touch.clientY;
      }
    }, { passive: false });
  }

  function showMobileSeekRipple(container, side, text) {
    const old = container.querySelector('.mobile-seek-ripple-container');
    if (old) old.remove();

    const ripple = document.createElement('div');
    ripple.className = `mobile-seek-ripple-container ${side}`;
    const icon = side === 'left' ? '⏪' : '⏩';
    ripple.innerHTML = `
      <div class="mobile-seek-ripple-bubble">
        <span class="icon">${icon}</span>
        <span class="label font-mono font-bold">${text}</span>
      </div>
    `;
    container.appendChild(ripple);
    setTimeout(() => {
      if (ripple.parentNode) ripple.remove();
    }, 650);
  }

  function setLightboxVideoSpeed(speed) {
    const val = parseFloat(speed) || 1.0;
    state.lightboxPlaybackSpeed = val;
    const video = document.querySelector('.fancybox__slide.is-selected video') ||
                  document.querySelector('.fancybox__container video');
    if (video) {
      video.playbackRate = val;
      showToast(`Speed: ${val}x`, 'info');
    }
    const speedSelect = document.querySelector('.lightbox-speed-select');
    if (speedSelect) speedSelect.value = String(val);
  }

  function toggleLightboxVideoLoop() {
    state.lightboxVideoLoop = !state.lightboxVideoLoop;
    const video = document.querySelector('.fancybox__slide.is-selected video') ||
                  document.querySelector('.fancybox__container video');
    if (video) {
      video.loop = state.lightboxVideoLoop;
    }
    const loopBtns = document.querySelectorAll('.lightbox-loop-btn');
    loopBtns.forEach(btn => {
      btn.classList.toggle('is-active', state.lightboxVideoLoop);
    });
    showToast(state.lightboxVideoLoop ? 'Video loop enabled' : 'Video loop disabled', 'info');
  }

  async function captureCurrentFrameAsPoster() {
    if (!state.fancyboxInstance) return;
    const activeSlide = state.fancyboxInstance.getSlide();
    if (!activeSlide || !activeSlide.customData) return;
    const { file, path } = activeSlide.customData;
    if (!file || file.type !== 'video') {
      showToast('Poster capture is only available for videos', 'info');
      return;
    }
    const activeVideo = document.querySelector('.fancybox__slide.is-selected video') ||
                        document.querySelector('.fancybox__container video');
    if (!activeVideo) {
      showToast('No active video found', 'error');
      return;
    }
    const currentTime = activeVideo.currentTime || 0;
    const durStr = formatDuration(currentTime);
    showToast(`Capturing poster frame at ${durStr}...`, 'info');

    let canvasB64 = null;
    try {
      const canvas = document.createElement('canvas');
      canvas.width = activeVideo.videoWidth || 360;
      canvas.height = activeVideo.videoHeight || 240;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(activeVideo, 0, 0, canvas.width, canvas.height);
      canvasB64 = canvas.toDataURL('image/jpeg', 0.85);
    } catch (e) {
      canvasB64 = null;
    }

    try {
      const payload = { path: path, timestamp: currentTime };
      if (canvasB64) payload.image_base64 = canvasB64;

      const res = await fetch('/api/backup/thumbnails/set-poster', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to update poster frame');
      }

      showToast(`Poster updated to ${durStr}!`, 'success');

      file.has_thumb = true;
      file.thumb_url = data.thumb_url;

      const idxAll = state.allFiles ? state.allFiles.findIndex(f => f.path === path) : -1;
      if (idxAll >= 0) {
        state.allFiles[idxAll].has_thumb = true;
        state.allFiles[idxAll].thumb_url = data.thumb_url;
      }
      const idxFiltered = state.filteredFiles ? state.filteredFiles.findIndex(f => f.path === path) : -1;
      if (idxFiltered >= 0) {
        state.filteredFiles[idxFiltered].has_thumb = true;
        state.filteredFiles[idxFiltered].thumb_url = data.thumb_url;
      }

      const card = document.querySelector(`[data-path="${CSS.escape(path)}"]`);
      if (card) {
        card.dataset.hasAnimated = 'true';
        card.dataset.animatedUrl = data.animated_url || `/api/backup/thumbnails/animated?path=${encodeURIComponent(path)}&v=${Date.now()}`;
        const img = card.querySelector('img');
        if (img) {
          img.dataset.fallbackThumb = data.thumb_url;
        }
        syncCardAnimationState(card);
      }

      if (activeSlide.thumb) activeSlide.thumb = data.thumb_url;
      const activeThumbEl = document.querySelector('.fancybox__thumbs .is-nav-selected img, .fancybox__thumb.is-selected img');
      if (activeThumbEl) activeThumbEl.src = data.thumb_url;
    } catch (err) {
      showToast(err.message || 'Failed to set poster frame', 'error');
    }
  }

  let activeThumbChooserAbortCtrl = null;
  let thumbChooserDebounceTimer = null;

  async function openThumbnailChooser() {
    if (!state.fancyboxInstance) return;
    const activeSlide = state.fancyboxInstance.getSlide();
    if (!activeSlide || !activeSlide.customData) return;
    const { file, path } = activeSlide.customData;
    if (!file || file.type !== 'video') {
      showToast('Thumbnail chooser is only available for videos', 'info');
      return;
    }

    const activeVideo = document.querySelector('.fancybox__slide.is-selected video') ||
                        document.querySelector('.fancybox__container video');
    if (activeVideo && !activeVideo.paused) {
      activeVideo.pause();
    }
    const currentVidTime = activeVideo ? (activeVideo.currentTime || 0) : 0;

    state.thumbChooser = {
      file: file,
      path: path,
      duration: file.duration || 0,
      candidates: [],
      selectedTimestamp: currentVidTime,
      activeVideoEl: activeVideo,
      applying: false,
    };

    if (els.thumbChooserFileName) {
      els.thumbChooserFileName.textContent = file.name || 'video.mp4';
      els.thumbChooserFileName.title = file.name || '';
    }

    // Set initial preview display
    updateThumbChooserPreview(currentVidTime, file.thumb_url || null);

    // Initial scrubber setup
    if (els.thumbChooserScrubber) {
      els.thumbChooserScrubber.min = '0';
      els.thumbChooserScrubber.max = String(Math.max(1, file.duration || 60));
      els.thumbChooserScrubber.step = '0.1';
      els.thumbChooserScrubber.value = String(currentVidTime);
    }
    updateThumbChooserTimeDisplay(currentVidTime, file.duration || 0);

    // Render skeleton placeholders in grid
    const approxCandCount = file.duration ? Math.ceil(file.duration / 30) : 12;
    renderThumbChooserSkeletons(approxCandCount);
    if (els.thumbChooserCandCount) {
      els.thumbChooserCandCount.textContent = '(loading...)';
    }

    // Show modal
    if (els.thumbnailChooserModal) {
      els.thumbnailChooserModal.classList.remove('hidden');
      document.body.classList.add('modal-scroll-lock');
    }

    // Fetch candidates (every 30s)
    if (activeThumbChooserAbortCtrl) {
      activeThumbChooserAbortCtrl.abort();
    }
    activeThumbChooserAbortCtrl = new AbortController();

    try {
      const res = await fetch(`/api/backup/thumbnails/candidates?path=${encodeURIComponent(path)}&interval=30`, {
        signal: activeThumbChooserAbortCtrl.signal,
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Failed to fetch candidate frames');
      }
      const data = await res.json();
      state.thumbChooser.duration = data.duration || state.thumbChooser.duration || 1;
      state.thumbChooser.candidates = data.candidates || [];

      if (els.thumbChooserScrubber) {
        els.thumbChooserScrubber.max = String(state.thumbChooser.duration);
        els.thumbChooserScrubber.value = String(state.thumbChooser.selectedTimestamp);
      }
      updateThumbChooserTimeDisplay(state.thumbChooser.selectedTimestamp, state.thumbChooser.duration);

      renderThumbChooserCandidates(data.candidates);
    } catch (err) {
      if (err.name === 'AbortError') return;
      console.error('Error fetching candidates:', err);
      if (els.thumbChooserCandCount) {
        els.thumbChooserCandCount.textContent = '(error)';
      }
      showToast(err.message || 'Error loading candidate frames', 'error');
    }
  }

  function closeThumbnailChooser() {
    if (activeThumbChooserAbortCtrl) {
      activeThumbChooserAbortCtrl.abort();
      activeThumbChooserAbortCtrl = null;
    }
    if (els.thumbnailChooserModal) {
      els.thumbnailChooserModal.classList.add('hidden');
    }
    const anyModalOpen = document.querySelector('[id$="Modal"]:not(.hidden)');
    if (!anyModalOpen) {
      document.body.classList.remove('modal-scroll-lock');
    }
    if (els.thumbChooserCandCount) {
      els.thumbChooserCandCount.textContent = '(every 30s)';
    }
    if (els.thumbChooserPreviewVideo) {
      els.thumbChooserPreviewVideo.pause();
      els.thumbChooserPreviewVideo.removeAttribute('src');
      els.thumbChooserPreviewVideo.load();
      els.thumbChooserPreviewVideo.classList.add('hidden');
    }
    if (els.thumbChooserPreviewImg) {
      els.thumbChooserPreviewImg.classList.remove('hidden');
    }
    if (state.thumbChooser) {
      state.thumbChooser.applying = false;
    }
  }

  function renderThumbChooserSkeletons(approxCount = 12) {
    if (!els.thumbChooserGrid) return;
    let html = '';
    const count = Math.min(24, Math.max(4, approxCount));
    for (let i = 0; i < count; i++) {
      html += `
        <div class="thumb-cand-skeleton rounded-lg bg-slate-800/80 border border-slate-700/60 aspect-video animate-pulse flex items-center justify-center">
          <span class="text-slate-600 text-xs">...</span>
        </div>
      `;
    }
    els.thumbChooserGrid.innerHTML = html;
  }

  function renderThumbChooserCandidates(candidates) {
    if (!els.thumbChooserGrid) return;
    if (els.thumbChooserCandCount) {
      const len = candidates ? candidates.length : 0;
      els.thumbChooserCandCount.textContent = `(${len} frames • every 30s)`;
    }
    if (!candidates || candidates.length === 0) {
      els.thumbChooserGrid.innerHTML = '<div class="col-span-full py-4 text-center text-slate-500">No candidate frames available</div>';
      return;
    }

    els.thumbChooserGrid.innerHTML = '';
    candidates.forEach((cand) => {
      const tile = document.createElement('div');
      tile.className = 'thumb-cand-card relative rounded-lg overflow-hidden border-2 cursor-pointer transition select-none aspect-video bg-slate-950 group border-slate-800 hover:border-slate-600 active:scale-98';
      tile.dataset.timestamp = String(cand.timestamp);
      tile.dataset.url = cand.url;
      tile.title = `Frame at ${cand.time_str} (Tap to preview, tap again or double-tap to apply)`;

      tile.innerHTML = `
        <img src="${cand.url}" alt="${cand.time_str}" class="w-full h-full object-cover transition group-hover:scale-105 pointer-events-none" loading="lazy" />
        <span class="absolute bottom-1 right-1 px-1.5 py-0.5 rounded bg-black/80 font-mono text-[10px] text-slate-300 pointer-events-none">${cand.time_str}</span>
        <span class="cand-apply-badge hidden absolute top-1 right-1 px-1.5 py-0.5 rounded bg-amber-600 font-semibold text-[10px] text-white shadow pointer-events-none animate-in fade-in zoom-in-95">Tap to Apply ✓</span>
      `;

      let lastTapTime = 0;
      let lastTapX = 0;
      let lastTapY = 0;

      // Mobile touch double-tap detection
      tile.addEventListener('touchend', (e) => {
        const touch = e.changedTouches && e.changedTouches[0];
        if (!touch) return;
        const now = Date.now();
        const timeDiff = now - lastTapTime;
        const distDiff = Math.hypot(touch.clientX - lastTapX, touch.clientY - lastTapY);
        lastTapTime = now;
        lastTapX = touch.clientX;
        lastTapY = touch.clientY;

        if (timeDiff > 40 && timeDiff < 400 && distDiff < 45) {
          e.preventDefault();
          selectThumbChooserCandidate(cand.timestamp, cand.url, tile);
          applySelectedThumbnail();
        }
      });

      // Click / single-tap: selects frame. If already selected, 2nd tap immediately applies!
      tile.addEventListener('click', () => {
        const isAlreadySelected = state.thumbChooser &&
          Math.abs(state.thumbChooser.selectedTimestamp - cand.timestamp) < 0.05 &&
          tile.classList.contains('border-amber-500');

        selectThumbChooserCandidate(cand.timestamp, cand.url, tile);

        if (isAlreadySelected) {
          applySelectedThumbnail();
        }
      });

      tile.addEventListener('dblclick', () => {
        selectThumbChooserCandidate(cand.timestamp, cand.url, tile);
        applySelectedThumbnail();
      });

      els.thumbChooserGrid.appendChild(tile);
    });
  }

  function selectThumbChooserCandidate(timestamp, url, tileEl) {
    if (!state.thumbChooser) return;
    state.thumbChooser.selectedTimestamp = timestamp;
    if (els.thumbChooserScrubber) {
      els.thumbChooserScrubber.value = String(timestamp);
    }
    updateThumbChooserTimeDisplay(timestamp, state.thumbChooser.duration);
    updateThumbChooserPreview(timestamp, url);

    if (els.thumbChooserGrid) {
      els.thumbChooserGrid.querySelectorAll('.thumb-cand-card').forEach((c) => {
        c.classList.remove('border-amber-500', 'ring-2', 'ring-amber-500');
        c.classList.add('border-slate-800');
        const badge = c.querySelector('.cand-apply-badge');
        if (badge) badge.classList.add('hidden');
      });
      if (tileEl) {
        tileEl.classList.remove('border-slate-800');
        tileEl.classList.add('border-amber-500', 'ring-2', 'ring-amber-500');
        const badge = tileEl.querySelector('.cand-apply-badge');
        if (badge) badge.classList.remove('hidden');
      }
    }
  }

  function updateThumbChooserTimeDisplay(current, total) {
    const curStr = formatDuration(current);
    const totStr = formatDuration(total);
    if (els.thumbChooserTimeDisplay) {
      els.thumbChooserTimeDisplay.textContent = `${curStr} / ${totStr}`;
    }
    if (els.thumbChooserSelectedTime) {
      els.thumbChooserSelectedTime.textContent = curStr;
    }
  }

  function updateThumbChooserPreview(timestamp, imageUrl = null) {
    const activeVid = state.thumbChooser?.activeVideoEl;
    if (activeVid && (activeVid.src || activeVid.currentSrc)) {
      const srcUrl = activeVid.currentSrc || activeVid.src;
      if (els.thumbChooserPreviewVideo) {
        if (els.thumbChooserPreviewVideo.src !== srcUrl) {
          els.thumbChooserPreviewVideo.src = srcUrl;
        }
        els.thumbChooserPreviewVideo.classList.remove('hidden');
        if (els.thumbChooserPreviewImg) {
          els.thumbChooserPreviewImg.classList.add('hidden');
        }
        try {
          els.thumbChooserPreviewVideo.currentTime = Math.max(0, timestamp);
        } catch (e) {}
        return;
      }
    }

    if (els.thumbChooserPreviewImg) {
      els.thumbChooserPreviewImg.classList.remove('hidden');
      if (els.thumbChooserPreviewVideo) {
        els.thumbChooserPreviewVideo.classList.add('hidden');
      }
      if (imageUrl) {
        els.thumbChooserPreviewImg.src = imageUrl;
      } else if (state.thumbChooser?.path) {
        els.thumbChooserPreviewImg.src = `/api/backup/thumbnails/frame?path=${encodeURIComponent(state.thumbChooser.path)}&timestamp=${timestamp}`;
      }
    }
  }

  function onThumbChooserScrub() {
    if (!els.thumbChooserScrubber || !state.thumbChooser) return;
    const ts = parseFloat(els.thumbChooserScrubber.value) || 0;
    state.thumbChooser.selectedTimestamp = ts;
    updateThumbChooserTimeDisplay(ts, state.thumbChooser.duration);

    if (els.thumbChooserPreviewVideo && !els.thumbChooserPreviewVideo.classList.contains('hidden')) {
      try {
        els.thumbChooserPreviewVideo.currentTime = ts;
      } catch (e) {}
    } else {
      if (thumbChooserDebounceTimer) clearTimeout(thumbChooserDebounceTimer);
      thumbChooserDebounceTimer = setTimeout(() => {
        updateThumbChooserPreview(ts);
      }, 150);
    }

    if (els.thumbChooserGrid) {
      els.thumbChooserGrid.querySelectorAll('.thumb-cand-card').forEach((c) => {
        const cTs = parseFloat(c.dataset.timestamp);
        const matches = Math.abs(cTs - ts) < 0.2;
        c.classList.toggle('border-amber-500', matches);
        c.classList.toggle('ring-2', matches);
        c.classList.toggle('ring-amber-500', matches);
        c.classList.toggle('border-slate-800', !matches);
      });
    }
  }

  function useCurrentThumbChooserPosition() {
    if (!state.thumbChooser) return;
    const activeVideo = state.thumbChooser.activeVideoEl ||
                        document.querySelector('.fancybox__slide.is-selected video') ||
                        document.querySelector('.fancybox__container video');
    const currentVidTime = activeVideo ? (activeVideo.currentTime || 0) : 0;
    state.thumbChooser.selectedTimestamp = currentVidTime;
    if (els.thumbChooserScrubber) {
      els.thumbChooserScrubber.value = String(currentVidTime);
    }
    updateThumbChooserTimeDisplay(currentVidTime, state.thumbChooser.duration);
    updateThumbChooserPreview(currentVidTime);

    if (els.thumbChooserGrid) {
      els.thumbChooserGrid.querySelectorAll('.thumb-cand-card').forEach((c) => {
        const cTs = parseFloat(c.dataset.timestamp);
        const matches = Math.abs(cTs - currentVidTime) < 0.2;
        c.classList.toggle('border-amber-500', matches);
        c.classList.toggle('ring-2', matches);
        c.classList.toggle('ring-amber-500', matches);
        c.classList.toggle('border-slate-800', !matches);
      });
    }
  }

  async function applySelectedThumbnail() {
    if (!state.thumbChooser || state.thumbChooser.applying) return;
    const { file, path } = state.thumbChooser;
    if (!file || !path) return;

    state.thumbChooser.applying = true;
    const selectedTime = state.thumbChooser.selectedTimestamp || 0;
    const durStr = formatDuration(selectedTime);

    if (els.thumbChooserApplyBtn) {
      els.thumbChooserApplyBtn.disabled = true;
      els.thumbChooserApplyBtn.innerHTML = '<span>⏳</span> Applying...';
    }

    let canvasB64 = null;
    try {
      const v = els.thumbChooserPreviewVideo;
      if (v && !v.classList.contains('hidden') && v.videoWidth > 0 && v.readyState >= 2) {
        const canvas = document.createElement('canvas');
        canvas.width = v.videoWidth;
        canvas.height = v.videoHeight;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
        canvasB64 = canvas.toDataURL('image/jpeg', 0.85);
      }
    } catch (e) {
      canvasB64 = null;
    }

    try {
      const payload = { path: path, timestamp: selectedTime };
      if (canvasB64) payload.image_base64 = canvasB64;

      const res = await fetch('/api/backup/thumbnails/set-poster', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to update thumbnail');
      }

      showToast(`Thumbnail set to ${durStr}!`, 'success');

      file.has_thumb = true;
      file.thumb_url = data.thumb_url;

      const idxAll = state.allFiles ? state.allFiles.findIndex((f) => f.path === path) : -1;
      if (idxAll >= 0) {
        state.allFiles[idxAll].has_thumb = true;
        state.allFiles[idxAll].thumb_url = data.thumb_url;
      }
      const idxFiltered = state.filteredFiles ? state.filteredFiles.findIndex((f) => f.path === path) : -1;
      if (idxFiltered >= 0) {
        state.filteredFiles[idxFiltered].has_thumb = true;
        state.filteredFiles[idxFiltered].thumb_url = data.thumb_url;
      }

      const card = document.querySelector(`[data-path="${CSS.escape(path)}"]`);
      if (card) {
        card.dataset.hasAnimated = 'true';
        card.dataset.animatedUrl = data.animated_url || `/api/backup/thumbnails/animated?path=${encodeURIComponent(path)}&v=${Date.now()}`;
        const img = card.querySelector('img');
        if (img) {
          img.dataset.fallbackThumb = data.thumb_url;
        }
        syncCardAnimationState(card);
      }

      const activeSlide = state.fancyboxInstance?.getSlide();
      if (activeSlide && activeSlide.thumb) activeSlide.thumb = data.thumb_url;
      const activeThumbEl = document.querySelector('.fancybox__thumbs .is-nav-selected img, .fancybox__thumb.is-selected img');
      if (activeThumbEl) activeThumbEl.src = data.thumb_url;

      closeThumbnailChooser();
    } catch (err) {
      showToast(err.message || 'Failed to set thumbnail', 'error');
    } finally {
      if (state.thumbChooser) {
        state.thumbChooser.applying = false;
      }
      if (els.thumbChooserApplyBtn) {
        els.thumbChooserApplyBtn.disabled = false;
        els.thumbChooserApplyBtn.innerHTML = '<span>Apply Thumbnail</span>';
      }
    }
  }

  function openMoveModalFromLightbox(path, name) {
    const decodedPath = decodeURIComponent(path);
    const decodedName = decodeURIComponent(name);
    openMoveModal({ path: decodedPath, name: decodedName, kind: 'file' });
  }

  function openMediaInfoModalByPath(path) {
    const decodedPath = decodeURIComponent(path);
    const file = (state.allFiles && state.allFiles.find(f => f.path === decodedPath)) ||
                 (state.filteredFiles && state.filteredFiles.find(f => f.path === decodedPath));
    if (file) {
      openMediaInfoModal(file);
    } else {
      showToast('Could not load file metadata', 'error');
    }
  }

  function openMediaInfoModal(fileOrTarget) {
    if (!fileOrTarget) return;
    const path = fileOrTarget.path;
    const file = (state.allFiles && state.allFiles.find(f => f.path === path)) ||
                 (state.filteredFiles && state.filteredFiles.find(f => f.path === path)) ||
                 fileOrTarget;

    state.currentInfoFile = file;

    const isVid = file.type === 'video';
    const isImg = file.type === 'image';
    const resLabel = getResolutionLabel(file.width, file.height);
    const aspect = getAspectRatio(file.width, file.height);

    if (els.infoModalIcon) {
      els.infoModalIcon.textContent = isVid ? '🎬' : (isImg ? '🖼️' : '📄');
    }
    if (els.infoModalTitle) {
      els.infoModalTitle.textContent = isVid ? 'Video Details' : (isImg ? 'Image Details' : 'File Details');
    }
    if (els.infoModalThumb) {
      els.infoModalThumb.src = file.thumb_url || '';
    }
    if (els.infoModalFileName) {
      els.infoModalFileName.textContent = file.name || 'Unknown';
      els.infoModalFileName.title = file.name || '';
    }
    if (els.infoModalSizeBadge) {
      els.infoModalSizeBadge.textContent = file.size_formatted || (file.size ? formatBytes(file.size) : '0 B');
    }
    if (els.infoModalTypeBadge) {
      els.infoModalTypeBadge.textContent = (file.type || 'FILE').toUpperCase();
    }
    if (els.infoModalResBadge) {
      if (resLabel) {
        els.infoModalResBadge.textContent = resLabel;
        els.infoModalResBadge.classList.remove('hidden');
      } else {
        els.infoModalResBadge.classList.add('hidden');
      }
    }

    if (els.infoModalResolution) {
      if (file.width && file.height) {
        els.infoModalResolution.textContent = `${file.width} × ${file.height} px`;
      } else {
        els.infoModalResolution.textContent = 'Not probed';
      }
    }
    if (els.infoModalAspectRatio) {
      if (aspect) {
        els.infoModalAspectRatio.textContent = `Aspect Ratio: ${aspect}`;
      } else {
        els.infoModalAspectRatio.textContent = '';
      }
    }

    if (els.infoModalDuration) {
      if (file.duration) {
        els.infoModalDuration.textContent = `${formatDuration(file.duration)} (${Math.round(file.duration)}s)`;
      } else {
        els.infoModalDuration.textContent = isVid ? 'Unknown' : 'N/A';
      }
    }
    if (els.infoModalBitrate) {
      if (file.duration && file.size) {
        els.infoModalBitrate.textContent = `Bitrate: ${formatBitrate(file.size, file.duration)}`;
      } else {
        els.infoModalBitrate.textContent = '';
      }
    }

    if (els.infoModalVideoCodec) {
      if (file.video_codec) {
        els.infoModalVideoCodec.textContent = file.video_codec.toUpperCase();
      } else {
        els.infoModalVideoCodec.textContent = isVid ? 'Unknown' : 'N/A';
      }
    }
    if (els.infoModalFps) {
      if (file.fps) {
        els.infoModalFps.textContent = `${Number(file.fps).toFixed(file.fps % 1 === 0 ? 0 : 2)} fps`;
      } else {
        els.infoModalFps.textContent = '';
      }
    }

    if (els.infoModalAudioCodec) {
      if (file.audio_codec) {
        els.infoModalAudioCodec.textContent = file.audio_codec.toUpperCase();
      } else {
        els.infoModalAudioCodec.textContent = isVid ? 'None / Unknown' : 'N/A';
      }
    }
    if (els.infoModalContainer) {
      if (file.ext) {
        els.infoModalContainer.textContent = `${file.ext.toUpperCase()} container`;
      } else {
        els.infoModalContainer.textContent = '';
      }
    }

    if (els.infoModalCaptureTime) {
      els.infoModalCaptureTime.textContent = file.capture_time ? formatTimestamp(file.capture_time) : 'Not in EXIF / Metadata';
    }
    if (els.infoModalModifiedTime) {
      els.infoModalModifiedTime.textContent = file.mtime ? formatTimestamp(file.mtime) : 'Unknown';
    }
    if (els.infoModalPath) {
      els.infoModalPath.textContent = file.path || '';
    }

    if (els.infoModalPosterBtn) {
      if (isVid) {
        els.infoModalPosterBtn.classList.remove('hidden');
        els.infoModalPosterBtn.classList.add('flex');
      } else {
        els.infoModalPosterBtn.classList.add('hidden');
        els.infoModalPosterBtn.classList.remove('flex');
      }
    }

    if (els.mediaInfoModal) {
      els.mediaInfoModal.classList.remove('hidden');
    }
  }

  function closeMediaInfoModal() {
    if (els.mediaInfoModal) {
      els.mediaInfoModal.classList.add('hidden');
    }
    state.currentInfoFile = null;
  }

  function downloadMediaFile(target) {
    if (!target) return;
    const mediaUrl = target.media_url || `/api/backup/media?path=${encodeURIComponent(target.path)}`;
    const link = document.createElement('a');
    link.href = mediaUrl;
    link.download = target.name || 'media';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    showToast(`Downloading "${target.name || 'file'}"...`, 'info');
  }

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

  // Rename Flow
  function openRenameModal(path, name, kind = 'file') {
    state.itemToRename = {
      path: decodeURIComponent(path),
      name: decodeURIComponent(name),
      kind,
    };
    els.newFileNameInput.value = state.itemToRename.name;
    els.renameModal.classList.remove('hidden');
    setTimeout(() => {
      els.newFileNameInput.focus();
      const lastDot = state.itemToRename.name.lastIndexOf('.');
      if (lastDot > 0 && kind === 'file') {
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

    const isFile = state.itemToRename.kind === 'file';
    const endpoint = isFile ? '/api/backup/rename' : '/api/backup/folders/rename';
    const itemPath = state.itemToRename.path;
    const itemKind = state.itemToRename.kind;

    try {
      els.confirmRenameBtn.disabled = true;
      els.confirmRenameBtn.textContent = 'Saving...';

      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: itemPath,
          new_name: newName,
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to rename item');
      }

      showToast(`Renamed to "${newName}"`, 'success');
      closeRenameModal();

      if (isFile) {
        // Update in local arrays
        const file = state.allFiles.find(f => f.path === itemPath);
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
      } else {
        // Folder / ZIP rename: re-browse to reflect the changed subtree.
        loadFolder(state.currentPath, false);
      }
    } catch (err) {
      console.error(err);
      showToast(err.message || 'Error renaming item', 'error');
    } finally {
      els.confirmRenameBtn.disabled = false;
      els.confirmRenameBtn.textContent = 'Save Name';
    }
  }

  // Delete Flow
  function resetDeleteConfirmation() {
    if (els.deleteConfirmAckInput) els.deleteConfirmAckInput.checked = false;
    if (els.deleteConfirmAck) {
      els.deleteConfirmAck.classList.remove('hidden');
      els.deleteConfirmAck.classList.add('flex');
    }
    els.confirmDeleteBtn.disabled = true;
  }

  function openDeleteModal(path, name, sizeFormatted, kind = 'file') {
    state.itemToDelete = {
      path: decodeURIComponent(path),
      name: decodeURIComponent(name),
      size: sizeFormatted,
      kind,
    };
    els.deleteFileName.textContent = state.itemToDelete.name;
    els.deleteFileSize.textContent = state.itemToDelete.size || '';

    if (kind === 'folder') {
      els.deleteModalTitle.textContent = 'Permanently Delete Folder?';
      els.deleteModalSubtitle.textContent = 'This folder and everything inside it will be permanently deleted.';
      els.confirmDeleteBtn.textContent = 'Delete Folder';
    } else if (kind === 'zip') {
      els.deleteModalTitle.textContent = 'Delete ZIP Archive?';
      els.deleteModalSubtitle.textContent = 'This archive and its cached contents will be deleted.';
      els.confirmDeleteBtn.textContent = 'Delete Archive';
    } else {
      els.deleteModalTitle.textContent = 'Permanently Delete File?';
      els.deleteModalSubtitle.textContent = 'This action cannot be undone.';
      els.confirmDeleteBtn.textContent = 'Delete File';
    }

    resetDeleteConfirmation();
    els.deleteModal.classList.remove('hidden');
  }

  function closeDeleteModal() {
    els.deleteModal.classList.add('hidden');
    state.itemToDelete = null;
  }

  async function submitDelete() {
    if (!state.itemToDelete) return;

    if (els.deleteConfirmAckInput && !els.deleteConfirmAckInput.checked) {
      showToast('Tick the confirmation box to proceed', 'error');
      return;
    }

    if (state.itemToDelete.bulk) {
      try {
        els.confirmDeleteBtn.disabled = true;
        els.confirmDeleteBtn.textContent = 'Deleting...';
        const res = await fetch('/api/backup/bulk/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ paths: state.itemToDelete.paths }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed to delete');
        showToast(`Queued ${data.queued} deletion(s)`, 'success');
        closeDeleteModal();
        clearSelection();
        awaitLibraryOpsAndRefresh((data.tasks || []).map(t => t.task_id), state.currentPath);
      } catch (err) {
        showToast(err.message || 'Error deleting', 'error');
      } finally {
        els.confirmDeleteBtn.disabled = !(els.deleteConfirmAckInput && els.deleteConfirmAckInput.checked);
        els.confirmDeleteBtn.textContent = 'Delete';
      }
      return;
    }

    const kind = state.itemToDelete.kind;
    const endpoint = kind === 'file' ? '/api/backup/delete' : '/api/backup/folders/delete';
    const deletedPath = state.itemToDelete.path;

    try {
      els.confirmDeleteBtn.disabled = true;
      els.confirmDeleteBtn.textContent = 'Deleting...';

      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: deletedPath }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to delete');
      }

      if (kind === 'file') {
        showToast(`Deleted "${state.itemToDelete.name}"`, 'success');
        closeDeleteModal();
        state.allFiles = state.allFiles.filter(f => f.path !== deletedPath);
        if (state.fancyboxInstance) {
          Fancybox.close();
        }
        applyFiltersAndSort();
      } else {
        showToast(`Queued deletion of "${state.itemToDelete.name}"`, 'success');
        const delTaskId = data.task_id;
        closeDeleteModal();
        awaitLibraryOpsAndRefresh([delTaskId], state.currentPath);
      }
    } catch (err) {
      console.error(err);
      showToast(err.message || 'Error deleting', 'error');
    } finally {
      els.confirmDeleteBtn.disabled = !(els.deleteConfirmAckInput && els.deleteConfirmAckInput.checked);
      els.confirmDeleteBtn.textContent = 'Delete';
    }
  }

  // ==========================================
  // Library File/Folder Management (P1)
  // ==========================================

  // --- New Folder ---
  function openNewFolderModal() {
    els.newFolderNameInput.value = '';
    els.newFolderModal.classList.remove('hidden');
    setTimeout(() => els.newFolderNameInput.focus(), 50);
  }

  function closeNewFolderModal() {
    els.newFolderModal.classList.add('hidden');
  }

  async function submitNewFolder() {
    const name = els.newFolderNameInput.value.trim();
    if (!name) {
      showToast('Folder name cannot be empty', 'error');
      return;
    }
    try {
      els.confirmNewFolderBtn.disabled = true;
      const res = await fetch('/api/backup/folders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parent_path: state.currentPath, name }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to create folder');
      showToast(`Created folder "${name}"`, 'success');
      closeNewFolderModal();
      loadFolder(state.currentPath, false);
    } catch (err) {
      showToast(err.message || 'Error creating folder', 'error');
    } finally {
      els.confirmNewFolderBtn.disabled = false;
    }
  }

  // --- Context Menu ---
  function openContextMenu(x, y, target) {
    state.contextMenuTarget = target;
    const isVid = target && (target.type === 'video' || (target.kind === 'file' && target.ext && ['.mp4', '.mkv', '.webm', '.avi', '.mov', '.wmv', '.flv', '.m4v', '.ts'].includes(target.ext.toLowerCase())));
    const posterBtn = els.contextMenu ? els.contextMenu.querySelector('button[data-cm="set-poster"]') : null;
    if (posterBtn) {
      posterBtn.classList.toggle('hidden', !isVid);
      posterBtn.classList.toggle('flex', isVid);
    }
    const animBtn = els.contextMenu ? els.contextMenu.querySelector('button[data-cm="gen-animated"]') : null;
    if (animBtn) {
      animBtn.classList.toggle('hidden', !isVid);
      animBtn.classList.toggle('flex', isVid);
    }
    els.contextMenu.style.left = `${x}px`;
    els.contextMenu.style.top = `${y}px`;
    els.contextMenu.classList.remove('hidden');
    // Keep within viewport
    const rect = els.contextMenu.getBoundingClientRect();
    if (rect.right > window.innerWidth) els.contextMenu.style.left = `${window.innerWidth - rect.width - 8}px`;
    if (rect.bottom > window.innerHeight) els.contextMenu.style.top = `${window.innerHeight - rect.height - 8}px`;
  }

  function closeContextMenu() {
    els.contextMenu.classList.add('hidden');
    state.contextMenuTarget = null;
  }

  function runContextAction(action) {
    const target = state.contextMenuTarget;
    closeContextMenu();
    if (!target) return;
    if (action === 'move') openMoveModal(target);
    else if (action === 'rename') openRenameModal(target.path, target.name, target.kind);
    else if (action === 'delete') openDeleteModal(target.path, target.name, target.size_formatted || '', target.kind);
    else if (action === 'info') openMediaInfoModal(target);
    else if (action === 'download') downloadMediaFile(target);
    else if (action === 'set-poster') {
      const idx = state.filteredFiles ? state.filteredFiles.findIndex(f => f.path === target.path) : -1;
      if (idx >= 0) {
        openFancybox(idx);
        showToast("Scrub to desired frame and click 'Set Poster' (or press P)", 'info');
      }
    } else if (action === 'gen-animated') {
      generateAnimatedPreviewForTarget(target);
    }
  }

  // Trigger Animated Preview Generation for All Videos in Library
  async function triggerAllAnimatedPreviews() {
    const btns = [
      els.generateAllAnimatedBtn,
      els.triggerAllAnimWorkerBtn,
      els.triggerQueueAllAnimBtn,
      els.mobileGenerateAllAnimatedTrigger,
    ].filter(Boolean);
    try {
      btns.forEach(b => { b.disabled = true; });
      if (els.generateAllAnimatedBtnText) {
        els.generateAllAnimatedBtnText.textContent = 'Queueing...';
      }
      showToast('Scanning library and queuing videos for animated previews...', 'info');
      const res = await fetch('/api/backup/thumbnails/batch-animated', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ all_videos: true }),
      });
      if (res.ok) {
        const data = await res.json();
        showToast(data.message || `Queued ${data.queued} video(s) for animated preview generation`, 'success');
        pollSystemStatus(true);
      } else {
        const err = await res.json().catch(() => ({}));
        showToast(err.detail || 'Failed to queue animated preview generation', 'error');
      }
    } catch (err) {
      console.error('Error triggering all animated previews:', err);
      showToast('Failed to queue animated preview generation', 'error');
    } finally {
      btns.forEach(b => { b.disabled = false; });
      if (els.generateAllAnimatedBtnText) {
        els.generateAllAnimatedBtnText.textContent = 'All Videos';
      }
    }
  }

  // Trigger Animated Preview Generation for Selected Videos
  async function triggerSelectedAnimatedPreviews() {
    if (!state.selectedPaths || state.selectedPaths.size === 0) return;
    const selectedVideoPaths = Array.from(state.selectedPaths).filter(p => {
      const f = state.allFiles ? state.allFiles.find(item => item.path === p) : null;
      return f ? f.type === 'video' : /\.(mp4|mkv|mov|avi|webm|flv|wmv|m4v|3gp|ts)$/i.test(p);
    });
    if (selectedVideoPaths.length === 0) {
      showToast('No video files selected', 'info');
      return;
    }
    try {
      if (els.selectionAnimBtn) els.selectionAnimBtn.disabled = true;
      const res = await fetch('/api/backup/thumbnails/batch-animated', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths: selectedVideoPaths, priority: true }),
      });
      if (res.ok) {
        const data = await res.json();
        showToast(data.message || `Queued ${data.queued} video(s) for animated previews`, 'success');
        pollSystemStatus(true);
      } else {
        const err = await res.json().catch(() => ({}));
        showToast(err.detail || 'Failed to queue animated previews', 'error');
      }
    } catch (err) {
      console.error('Error triggering selected animated previews:', err);
      showToast('Failed to queue animated previews', 'error');
    } finally {
      if (els.selectionAnimBtn) els.selectionAnimBtn.disabled = false;
    }
  }

  // Trigger Animated Preview Generation for Current Folder
  async function triggerFolderAnimatedPreviews() {
    try {
      if (els.generateFolderAnimatedBtn) {
        els.generateFolderAnimatedBtn.disabled = true;
      }
      const res = await fetch('/api/backup/thumbnails/batch-animated', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder: state.currentPath || '', recursive: false }),
      });
      if (res.ok) {
        const data = await res.json();
        showToast(data.message || `Queued ${data.queued} video(s) for animated preview generation`, 'success');
        pollSystemStatus(true);
      } else {
        const err = await res.json().catch(() => ({}));
        showToast(err.detail || 'Failed to queue animated preview generation', 'error');
      }
    } catch (err) {
      console.error('Error triggering folder animated previews:', err);
      showToast('Failed to queue animated preview generation', 'error');
    } finally {
      if (els.generateFolderAnimatedBtn) {
        els.generateFolderAnimatedBtn.disabled = false;
      }
    }
  }

  // Generate Animated Preview for a Single Video Target
  async function generateAnimatedPreviewForTarget(target) {
    if (!target || !target.path) return;
    try {
      showToast(`Generating animated preview for ${target.name}...`, 'info');
      const res = await fetch('/api/backup/thumbnails/generate-animated', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: target.path }),
      });
      if (res.ok) {
        showToast(`Animated preview generated for ${target.name}`, 'success');
        const file = state.allFiles ? state.allFiles.find(f => f.path === target.path) : null;
        if (file) {
          file.has_animated = true;
          file.animated_url = `/api/backup/thumbnails/animated?path=${encodeURIComponent(target.path)}&t=${Date.now()}`;
        }
        const card = document.querySelector(`[data-path="${CSS.escape(target.path)}"]`);
        if (card) {
          card.dataset.hasAnimated = 'true';
          card.dataset.animatedUrl = file ? file.animated_url : `/api/backup/thumbnails/animated?path=${encodeURIComponent(target.path)}&t=${Date.now()}`;
          card.removeAttribute('data-anim-pending');
          syncCardAnimationState(card);
        }
      } else {
        const err = await res.json().catch(() => ({}));
        showToast(err.detail || 'Failed to generate animated preview', 'error');
      }
    } catch (err) {
      console.error('Error generating animated preview:', err);
      showToast('Failed to generate animated preview', 'error');
    }
  }

  // Tap-and-hold (touch) support: opens the same context menu as right-click.
  function attachLongPress(el, targetFn) {
    let timer = null;
    let startX = 0;
    let startY = 0;

    const cancel = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };

    el.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) return;
      const t = e.touches[0];
      startX = t.clientX;
      startY = t.clientY;
      cancel();
      timer = setTimeout(() => {
        timer = null;
        // Suppress the synthetic click that follows the touch so the menu
        // isn't closed instantly and the lightbox doesn't open.
        state.suppressClickUntil = Date.now() + 800;
        openContextMenu(t.clientX, t.clientY, targetFn());
        if (navigator.vibrate) {
          try { navigator.vibrate(10); } catch (err) { /* ignore */ }
        }
      }, 500);
    }, { passive: true });

    el.addEventListener('touchmove', (e) => {
      if (!timer) return;
      const t = e.touches[0];
      if (!t || Math.abs(t.clientX - startX) > 12 || Math.abs(t.clientY - startY) > 12) {
        cancel();
      }
    }, { passive: true });

    el.addEventListener('touchend', cancel);
    el.addEventListener('touchcancel', cancel);
  }

  // --- Move To... ---
  function openMoveModal(target) {
    state.moveItems = [target];
    els.moveItemName.textContent = target.name;
    state.movePickerPath = state.basePath || state.currentPath;
    els.moveModal.classList.remove('hidden');
    loadMoveFolders(state.movePickerPath);
  }

  function openBulkMove(items) {
    state.moveItems = items;
    els.moveItemName.textContent = `${items.length} items`;
    state.movePickerPath = state.basePath || state.currentPath;
    els.moveModal.classList.remove('hidden');
    loadMoveFolders(state.movePickerPath);
  }

  function closeMoveModal() {
    els.moveModal.classList.add('hidden');
    state.moveItems = [];
  }

  async function loadMoveFolders(path) {
    try {
      const res = await fetch(`/api/backup/folders?path=${encodeURIComponent(path)}`);
      const data = await res.json();
      state.movePickerPath = data.current_path || path;
      state.movePickerFolders = data.subfolders || [];
      renderMoveFolders();
    } catch (err) {
      showToast('Error loading folders', 'error');
    }
  }

  function renderMoveFolders() {
    els.moveCurrentPath.textContent = state.movePickerPath;
    els.moveCurrentPath.title = state.movePickerPath;
    els.moveFolderList.innerHTML = '';

    const dirs = (state.movePickerFolders || []).filter(s =>
      !(s.is_zip || (s.path && s.path.toLowerCase().endsWith('.zip')))
    );

    if (dirs.length === 0) {
      els.moveFolderList.innerHTML = `<div class="text-xs text-slate-500 px-2 py-3 text-center">No subfolders — choose this location</div>`;
      return;
    }

    dirs.forEach(s => {
      const btn = document.createElement('button');
      btn.className = 'flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs text-slate-200 hover:bg-slate-800 text-left transition';
      btn.innerHTML = `<span>📁</span><span class="truncate flex-1">${escapeHtml(s.name)}</span><span class="text-slate-500 shrink-0">›</span>`;
      btn.addEventListener('click', () => loadMoveFolders(s.path));
      els.moveFolderList.appendChild(btn);
    });
  }

  function movePickerUp() {
    const base = state.basePath;
    const cur = state.movePickerPath;
    if (!cur || cur === base) return;
    const parent = cur.substring(0, cur.lastIndexOf('/')) || base;
    loadMoveFolders(parent || base);
  }

  async function submitMove() {
    if (!state.moveItems.length) return;
    const paths = state.moveItems.map(t => t.path);
    const dest = state.movePickerPath;
    try {
      const res = await fetch('/api/backup/move/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths, destination_folder: dest }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Move failed');
      if (data.collision_count > 0) {
        state.pendingMove = { paths, destination_folder: dest };
        els.moveConflictCount.textContent = `${data.collision_count} item(s) already exist at the destination.`;
        els.moveConflictModal.classList.remove('hidden');
        return;
      }
      await doMove(paths, dest, 'auto_rename');
    } catch (err) {
      showToast(err.message || 'Error moving', 'error');
    }
  }

  async function doMove(paths, dest, policy) {
    try {
      const res = await fetch('/api/backup/move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths, destination_folder: dest, conflict_policy: policy }),
      });
      const data = await res.json();
      if (!res.ok) {
        const msg = (data.detail && data.detail.message) ? data.detail.message : (data.detail || 'Move failed');
        throw new Error(msg);
      }
      showToast(`Queued ${data.queued} move(s)`, 'success');
      els.moveConflictModal.classList.add('hidden');
      closeMoveModal();
      if (state.fancyboxInstance) {
        Fancybox.close();
      }
      awaitLibraryOpsAndRefresh((data.tasks || []).map(t => t.task_id), state.currentPath);
    } catch (err) {
      showToast(err.message || 'Error moving', 'error');
    }
  }

  // Wait for queued library operations to finish, then reload the current
  // folder so the UI reflects the move/delete/rename without a manual refresh.
  async function awaitLibraryOpsAndRefresh(taskIds, expectedPath) {
    const pending = new Set((taskIds || []).filter(Boolean));
    const deadline = Date.now() + 10 * 60 * 1000;
    while (pending.size && Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 800));
      try {
        const res = await fetch('/api/backup/library-ops/status');
        if (!res.ok) break;
        const data = await res.json();
        const activeId = data.active ? data.active.id : null;
        const queuedIds = new Set((data.queued || []).map(q => q.id));
        Array.from(pending).forEach(id => {
          if (id !== activeId && !queuedIds.has(id)) pending.delete(id);
        });
      } catch (e) {
        break;
      }
    }
    if (!expectedPath || state.currentPath === expectedPath) {
      loadFolder(state.currentPath, false);
    }
  }

  // --- Multi-select ---
  function selectedItems() {
    return state.filteredFiles.filter(f => state.selectedPaths.has(f.path));
  }

  function updateSelectionToolbar() {
    const n = state.selectedPaths.size;
    els.selectionCount.textContent = n;
    if (n > 0) els.selectionToolbar.classList.remove('hidden');
    else els.selectionToolbar.classList.add('hidden');
    updateSelectAllBtn();
  }

  function allFilteredSelected() {
    return state.filteredFiles.length > 0
      && state.filteredFiles.every(f => state.selectedPaths.has(f.path));
  }

  function updateSelectAllBtn() {
    if (!els.selectAllBtn) return;
    const hasFiles = state.filteredFiles.length > 0;
    els.selectAllBtn.classList.toggle('hidden', !hasFiles);
    els.selectAllBtn.textContent = allFilteredSelected() ? '☐ Deselect All' : '☑️ Select All';
    els.selectAllBtn.title = allFilteredSelected()
      ? 'Deselect every item in the current view'
      : 'Select every item in the current view';
  }

  // Select every item in the current filtered view, or clear when all are
  // already selected.
  function toggleSelectAll() {
    if (state.filteredFiles.length === 0) return;
    if (allFilteredSelected()) {
      state.selectedPaths.clear();
      state.lastSelectedIndex = null;
    } else {
      state.filteredFiles.forEach(f => state.selectedPaths.add(f.path));
      state.lastSelectedIndex = state.filteredFiles.length - 1;
    }
    updateSelectionToolbar();
    updateCardSelectStates();
  }

  function updateCardSelectStates() {
    document.querySelectorAll('.card-select').forEach(cb => {
      const idx = parseInt(cb.dataset.selectIdx, 10);
      const f = state.filteredFiles[idx];
      cb.checked = f ? state.selectedPaths.has(f.path) : false;
    });
  }

  function toggleSelect(path, index) {
    if (state.selectedPaths.has(path)) state.selectedPaths.delete(path);
    else state.selectedPaths.add(path);
    state.lastSelectedIndex = index;
    updateSelectionToolbar();
    updateCardSelectStates();
  }

  function rangeSelect(startIdx, endIdx) {
    const a = Math.min(startIdx, endIdx);
    const b = Math.max(startIdx, endIdx);
    for (let i = a; i <= b; i++) {
      const f = state.filteredFiles[i];
      if (f) state.selectedPaths.add(f.path);
    }
    updateSelectionToolbar();
    updateCardSelectStates();
  }

  function clearSelection() {
    state.selectedPaths.clear();
    state.lastSelectedIndex = null;
    updateSelectionToolbar();
    updateCardSelectStates();
  }

  // --- Bulk delete ---
  function openBulkDeleteModal() {
    const paths = Array.from(state.selectedPaths);
    state.itemToDelete = {
      path: null,
      name: `${paths.length} items`,
      size: '',
      kind: 'file',
      bulk: true,
      paths,
    };
    els.deleteFileName.textContent = `${paths.length} item${paths.length === 1 ? '' : 's'} selected`;
    els.deleteFileSize.textContent = '';
    els.deleteModalTitle.textContent = `Delete ${paths.length} item${paths.length === 1 ? '' : 's'}?`;
    els.deleteModalSubtitle.textContent = 'This will permanently delete the selected files and folders.';
    els.confirmDeleteBtn.textContent = `Delete ${paths.length} item${paths.length === 1 ? '' : 's'}`;
    resetDeleteConfirmation();
    els.deleteModal.classList.remove('hidden');
  }

  // --- Bulk rename ---
  function openBulkRenameModal() {
    state.bulkRenamePreview = [];
    els.bulkRenameCount.textContent = `${state.selectedPaths.size} file${state.selectedPaths.size === 1 ? '' : 's'}`;
    els.bulkRenamePreviewList.innerHTML = `<div class="text-xs text-slate-500 px-2 py-3 text-center">Configure a mode, then preview</div>`;
    els.bulkRenameModal.classList.remove('hidden');
    renderBulkRenameParams();
  }

  function closeBulkRenameModal() {
    els.bulkRenameModal.classList.add('hidden');
  }

  function renderBulkRenameParams() {
    const mode = els.bulkRenameMode.value;
    document.querySelectorAll('.bulk-param').forEach(el => el.classList.add('hidden'));
    const map = {
      prefix: 'bulkPrefixParams',
      numbering: 'bulkNumberingParams',
      base_counter: 'bulkBaseCounterParams',
      find_replace: 'bulkFindReplaceParams',
      case: 'bulkCaseParams',
    };
    const el = document.getElementById(map[mode]);
    if (el) {
      el.classList.remove('hidden');
      if (mode === 'numbering' || mode === 'base_counter') el.classList.add('flex');
    }
  }

  function collectBulkRenameParams() {
    const mode = els.bulkRenameMode.value;
    if (mode === 'prefix') return { prefix: document.getElementById('bulkPrefixInput').value.trim(), suffix: document.getElementById('bulkSuffixInput').value.trim() };
    if (mode === 'numbering') return {
      start: parseInt(document.getElementById('bulkNumStart').value, 10) || 1,
      step: parseInt(document.getElementById('bulkNumStep').value, 10) || 1,
      padding: parseInt(document.getElementById('bulkNumPadding').value, 10) || 0,
      position: document.getElementById('bulkNumPosition').value,
    };
    if (mode === 'base_counter') return {
      base: document.getElementById('bulkBaseName').value.trim(),
      start: parseInt(document.getElementById('bulkBaseStart').value, 10) || 1,
      padding: parseInt(document.getElementById('bulkBasePadding').value, 10) || 0,
    };
    if (mode === 'find_replace') return { find: document.getElementById('bulkFindInput').value, replace: document.getElementById('bulkReplaceInput').value };
    if (mode === 'case') return { case: document.getElementById('bulkCaseSelect').value };
    return {};
  }

  async function previewBulkRename() {
    const paths = Array.from(state.selectedPaths);
    if (!paths.length) return;
    try {
      const res = await fetch('/api/backup/bulk/rename/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths, mode: els.bulkRenameMode.value, params: collectBulkRenameParams() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to preview');
      state.bulkRenamePreview = data.items || [];
      if (!state.bulkRenamePreview.length) {
        els.bulkRenamePreviewList.innerHTML = `<div class="text-xs text-slate-500 px-2 py-3 text-center">Nothing to rename</div>`;
        return;
      }
      els.bulkRenamePreviewList.innerHTML = state.bulkRenamePreview.map(item => {
        const changed = item.new_name !== item.name;
        const conflictCls = item.conflict ? 'text-red-300' : (changed ? 'text-emerald-300' : 'text-slate-500');
        return `
          <div class="flex items-center gap-2 text-xs">
            <span class="truncate text-slate-400">${escapeHtml(item.name)}</span>
            <span class="shrink-0 text-slate-500">→</span>
            <span class="truncate ${conflictCls}">${escapeHtml(item.new_name)}</span>
          </div>
        `;
      }).join('');
    } catch (err) {
      showToast(err.message || 'Error previewing rename', 'error');
    }
  }

  async function applyBulkRename() {
    const paths = Array.from(state.selectedPaths);
    if (!paths.length) return;
    try {
      const res = await fetch('/api/backup/bulk/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths, mode: els.bulkRenameMode.value, params: collectBulkRenameParams() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to rename');
      showToast(`Queued ${data.queued} rename(s)`, 'success');
      closeBulkRenameModal();
      clearSelection();
      awaitLibraryOpsAndRefresh((data.tasks || []).map(t => t.task_id), state.currentPath);
    } catch (err) {
      showToast(err.message || 'Error renaming', 'error');
    }
  }

  async function bulkWebpOptimize() {
    const paths = Array.from(state.selectedPaths);
    if (!paths.length) return;
    try {
      const res = await fetch('/api/backup/bulk/webp-optimize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to queue WebP optimization');
      let msg = `Queued ${data.images_queued} image(s) for WebP`;
      if (data.folders_queued) msg += `, ${data.folders_queued} folder(s)`;
      if (data.skipped) msg += ` (${data.skipped} skipped)`;
      showToast(msg, 'success');
      clearSelection();
    } catch (err) {
      showToast(err.message || 'Error queueing WebP optimization', 'error');
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

  // Browser Animated Preview Cache Badge
  async function updateBrowserCacheBadge() {
    if (!els.browserCacheBadge) return;
    try {
      const stats = await getBrowserAnimCacheStats();
      if (!stats.supported) {
        els.browserCacheBadge.textContent = 'Not supported';
        return;
      }
      const countStr = `${stats.count} cached`;
      if (stats.usage && stats.usage > 0) {
        els.browserCacheBadge.textContent = `${countStr} (${formatBytes(stats.usage)})`;
      } else {
        els.browserCacheBadge.textContent = countStr;
      }
    } catch (_) {
      els.browserCacheBadge.textContent = '0 cached';
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

  let systemPollTimer = null;
  let isPollingSystem = false;

  // Unified System Status Polling
  async function pollSystemStatus(immediate = false) {
    if (isPollingSystem && !immediate) return;
    if (document.hidden && !immediate) {
      clearTimeout(systemPollTimer);
      systemPollTimer = setTimeout(pollSystemStatus, 30000);
      return;
    }

    isPollingSystem = true;
    let nextDelay = 6000;
    try {
      const res = await fetch('/api/system/status');
      if (res.ok) {
        const data = await res.json();
        if (data.transfers) {
          state.latestTransferData = data.transfers;
          renderTransferStatus(data.transfers);
        }
        if (data.library_ops) {
          state.latestLibraryOpsData = data.library_ops;
          renderLibraryOpsStatus(data.library_ops);
        }
        if (data.transcode) {
          state.latestTranscodeData = data.transcode;
          renderTranscodeStatus(data.transcode);
          updateCardsTranscodeStatus(data.transcode);
        }
        if (data.index) {
          state.indexStatus = data.index.status;
          state.latestIndexData = data.index;
          renderIndexStatus(data.index);
        }
        if (data.thumbnails) {
          state.latestThumbData = data.thumbnails;
          renderThumbnailStatus(data.thumbnails);
        }
        if (data.animated_thumbnails) {
          state.latestAnimThumbData = data.animated_thumbnails;
          renderAnimatedThumbStatus(data.animated_thumbnails);
          updateCardsAnimatedStatus(data.animated_thumbnails);
        }
        if (data.duplicate_hash) {
          state.latestDupHashData = data.duplicate_hash;
          renderDuplicateHashStatus(data.duplicate_hash);
        }
        if (data.webp) {
          state.latestWebpData = data.webp;
          renderWebpStatus(data.webp);
        }
        if (data.library_backup) {
          state.latestLibBackupData = data.library_backup;
          renderLibBackupStatus(data.library_backup);
        }
        if (data.zip) {
          state.latestZipData = data.zip;
          renderZipStatus(data.zip);
          handleZipStatusUpdate(data.zip);
        }
        if (data.batch_zip) {
          state.latestBatchZipData = data.batch_zip;
          renderBatchZipStatus(data.batch_zip);
        }
        if (data.batch_unzip) {
          state.latestBatchUnpackData = data.batch_unzip;
          renderBatchUnpackStatus(data.batch_unzip);
        }
        if (data.url_downloads) {
          state.latestUrlDownloadsData = data.url_downloads;
          renderUrlDownloadsStatus(data.url_downloads);
        }

        updateHeaderTaskBadge();

        if (data.is_active) {
          nextDelay = 2000;
        }
      }
    } catch (err) {
      nextDelay = 8000;
    } finally {
      isPollingSystem = false;
      clearTimeout(systemPollTimer);
      systemPollTimer = setTimeout(pollSystemStatus, nextDelay);
    }
  }

  // Page visibility awareness: wake up immediately when user returns to tab
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      clearTimeout(systemPollTimer);
      pollSystemStatus(true);
    }
  });

  function pollTranscodeStatus() {
    pollSystemStatus(true);
  }

  // Shared Header Task Badge Updater
  function updateHeaderTaskBadge() {
    if (!els.transferBadgeBtn) return;

    const trData = state.latestTransferData;
    const tData = state.latestTranscodeData;
    const thData = state.latestThumbData;
    const animData = state.latestAnimThumbData;
    const wData = state.latestWebpData;
    const idxData = state.latestIndexData;
    const bkData = state.latestLibBackupData;
    const zipData = state.latestZipData;
    const batchZipData = state.latestBatchZipData;
    const batchUnpackData = state.latestBatchUnpackData;
    const libOpsData = state.latestLibraryOpsData;
    const urlDlData = state.latestUrlDownloadsData;

    const trActive = trData && (trData.active || (trData.queued && trData.queued.length > 0));
    const libOpsActive = libOpsData && (libOpsData.active || (libOpsData.queued && libOpsData.queued.length > 0));
    const idxActive = idxData && idxData.status === 'crawling';
    const tActive = tData && (tData.active || (tData.queued && tData.queued.length > 0));
    const thActive = thData && thData.status === 'generating' && (thData.pending_count > 0);
    const animActive = animData && ((animData.status === 'generating') || (animData.total_queued > 0) || Boolean(animData.active));
    const wActive = wData && wData.status === 'converting' && (wData.pending_count > 0);
    const bkActive = bkData && (bkData.status === 'backing_up' || bkData.status === 'restoring');
    const zipActive = zipData && zipData.is_active;
    const batchZipActive = batchZipData && (batchZipData.status === 'running' || batchZipData.status === 'processing');
    const batchUnpackActive = batchUnpackData && (batchUnpackData.status === 'running' || batchUnpackData.status === 'processing');
    const urlDlActive = urlDlData && ((urlDlData.active_count || 0) > 0 || (urlDlData.queued_count || 0) > 0);

    const isAnyActive = trActive || libOpsActive || idxActive || bkActive || tActive || thActive || animActive || wActive || zipActive || batchZipActive || batchUnpackActive || urlDlActive;

    // Unified badge state shared by the desktop pill and the mobile header
    // one-tap tasks button (active+queued item count + descriptive tooltip).
    const taskSources = {
      transfers: trData,
      libraryOps: libOpsData,
      transcode: tData,
      animated: animData,
      thumbnails: thData,
      webp: wData,
      index: idxData,
      backup: bkData,
      zip: zipData,
      batchZip: batchZipData,
      batchUnpack: batchUnpackData,
      urlDownloads: urlDlData,
      uploads: {
        active: uploadState.activeUploads || 0,
        queued: uploadState.queue.filter((f) => f.status === 'pending').length,
        percent: uploadState.totalBytes
          ? Math.min(100, Math.round((uploadState.uploadedBytes / uploadState.totalBytes) * 100))
          : 0,
      },
    };
    const tasksSummary = computeTaskSummary(taskSources);
    const tasksLabel = describePrimaryTask(taskSources);
    const badgeText = formatTaskCount(tasksSummary.count);
    const badgeStatus = tasksSummary.active ? 'Active' : 'Idle';

    // Always keep the desktop button visible so the background tasks modal can
    // be opened anytime
    els.transferBadgeBtn.classList.remove('hidden');

    if (tasksSummary.active) {
      if (els.transferIcon) els.transferIcon.classList.add('hidden');
      if (els.transferSpinner) els.transferSpinner.classList.remove('hidden');
      els.transferBadgeBtn.classList.add('text-emerald-300', 'border-emerald-500/40');
      els.transferBadgeBtn.classList.remove('text-slate-300');
      els.transferBadgeBtn.title = `View background activity (${badgeStatus})${tasksLabel ? ` - ${tasksLabel}` : ''}`;
    } else {
      if (els.transferIcon) els.transferIcon.classList.remove('hidden');
      if (els.transferSpinner) els.transferSpinner.classList.add('hidden');
      els.transferBadgeBtn.classList.remove('text-emerald-300', 'border-emerald-500/40');
      els.transferBadgeBtn.classList.add('text-slate-300');
      els.transferBadgeBtn.title = `View background activity (${badgeStatus})${tasksLabel ? ` - ${tasksLabel}` : ''}`;
      // Keep transferDropdown open if the user opened it; do not auto-close
    }
    if (els.transferCountText) {
      els.transferCountText.textContent = badgeText;
      els.transferCountText.classList.toggle('hidden', !badgeText);
    }

    // Mobile header one-tap tasks button mirrors the same state.
    if (els.mobileTasksBtn) {
      if (tasksSummary.active) {
        if (els.mobileTasksIcon) els.mobileTasksIcon.classList.add('hidden');
        if (els.mobileTasksSpinner) els.mobileTasksSpinner.classList.remove('hidden');
      } else {
        if (els.mobileTasksIcon) els.mobileTasksIcon.classList.remove('hidden');
        if (els.mobileTasksSpinner) els.mobileTasksSpinner.classList.add('hidden');
      }
      els.mobileTasksBtn.title = `Background tasks & system status (${badgeStatus})${tasksLabel ? ` - ${tasksLabel}` : ''}`;
      if (els.mobileTasksCountBadge) {
        els.mobileTasksCountBadge.textContent = badgeText;
        els.mobileTasksCountBadge.classList.toggle('hidden', !badgeText);
      }
    }

    updateMobileControlsBadge();

    // Update tab badges and empty state in tasks modal
    const activeQueueCount = (trData && trData.active ? 1 : 0) +
      (trData && trData.queued ? trData.queued.length : 0) +
      (libOpsData && libOpsData.active ? 1 : 0) +
      (libOpsData && libOpsData.queued ? libOpsData.queued.length : 0) +
      (tData && tData.active ? 1 : 0) +
      (tData && tData.queued ? tData.queued.length : 0) +
      (animData && animData.active ? 1 : 0) +
      (animData && animData.queued ? animData.queued.length : 0) +
      urlDownloadsActivityCount(urlDlData);

    const tabBadgeActive = document.getElementById('tabBadgeActive');
    const activeQueuesEmptyState = document.getElementById('activeQueuesEmptyState');
    if (tabBadgeActive) {
      if (activeQueueCount > 0) {
        tabBadgeActive.textContent = activeQueueCount;
        tabBadgeActive.classList.remove('hidden');
      } else {
        tabBadgeActive.classList.add('hidden');
      }
    }
    if (activeQueuesEmptyState) {
      activeQueuesEmptyState.classList.toggle('hidden', activeQueueCount > 0);
    }

    const tabBadgeWorkers = document.getElementById('tabBadgeWorkers');
    if (tabBadgeWorkers) {
      const hData = state.latestHashData;
      const isAnyWorkerActive = idxActive || thActive || animActive || wActive || (hData && (hData.status === 'checking' || (hData.pending_count > 0)));
      if (isAnyWorkerActive) {
        tabBadgeWorkers.textContent = 'Active';
        tabBadgeWorkers.className = 'px-1.5 py-0.2 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 text-[10px] font-mono';
        tabBadgeWorkers.classList.remove('hidden');
      } else {
        tabBadgeWorkers.textContent = 'Idle';
        tabBadgeWorkers.className = 'px-1.5 py-0.2 rounded-full bg-slate-800 text-slate-400 border border-slate-700 text-[10px] font-mono';
        tabBadgeWorkers.classList.remove('hidden');
      }
    }
  }

  // Poll Cloud Transfers Status & Update UI
  function pollTransfersStatus() {
    pollSystemStatus(true);
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
      els.queuedTransfersList.innerHTML = queued.slice(0, sheetListSlice('queuedTransfers', queued.length, 10)).map(q => `
        <div class="flex items-center justify-between gap-2 p-1.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
          <span class="truncate text-slate-300 font-mono text-[11px] flex-1 min-w-0">${q.filename}</span>
          <span class="text-[10px] text-emerald-400 font-mono shrink-0">→ /${q.destination_folder}</span>
        </div>
      `).join('') + renderListExpander('queuedTransfers', queued.length, 10);
    } else {
      els.queuedTransfersContainer.classList.add('hidden');
      els.queuedTransfersList.innerHTML = '';
      if (els.queuedTransfersCount) els.queuedTransfersCount.textContent = '0';
    }
  }

  // Render Library Operations (queued move / delete / rename) in the tasks panel.
  const LIBOP_LABELS = { move: 'Moving', delete: 'Deleting', rename: 'Renaming' };
  const LIBOP_BADGES = { move: 'Move', delete: 'Delete', rename: 'Rename' };
  function renderLibraryOpsStatus(data) {
    if (!els.libraryOpsSection) return;
    const active = data.active;
    const queued = data.queued || [];
    const history = data.history || [];
    const undoCount = data.undo_count || 0;
    const isActive = !!active || queued.length > 0;

    els.libraryOpsSection.classList.toggle('hidden', !isActive);
    if (!isActive) return;

    const bn = (p) => (p ? String(p).split('/').filter(Boolean).pop() || p : '');
    if (els.libraryOpsSummary) {
      els.libraryOpsSummary.textContent = active
        ? `${LIBOP_LABELS[active.op] || 'Working'}${queued.length ? ` +${queued.length}` : ''}`
        : `${queued.length} queued`;
    }

    if (active) {
      els.activeLibraryOpBox.classList.remove('hidden');
      if (els.activeLibraryOpKind) els.activeLibraryOpKind.textContent = LIBOP_BADGES[active.op] || 'Op';
      if (els.activeLibraryOpName) els.activeLibraryOpName.textContent = bn(active.source_path) || 'item';
      if (els.activeLibraryOpTarget) {
        els.activeLibraryOpTarget.textContent = active.target_path ? `→ ${bn(active.target_path)}` : '';
      }
      const done = history.filter(h => h.status === 'completed').length;
      if (els.libraryOpsProgress) {
        els.libraryOpsProgress.textContent = `${done} done${undoCount ? ` · undo ${undoCount}` : ''}`;
      }
    } else {
      els.activeLibraryOpBox.classList.add('hidden');
    }

    if (queued.length) {
      els.queuedLibraryOpsContainer.classList.remove('hidden');
      if (els.queuedLibraryOpsCount) els.queuedLibraryOpsCount.textContent = queued.length;
      if (els.queuedLibraryOpsList) {
        els.queuedLibraryOpsList.innerHTML = queued.slice(0, sheetListSlice('queuedLibraryOps', queued.length, 10)).map(q => `
          <div class="flex items-center gap-2 p-1.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
            <span class="text-[9px] uppercase px-1 py-0.2 rounded bg-slate-800 text-slate-400 font-mono shrink-0">${LIBOP_BADGES[q.op] || 'Op'}</span>
            <span class="truncate text-slate-400 font-mono text-[11px] flex-1 min-w-0">${escapeHtml(String(q.id || '').slice(0, 8))}</span>
          </div>
        `).join('') + renderListExpander('queuedLibraryOps', queued.length, 10);
      }
    } else {
      els.queuedLibraryOpsContainer.classList.add('hidden');
      if (els.queuedLibraryOpsList) els.queuedLibraryOpsList.innerHTML = '';
    }
  }

  function bindUrlDownloadCancelButtons(container) {
    if (!container) return;
    container.querySelectorAll('[data-urldl-cancel]').forEach(btn => {
      btn.addEventListener('click', () => cancelUrlDownloadJob(btn.dataset.urldlCancel));
    });
  }

  // Mobile Controls & Settings sheet "Downloads" tab: form + full live job list.
  // The desktop Background Tasks modal URL Downloads section was removed when
  // the /downloads page became the canonical downloader surface, so this is the
  // only in-library render left.
  function renderUrlDownloadsStatus(data) {
    renderMobileUrlDownloadsStatus(data);
  }

  function renderMobileUrlDownloadsStatus(data) {
    const badge = els.mobileTabDownloadsBadge;
    if (badge) {
      const count = urlDownloadsActivityCount(data);
      badge.textContent = count;
      badge.classList.toggle('hidden', count === 0);
    }

    const listEl = els.mobileDlList;
    if (!listEl) return;
    if (els.mobileDlSummary) els.mobileDlSummary.textContent = urlDownloadsSummaryText(data);

    const html = urlDownloadRowsHtml(data, { historyLimit: 20 });
    if (!html) {
      listEl.innerHTML = `<div class="p-3 text-center text-[11px] text-slate-500 bg-slate-950/60 border border-slate-800 rounded-xl">No downloads yet</div>`;
      return;
    }
    listEl.innerHTML = html;
    bindUrlDownloadCancelButtons(listEl);
  }

  async function cancelUrlDownloadJob(jobId) {
    try {
      const res = await fetch('/api/backup/upload/url/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: jobId })
      });
      if (res.ok) {
        showToast('Download cancelled', 'info');
        pollSystemStatus(true);
      } else {
        const j = await res.json().catch(() => ({}));
        showToast('Cancel failed: ' + (j.detail || res.status), 'error');
      }
    } catch (err) {
      showToast('Cancel failed: ' + (err.message || 'Error'), 'error');
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
      const displayQueue = queued.slice(0, sheetListSlice('queuedTranscodes', queued.length, 15));
      els.queuedTranscodesList.innerHTML = displayQueue.map(q => `
        <div class="flex items-center justify-between gap-2 p-1.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
          <span class="truncate text-slate-300 font-mono text-[11px] flex-1 min-w-0">${q.filename}</span>
          <button onclick="window.libraryApp.cancelTranscode('${q.id}')" class="text-[10px] text-red-400 hover:text-red-300 px-1.5 py-0.5 bg-red-950/40 hover:bg-red-900/60 rounded border border-red-800/50 shrink-0">Cancel</button>
        </div>
      `).join('') + renderListExpander('queuedTranscodes', queued.length, 15);
    } else {
      els.queuedTranscodesContainer.classList.add('hidden');
      els.queuedTranscodesList.innerHTML = '';
      if (els.queuedTranscodesCount) els.queuedTranscodesCount.textContent = '0';
    }

    // Recent history
    if (history.length > 0) {
      els.transcodeHistoryList.innerHTML = history.slice(0, sheetListSlice('transcodeHistory', history.length, 8)).map(h => {
        const isSuccess = h.status === 'completed';
        const icon = isSuccess ? '✓' : '✕';
        const color = isSuccess ? 'text-emerald-400' : 'text-red-400';
        return `
          <div class="flex items-center justify-between gap-2 text-[10px] font-mono py-0.5 border-b border-slate-800/50 last:border-0">
            <span class="truncate text-slate-300 flex-1 min-w-0">${h.filename}</span>
            <span class="${color} shrink-0">${icon} ${h.status}</span>
          </div>
        `;
      }).join('') + renderListExpander('transcodeHistory', history.length, 8);
    } else {
      els.transcodeHistoryList.innerHTML = `<span class="text-slate-600 italic">No conversions yet</span>`;
    }
  }

  // Poll Thumbnail Status & Update UI
  function pollThumbnailStatus() {
    pollSystemStatus(true);
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

  // Render Animated Previews Queue & Background Worker Status
  function renderAnimatedThumbStatus(data) {
    if (!data) return;
    const isPaused = data.is_paused;
    const isGenerating = data.status === 'generating';
    const active = data.active;
    const queued = data.queued || [];

    // 1. Pane 1: Queue Controls & Active Item
    if (els.animSummaryText) {
      els.animSummaryText.textContent = isPaused ? 'Paused' : (isGenerating ? 'Generating' : 'Idle');
    }
    if (els.toggleAnimPauseBtn) {
      els.toggleAnimPauseBtn.textContent = isPaused ? 'Resume' : 'Pause';
      els.toggleAnimPauseBtn.className = isPaused 
        ? 'px-2.5 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-[10px] font-semibold transition cursor-pointer'
        : 'px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-violet-300 hover:text-white border border-slate-700 rounded-lg text-[10px] font-semibold transition cursor-pointer';
    }

    if (active) {
      if (els.activeAnimBox) els.activeAnimBox.classList.remove('hidden');
      if (els.activeAnimFilename) els.activeAnimFilename.textContent = active.name;
      const pct = typeof data.percent === 'number' ? data.percent : 0;
      if (els.activeAnimPercent) els.activeAnimPercent.textContent = `${pct.toFixed(0)}%`;
      if (els.activeAnimProgressBar) els.activeAnimProgressBar.style.width = `${pct}%`;
      if (els.activeAnimCount) els.activeAnimCount.textContent = `${data.queue_length || 0} queued`;
    } else {
      if (els.activeAnimBox) els.activeAnimBox.classList.add('hidden');
    }

    if (queued.length > 0) {
      if (els.queuedAnimContainer) els.queuedAnimContainer.classList.remove('hidden');
      if (els.queuedAnimCount) els.queuedAnimCount.textContent = data.queue_length || queued.length;
      if (els.queuedAnimList) {
        const animTotal = data.queue_length || queued.length;
        const displayQueue = queued.slice(0, sheetListSlice('queuedAnim', animTotal, 15));
        els.queuedAnimList.innerHTML = displayQueue.map(q => `
          <div class="flex items-center justify-between gap-2 p-1.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
            <span class="truncate text-slate-300 font-mono text-[11px] flex-1 min-w-0">${escapeHtml(q.name)}</span>
            <span class="text-[9px] uppercase px-1.5 py-0.5 rounded bg-violet-950/60 text-violet-300 border border-violet-500/30 font-mono shrink-0">Queued</span>
          </div>
        `).join('') + renderListExpander('queuedAnim', animTotal, 15);
      }
    } else {
      if (els.queuedAnimContainer) els.queuedAnimContainer.classList.add('hidden');
      if (els.queuedAnimList) els.queuedAnimList.innerHTML = '';
      if (els.queuedAnimCount) els.queuedAnimCount.textContent = '0';
    }

    // 2. Pane 2: Worker Card Controls & Progress
    if (els.animWorkerSummaryText) {
      els.animWorkerSummaryText.textContent = isPaused ? 'Paused' : (isGenerating ? 'Generating' : 'Idle');
    }
    if (els.toggleAnimWorkerPauseBtn) {
      els.toggleAnimWorkerPauseBtn.textContent = isPaused ? 'Resume' : 'Pause';
      els.toggleAnimWorkerPauseBtn.className = isPaused 
        ? 'px-2.5 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-[10px] font-semibold transition cursor-pointer shrink-0'
        : 'px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-violet-300 hover:text-white border border-slate-700 rounded-lg text-[10px] font-semibold transition cursor-pointer shrink-0';
    }
    if (els.animWorkerCurrentFile) {
      if (isGenerating && data.current_file) {
        els.animWorkerCurrentFile.textContent = data.current_file;
        els.animWorkerCurrentFile.title = data.current_file;
      } else if (isPaused) {
        els.animWorkerCurrentFile.textContent = 'Generation paused';
        els.animWorkerCurrentFile.title = '';
      } else {
        els.animWorkerCurrentFile.textContent = 'Idle';
        els.animWorkerCurrentFile.title = '';
      }
    }
    const pct = typeof data.percent === 'number' ? data.percent : 0;
    if (els.animWorkerPercentText) els.animWorkerPercentText.textContent = `${pct.toFixed(0)}%`;
    if (els.animWorkerProgressBar) els.animWorkerProgressBar.style.width = `${pct}%`;
    if (els.animWorkerCountsText) {
      const processed = (data.processed_count || 0).toLocaleString();
      const queuedCount = (data.total_queued || 0).toLocaleString();
      els.animWorkerCountsText.textContent = `${processed} done • ${queuedCount} in queue`;
    }
    if (els.animWorkerStatusLabel) {
      els.animWorkerStatusLabel.textContent = isPaused ? 'Paused' : (isGenerating ? 'Active' : 'Idle');
    }
  }

  // Toggle Animated Preview Worker Pause / Resume
  async function toggleAnimPause() {
    try {
      if (els.toggleAnimPauseBtn) els.toggleAnimPauseBtn.disabled = true;
      if (els.toggleAnimWorkerPauseBtn) els.toggleAnimWorkerPauseBtn.disabled = true;
      const res = await fetch('/api/backup/thumbnails/animated-pause', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        showToast(data.message || (data.is_paused ? 'Animated preview worker paused' : 'Animated preview worker resumed'), 'info');
        pollSystemStatus(true);
      }
    } catch (err) {
      console.error('Error toggling animated pause:', err);
      showToast('Failed to toggle animated preview pause', 'error');
    } finally {
      if (els.toggleAnimPauseBtn) els.toggleAnimPauseBtn.disabled = false;
      if (els.toggleAnimWorkerPauseBtn) els.toggleAnimWorkerPauseBtn.disabled = false;
    }
  }

  // Cancel All Queued Animated Previews
  async function cancelAllAnimPreviews() {
    try {
      if (els.cancelAllAnimBtn) els.cancelAllAnimBtn.disabled = true;
      const res = await fetch('/api/backup/thumbnails/animated-cancel', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        showToast(data.message || `Cancelled ${data.cancelled} animated preview(s)`, 'info');
        pollSystemStatus(true);
      }
    } catch (err) {
      console.error('Error cancelling animated previews:', err);
      showToast('Failed to cancel animated previews', 'error');
    } finally {
      if (els.cancelAllAnimBtn) els.cancelAllAnimBtn.disabled = false;
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
  function pollDuplicateHashStatus() {
    pollSystemStatus(true);
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
  function pollWebpStatus() {
    pollSystemStatus(true);
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
      const tot = data.total_images || 0;
      const pend = data.pending_count || 0;
      const opt = data.optimized_count || Math.max(0, tot - pend);
      const conv = data.converted_count || 0;
      if (pend === 0 && tot > 0) {
        if (conv > 0) {
          els.webpCountsText.textContent = `${tot} / ${tot} WebP (${conv} converted)`;
        } else {
          els.webpCountsText.textContent = `${tot} / ${tot} WebP`;
        }
      } else {
        if (conv > 0) {
          els.webpCountsText.textContent = `${opt} / ${tot} WebP (${conv} converted)`;
        } else {
          els.webpCountsText.textContent = `${opt} / ${tot} WebP`;
        }
      }
    }
    if (els.webpSavedText) {
      const sBytes = data.saved_bytes || 0;
      if (sBytes <= 0) {
        els.webpSavedText.textContent = 'Saved: 0 B';
      } else {
        els.webpSavedText.textContent = `Saved: ${data.saved_formatted || '0 B'}`;
      }
    }
  }

  // Poll Library Folder Snapshot Backup Status
  function pollLibBackupStatus() {
    pollSystemStatus(true);
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

  // Handle ZIP task completion and progression
  function handleZipStatusUpdate(data) {
    if (!data) return;

    // Check if extraction of current folder completed
    if (state.isZip && state.zipExtracting) {
      const currentTask = (data.tasks || []).find(t => t.path === state.currentPath && t.type === 'extract');
      if (currentTask) {
        if (els.zipExtractBanner) {
          els.zipExtractBanner.classList.remove('hidden');
          const pct = Math.round(currentTask.percent || 0);
          els.zipExtractPercent.textContent = `${pct}%`;
          els.zipExtractProgressBar.style.width = `${pct}%`;
          if (currentTask.message) els.zipExtractMsg.textContent = currentTask.message;
        }
        if (currentTask.status === 'completed') {
          state.zipExtracting = false;
          if (els.zipExtractBanner) els.zipExtractBanner.classList.add('hidden');
          loadFolder(state.currentPath, false);
        }
      } else {
        // Task completed or no longer running
        state.zipExtracting = false;
        if (els.zipExtractBanner) els.zipExtractBanner.classList.add('hidden');
      }
    }

    // Check compression progress for actively compressing folder
    if (state.compressingFolder) {
      const compTask = (data.tasks || []).find(t => t.path === state.compressingFolder && t.type === 'compress');
      if (compTask) {
        if (els.zipExtractBanner) {
          els.zipExtractBanner.classList.remove('hidden');
          const pct = Math.round(compTask.percent || 0);
          els.zipExtractPercent.textContent = `${pct}%`;
          els.zipExtractProgressBar.style.width = `${pct}%`;
          if (compTask.message) els.zipExtractMsg.textContent = compTask.message;
        }
        if (compTask.status === 'completed') {
          const targetZip = state.compressingTargetZip;
          state.compressingFolder = null;
          state.compressingTargetZip = null;
          if (els.zipExtractBanner) els.zipExtractBanner.classList.add('hidden');
          showToast('Folder replaced with ZIP archive! Opening...', 'success');
          loadFolder(targetZip, true, true);
        } else if (compTask.status === 'failed') {
          showToast(`Compression failed: ${compTask.error || compTask.message}`, 'error');
          state.compressingFolder = null;
          state.compressingTargetZip = null;
          if (els.zipExtractBanner) els.zipExtractBanner.classList.add('hidden');
        }
      } else {
        // Task completed before first poll or no longer in active list
        const targetZip = state.compressingTargetZip;
        state.compressingFolder = null;
        state.compressingTargetZip = null;
        if (els.zipExtractBanner) els.zipExtractBanner.classList.add('hidden');
        showToast('Compression finished! Opening ZIP archive...', 'success');
        loadFolder(targetZip, true, true);
      }
    }
  }

  function pollZipTasksStatus() {
    pollSystemStatus(true);
  }

  // Render ZIP Tasks in dropdown panel
  function renderZipStatus(data) {
    if (!els.zipTasksList) return;
    const tasks = data.tasks || [];
    const activeTasks = tasks.filter(t => t.status === 'running');
    if (els.zipTasksSummary) {
      els.zipTasksSummary.textContent = activeTasks.length > 0 ? `${activeTasks.length} active` : 'Idle';
    }

    if (tasks.length === 0) {
      els.zipTasksList.innerHTML = `<div class="text-[10px] text-slate-500 italic">No active zip operations</div>`;
      return;
    }

    els.zipTasksList.innerHTML = tasks.slice(0, 5).map(t => {
      const typeLabel = t.type === 'compress' ? 'Compress' : (t.type === 'unpack' ? 'Unpack' : (t.type === 'extract' ? 'Cache Extract' : 'Sync Back'));
      const statusColor = t.status === 'running' ? 'text-sky-300' : (t.status === 'completed' ? 'text-emerald-400' : 'text-red-400');
      return `
        <div class="bg-slate-950 p-2 rounded-lg border border-slate-800 text-[11px] flex flex-col gap-1">
          <div class="flex items-center justify-between">
            <span class="font-medium text-slate-200 truncate max-w-[180px]">${t.filename}</span>
            <span class="${statusColor} font-mono text-[10px]">${t.status === 'running' ? Math.round(t.percent) + '%' : t.status}</span>
          </div>
          <div class="w-full bg-slate-800 rounded-full h-1 overflow-hidden">
            <div class="bg-sky-500 h-1 rounded-full transition-all" style="width: ${t.percent || 0}%"></div>
          </div>
          <span class="text-[10px] text-slate-400 truncate">${typeLabel}: ${t.message || t.status}</span>
        </div>
      `;
    }).join('');
  }

  // Open Compress ZIP Modal
  async function openCompressZipModal() {
    if (!state.currentPath || state.isRoot || state.isZip) return;
    try {
      if (els.compressFolderZipBtn) {
        els.compressFolderZipBtn.disabled = true;
        if (els.zipBtnText) els.zipBtnText.textContent = 'Checking...';
      }
      const res = await fetch(`/api/backup/zip/check?folder_path=${encodeURIComponent(state.currentPath)}`);
      const check = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(check.detail || 'Server error checking folder eligibility', 'error');
        return;
      }
      if (!check.eligible) {
        showToast(check.reason || 'This folder cannot be compressed to ZIP', 'warning');
        return;
      }

      const folderName = state.currentPath.split('/').filter(Boolean).pop() || 'folder';
      if (els.compressFolderName) els.compressFolderName.textContent = folderName;
      if (els.compressImageCount) els.compressImageCount.textContent = `${check.image_count} image${check.image_count === 1 ? '' : 's'}`;
      if (els.compressTotalSize) els.compressTotalSize.textContent = check.total_size_formatted || '0 MB';
      if (els.compressTargetZip) els.compressTargetZip.textContent = `${folderName}.zip`;

      if (els.compressZipModal) {
        els.compressZipModal.classList.remove('hidden');
      }
    } catch (err) {
      console.error('Error checking compressibility:', err);
      showToast('Failed to check folder eligibility', 'error');
    } finally {
      if (els.compressFolderZipBtn) {
        els.compressFolderZipBtn.disabled = false;
        applyFiltersAndSort();
      }
    }
  }

  function closeCompressZipModal() {
    if (els.compressZipModal) els.compressZipModal.classList.add('hidden');
  }

  async function submitCompressZip() {
    if (!state.currentPath) return;
    const folderToCompress = state.currentPath;
    const folderName = folderToCompress.split('/').filter(Boolean).pop() || 'folder';
    try {
      if (els.confirmCompressZipBtn) els.confirmCompressZipBtn.disabled = true;
      const res = await fetch('/api/backup/zip/compress', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_path: folderToCompress }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Compression failed');
      }
      state.compressingFolder = folderToCompress;
      state.compressingTargetZip = data.target_zip || (folderToCompress + '.zip');
      closeCompressZipModal();
      showToast(data.message || 'Compressing folder to ZIP...', 'info');

      // Show compression progress banner
      if (els.zipExtractBanner) {
        els.zipExtractBanner.classList.remove('hidden');
        if (els.zipExtractMsg) els.zipExtractMsg.textContent = `Compressing ${folderName} to ZIP and replacing folder...`;
        if (els.zipExtractPercent) els.zipExtractPercent.textContent = '0%';
        if (els.zipExtractProgressBar) els.zipExtractProgressBar.style.width = '0%';
      }

      pollZipTasksStatus();
    } catch (err) {
      console.error('Error starting compression:', err);
      showToast(err.message || 'Failed to start compression', 'error');
    } finally {
      if (els.confirmCompressZipBtn) els.confirmCompressZipBtn.disabled = false;
    }
  }

  // Open Unpack ZIP Modal
  function openUnpackZipModal() {
    if (!state.currentPath || !state.isZip) return;
    const fileName = state.currentPath.split('/').filter(Boolean).pop() || 'archive.zip';
    const folderName = fileName.replace(/\.zip$/i, '');
    if (els.unpackZipFileName) els.unpackZipFileName.textContent = fileName;
    if (els.unpackZipSource) els.unpackZipSource.textContent = fileName;
    if (els.unpackZipTarget) els.unpackZipTarget.textContent = folderName;

    if (els.unpackZipModal) els.unpackZipModal.classList.remove('hidden');
  }

  function closeUnpackZipModal() {
    if (els.unpackZipModal) els.unpackZipModal.classList.add('hidden');
  }

  async function submitUnpackZip() {
    if (!state.currentPath || !state.isZip) return;
    try {
      if (els.confirmUnpackZipBtn) els.confirmUnpackZipBtn.disabled = true;
      const res = await fetch('/api/backup/zip/unpack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ zip_path: state.currentPath }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Unpack failed');
      }
      showToast(data.message || 'Unpacking started in background', 'info');
      closeUnpackZipModal();
      pollZipTasksStatus();
      if (data.target_folder) {
        setTimeout(() => loadFolder(data.target_folder, true), 1500);
      }
    } catch (err) {
      console.error('Error starting unpack:', err);
      showToast(err.message || 'Failed to start unpack', 'error');
    } finally {
      if (els.confirmUnpackZipBtn) els.confirmUnpackZipBtn.disabled = false;
    }
  }

  // Open Batch ZIP Modal
  async function openBatchZipModal() {
    if (!els.batchZipModal) return;
    els.batchZipModal.classList.remove('hidden');
    if (els.batchZipCandidatesList) {
      els.batchZipCandidatesList.innerHTML = `
        <div class="col-span-full py-12 flex flex-col items-center justify-center gap-2 text-slate-400">
          <span class="w-6 h-6 border-2 border-sky-400 border-t-transparent rounded-full animate-spin"></span>
          <span class="text-xs">Scanning library for candidate image folders...</span>
        </div>
      `;
    }
    if (els.confirmBatchZipBtn) els.confirmBatchZipBtn.disabled = true;

    try {
      const res = await fetch('/api/backup/zip/batch/candidates');
      if (!res.ok) throw new Error('Failed to fetch candidate folders');
      const data = await res.json();

      state.batchZipCandidates = data.candidates || [];
      state.batchZipSelectedPaths = new Set(state.batchZipCandidates.map(c => c.path));
      state.batchZipFilterQuery = '';
      if (els.batchZipFilterInput) els.batchZipFilterInput.value = '';

      if (els.batchZipCandidateCount) els.batchZipCandidateCount.textContent = data.total_candidates;
      if (els.batchZipImageCount) els.batchZipImageCount.textContent = data.total_images;
      if (els.batchZipTotalSize) els.batchZipTotalSize.textContent = data.total_size_formatted;

      renderBatchZipCandidates();
    } catch (err) {
      console.error('Error opening batch zip modal:', err);
      showToast('Could not load candidate folders', 'error');
      if (els.batchZipCandidatesList) {
        els.batchZipCandidatesList.innerHTML = `<div class="text-center py-8 text-xs text-rose-400">Error loading candidates: ${escapeHtml(err.message)}</div>`;
      }
    }
  }

  function closeBatchZipModal() {
    if (els.batchZipModal) els.batchZipModal.classList.add('hidden');
  }

  function renderBatchZipCandidates() {
    if (!els.batchZipCandidatesList) return;
    const q = (state.batchZipFilterQuery || '').toLowerCase().trim();
    const filtered = state.batchZipCandidates.filter(c => 
      c.name.toLowerCase().includes(q) || 
      (c.parent_name && c.parent_name.toLowerCase().includes(q)) ||
      c.path.toLowerCase().includes(q)
    );

    if (filtered.length === 0) {
      els.batchZipCandidatesList.innerHTML = `
        <div class="text-center py-8 text-xs text-slate-500">
          ${state.batchZipCandidates.length === 0 ? 'No qualifying image-only folders found in library.' : 'No candidates match your search filter.'}
        </div>
      `;
      if (els.batchZipSelectedBadge) els.batchZipSelectedBadge.textContent = `0 of ${state.batchZipCandidates.length} selected`;
      if (els.confirmBatchZipBtn) els.confirmBatchZipBtn.disabled = true;
      if (els.confirmBatchZipBtnText) els.confirmBatchZipBtnText.textContent = 'Compress Selected (0)';
      return;
    }

    const selectedCount = state.batchZipSelectedPaths.size;
    const totalCount = state.batchZipCandidates.length;

    if (els.batchZipSelectedBadge) {
      els.batchZipSelectedBadge.textContent = `${selectedCount} of ${totalCount} selected`;
    }
    if (els.confirmBatchZipBtn) {
      els.confirmBatchZipBtn.disabled = selectedCount === 0;
    }
    if (els.confirmBatchZipBtnText) {
      els.confirmBatchZipBtnText.textContent = `Compress Selected (${selectedCount})`;
    }
    if (els.batchZipSelectAllToggle) {
      els.batchZipSelectAllToggle.checked = selectedCount === totalCount && totalCount > 0;
      els.batchZipSelectAllToggle.indeterminate = selectedCount > 0 && selectedCount < totalCount;
    }

    els.batchZipCandidatesList.innerHTML = filtered.map(c => {
      const isChecked = state.batchZipSelectedPaths.has(c.path);
      return `
        <div class="flex items-center justify-between gap-3 p-2 rounded-xl hover:bg-slate-800/50 transition cursor-pointer group" data-path="${escapeHtml(c.path)}">
          <div class="flex items-center gap-2.5 min-w-0 flex-1">
            <input 
              type="checkbox" 
              class="batch-zip-item-checkbox rounded bg-slate-800 border-slate-700 text-sky-600 focus:ring-sky-500 cursor-pointer"
              data-path="${escapeHtml(c.path)}"
              ${isChecked ? 'checked' : ''}
            />
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-1.5 text-xs font-semibold text-white truncate">
                <span>📁</span>
                <span class="truncate">${escapeHtml(c.name)}</span>
              </div>
              <div class="text-[10px] text-slate-400 font-mono truncate">
                ${c.parent_name ? `${escapeHtml(c.parent_name)} / ` : ''}${escapeHtml(c.target_zip)}
              </div>
            </div>
          </div>
          <div class="flex items-center gap-2 shrink-0 text-right">
            <span class="text-[11px] px-2 py-0.5 rounded-full bg-slate-800 text-sky-300 font-mono text-[10px]">
              ${c.image_count} img${c.image_count === 1 ? '' : 's'}
            </span>
            <span class="text-[11px] text-slate-400 font-mono w-16">
              ${c.total_size_formatted}
            </span>
          </div>
        </div>
      `;
    }).join('');

    // Attach checkbox toggle events
    els.batchZipCandidatesList.querySelectorAll('.batch-zip-item-checkbox').forEach(cb => {
      cb.addEventListener('change', (e) => {
        const p = cb.getAttribute('data-path');
        if (cb.checked) {
          state.batchZipSelectedPaths.add(p);
        } else {
          state.batchZipSelectedPaths.delete(p);
        }
        renderBatchZipCandidates();
      });
    });

    // Clicking row toggles checkbox
    els.batchZipCandidatesList.querySelectorAll('div[data-path]').forEach(row => {
      row.addEventListener('click', (e) => {
        if (e.target.tagName === 'INPUT') return;
        const p = row.getAttribute('data-path');
        const cb = row.querySelector('.batch-zip-item-checkbox');
        if (cb) {
          cb.checked = !cb.checked;
          if (cb.checked) {
            state.batchZipSelectedPaths.add(p);
          } else {
            state.batchZipSelectedPaths.delete(p);
          }
          renderBatchZipCandidates();
        }
      });
    });
  }

  // Submit Batch ZIP
  async function submitBatchZip() {
    const selected = Array.from(state.batchZipSelectedPaths);
    if (selected.length === 0) return;

    try {
      if (els.confirmBatchZipBtn) els.confirmBatchZipBtn.disabled = true;
      const res = await fetch('/api/backup/zip/batch/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_paths: selected }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to start batch compression');
      }

      showToast(data.message || `Started batch compression for ${selected.length} folders`, 'info');
      closeBatchZipModal();
      pollBatchZipStatus();
    } catch (err) {
      console.error('Error starting batch zip:', err);
      showToast(err.message || 'Error starting batch compression', 'error');
    } finally {
      if (els.confirmBatchZipBtn) els.confirmBatchZipBtn.disabled = false;
    }
  }

  // Toggle Batch ZIP Pause / Resume
  async function toggleBatchZipPause() {
    try {
      const res = await fetch('/api/backup/zip/batch/pause', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        showToast(data.message || (data.status === 'paused' ? 'Batch compression paused' : 'Batch compression resumed'), 'info');
        pollBatchZipStatus();
      }
    } catch (err) {
      console.error(err);
      showToast('Error toggling batch compression pause', 'error');
    }
  }

  // Cancel Batch ZIP
  async function cancelBatchZip() {
    if (!confirm('Are you sure you want to cancel the remaining batch compression? Currently running folder will complete.')) return;
    try {
      const res = await fetch('/api/backup/zip/batch/cancel', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        showToast(data.message || 'Batch compression canceled', 'warning');
        pollBatchZipStatus();
      }
    } catch (err) {
      console.error(err);
      showToast('Error canceling batch compression', 'error');
    }
  }

  // Render and handle Batch ZIP status updates
  function renderBatchZipStatus(data) {
    if (!data) return;
    const prevData = state.latestBatchZipData;
    state.latestBatchZipData = data;

    const isRunning = (data.status === 'running' || data.status === 'paused');

    if (els.batchZipProgressBox) {
      if (isRunning) {
        els.batchZipProgressBox.classList.remove('hidden');
      } else if (data.status === 'completed' || data.status === 'canceled') {
        if (prevData && (prevData.status === 'running' || prevData.status === 'paused')) {
          els.batchZipProgressBox.classList.remove('hidden');
          setTimeout(() => {
            if (state.latestBatchZipData && state.latestBatchZipData.status !== 'running' && state.latestBatchZipData.status !== 'paused') {
              if (els.batchZipProgressBox) els.batchZipProgressBox.classList.add('hidden');
            }
          }, 4000);
        } else {
          els.batchZipProgressBox.classList.add('hidden');
        }
      } else {
        els.batchZipProgressBox.classList.add('hidden');
      }
    }

    // Toggle buttons
    if (els.toggleBatchZipPauseBtn) {
      if (isRunning) {
        els.toggleBatchZipPauseBtn.classList.remove('hidden');
        els.toggleBatchZipPauseBtn.textContent = data.is_paused ? 'Resume' : 'Pause';
      } else {
        els.toggleBatchZipPauseBtn.classList.add('hidden');
      }
    }
    if (els.cancelBatchZipBtn) {
      if (isRunning) {
        els.cancelBatchZipBtn.classList.remove('hidden');
      } else {
        els.cancelBatchZipBtn.classList.add('hidden');
      }
    }

    // Update progress UI
    if (isRunning || data.status === 'completed') {
      if (els.batchZipCurrentFolder) {
        els.batchZipCurrentFolder.textContent = data.current_folder_name 
          ? `[${data.current_index}/${data.total_folders}] ${data.current_folder_name}`
          : (data.status === 'completed' ? 'All folders completed' : 'Batch compression in progress...');
      }
      if (els.batchZipPercentText) {
        els.batchZipPercentText.textContent = `${Math.round(data.overall_percent || 0)}%`;
      }
      if (els.batchZipProgressBar) {
        els.batchZipProgressBar.style.width = `${Math.round(data.overall_percent || 0)}%`;
      }
      if (els.batchZipCountsText) {
        els.batchZipCountsText.textContent = `${data.succeeded} done, ${data.failed} failed of ${data.total_folders}`;
      }
      if (els.batchZipStatusText) {
        els.batchZipStatusText.textContent = data.is_paused ? 'Paused' : (data.status === 'completed' ? 'Done' : 'Compressing...');
      }
      if (els.zipTasksSummary) {
        els.zipTasksSummary.textContent = isRunning 
          ? `Batch: ${data.current_index}/${data.total_folders}`
          : 'Idle';
      }
    }

    // Detect batch completion transition
    if (prevData && (prevData.status === 'running' || prevData.status === 'paused') && data.status === 'completed') {
      showToast(`Batch compression finished! ${data.succeeded} converted to ZIP${data.failed ? `, ${data.failed} failed` : ''}.`, data.failed ? 'warning' : 'success');
      if (state.currentPath) {
        loadFolder(state.currentPath, false, true);
      }
    }
  }

  function pollBatchZipStatus() {
    pollSystemStatus(true);
  }

  // ===== Batch Unpack (Extract All ZIPs) =====
  async function openBatchUnpackModal() {
    if (!els.batchUnpackModal) return;
    els.batchUnpackModal.classList.remove('hidden');
    if (els.batchUnpackCandidatesList) {
      els.batchUnpackCandidatesList.innerHTML = `
        <div class="col-span-full py-12 flex flex-col items-center justify-center gap-2 text-slate-400">
          <span class="w-6 h-6 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin"></span>
          <span class="text-xs">Scanning library for ZIP archives...</span>
        </div>
      `;
    }
    if (els.confirmBatchUnpackBtn) els.confirmBatchUnpackBtn.disabled = true;

    try {
      const res = await fetch('/api/backup/zip/batch-unpack/candidates');
      if (!res.ok) throw new Error('Failed to fetch candidate archives');
      const data = await res.json();

      state.batchUnpackCandidates = data.candidates || [];
      state.batchUnpackSelectedPaths = new Set(state.batchUnpackCandidates.map(c => c.path));
      state.batchUnpackFilterQuery = '';
      if (els.batchUnpackFilterInput) els.batchUnpackFilterInput.value = '';

      if (els.batchUnpackCandidateCount) els.batchUnpackCandidateCount.textContent = data.total_candidates;
      if (els.batchUnpackFileCount) els.batchUnpackFileCount.textContent = data.total_files;
      if (els.batchUnpackTotalSize) els.batchUnpackTotalSize.textContent = data.total_size_formatted;

      renderBatchUnpackCandidates();
    } catch (err) {
      console.error('Error opening batch unpack modal:', err);
      showToast('Could not load candidate archives', 'error');
      if (els.batchUnpackCandidatesList) {
        els.batchUnpackCandidatesList.innerHTML = `<div class="text-center py-8 text-xs text-rose-400">Error loading candidates: ${escapeHtml(err.message)}</div>`;
      }
    }
  }

  function closeBatchUnpackModal() {
    if (els.batchUnpackModal) els.batchUnpackModal.classList.add('hidden');
  }

  function renderBatchUnpackCandidates() {
    if (!els.batchUnpackCandidatesList) return;
    const q = (state.batchUnpackFilterQuery || '').toLowerCase().trim();
    const filtered = state.batchUnpackCandidates.filter(c =>
      c.name.toLowerCase().includes(q) ||
      (c.parent_name && c.parent_name.toLowerCase().includes(q)) ||
      c.path.toLowerCase().includes(q)
    );

    if (filtered.length === 0) {
      els.batchUnpackCandidatesList.innerHTML = `
        <div class="text-center py-8 text-xs text-slate-500">
          ${state.batchUnpackCandidates.length === 0 ? 'No ZIP archives found in library.' : 'No candidates match your search filter.'}
        </div>
      `;
      if (els.batchUnpackSelectedBadge) els.batchUnpackSelectedBadge.textContent = `0 of ${state.batchUnpackCandidates.length} selected`;
      if (els.confirmBatchUnpackBtn) els.confirmBatchUnpackBtn.disabled = true;
      if (els.confirmBatchUnpackBtnText) els.confirmBatchUnpackBtnText.textContent = 'Extract Selected (0)';
      return;
    }

    const selectedCount = state.batchUnpackSelectedPaths.size;
    const totalCount = state.batchUnpackCandidates.length;

    if (els.batchUnpackSelectedBadge) {
      els.batchUnpackSelectedBadge.textContent = `${selectedCount} of ${totalCount} selected`;
    }
    if (els.confirmBatchUnpackBtn) {
      els.confirmBatchUnpackBtn.disabled = selectedCount === 0;
    }
    if (els.confirmBatchUnpackBtnText) {
      els.confirmBatchUnpackBtnText.textContent = `Extract Selected (${selectedCount})`;
    }
    if (els.batchUnpackSelectAllToggle) {
      els.batchUnpackSelectAllToggle.checked = selectedCount === totalCount && totalCount > 0;
      els.batchUnpackSelectAllToggle.indeterminate = selectedCount > 0 && selectedCount < totalCount;
    }

    els.batchUnpackCandidatesList.innerHTML = filtered.map(c => {
      const isChecked = state.batchUnpackSelectedPaths.has(c.path);
      return `
        <div class="flex items-center justify-between gap-3 p-2 rounded-xl hover:bg-slate-800/50 transition cursor-pointer group" data-path="${escapeHtml(c.path)}">
          <div class="flex items-center gap-2.5 min-w-0 flex-1">
            <input
              type="checkbox"
              class="batch-unpack-item-checkbox rounded bg-slate-800 border-slate-700 text-emerald-600 focus:ring-emerald-500 cursor-pointer"
              data-path="${escapeHtml(c.path)}"
              ${isChecked ? 'checked' : ''}
            />
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-1.5 text-xs font-semibold text-white truncate">
                <span>🗜️</span>
                <span class="truncate">${escapeHtml(c.name)}</span>
              </div>
              <div class="text-[10px] text-slate-400 font-mono truncate">
                ${c.parent_name ? `${escapeHtml(c.parent_name)} / ` : ''}${escapeHtml(c.path)}
              </div>
            </div>
          </div>
          <div class="flex items-center gap-2 shrink-0 text-right">
            <span class="text-[11px] px-2 py-0.5 rounded-full bg-slate-800 text-emerald-300 font-mono text-[10px]">
              ${c.total_files} files
            </span>
            <span class="text-[11px] text-slate-400 font-mono w-16">
              ${c.total_size_formatted}
            </span>
          </div>
        </div>
      `;
    }).join('');

    // Attach checkbox toggle events
    els.batchUnpackCandidatesList.querySelectorAll('.batch-unpack-item-checkbox').forEach(cb => {
      cb.addEventListener('change', () => {
        const p = cb.dataset.path;
        if (cb.checked) {
          state.batchUnpackSelectedPaths.add(p);
        } else {
          state.batchUnpackSelectedPaths.delete(p);
        }
        renderBatchUnpackCandidates();
      });
    });

    // Clicking a row toggles its checkbox
    els.batchUnpackCandidatesList.querySelectorAll('div[data-path]').forEach(row => {
      row.addEventListener('click', (e) => {
        if (e.target.closest('input')) return;
        const p = row.dataset.path;
        const cb = row.querySelector('.batch-unpack-item-checkbox');
        if (!cb) return;
        cb.checked = !cb.checked;
        if (cb.checked) {
          state.batchUnpackSelectedPaths.add(p);
        } else {
          state.batchUnpackSelectedPaths.delete(p);
        }
        renderBatchUnpackCandidates();
      });
    });
  }

  // Submit Batch Unpack
  async function submitBatchUnpack() {
    const selected = Array.from(state.batchUnpackSelectedPaths);
    if (selected.length === 0) return;
    try {
      if (els.confirmBatchUnpackBtn) els.confirmBatchUnpackBtn.disabled = true;
      const res = await fetch('/api/backup/zip/batch-unpack/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ zip_paths: selected }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Failed to start batch extraction');
      }
      const data = await res.json();
      closeBatchUnpackModal();
      showToast(data.message || `Extracting ${selected.length} archives...`, 'info');
      pollBatchUnpackStatus();
    } catch (err) {
      console.error('Error starting batch unpack:', err);
      showToast(err.message || 'Error starting batch extraction', 'error');
      if (els.confirmBatchUnpackBtn) els.confirmBatchUnpackBtn.disabled = false;
    }
  }

  // Toggle Batch Unpack Pause / Resume
  async function toggleBatchUnpackPause() {
    try {
      const res = await fetch('/api/backup/zip/batch-unpack/pause', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        showToast(data.message || (data.status === 'paused' ? 'Batch extraction paused' : 'Batch extraction resumed'), 'info');
        pollBatchUnpackStatus();
      }
    } catch (err) {
      console.error(err);
      showToast('Error toggling batch extraction pause', 'error');
    }
  }

  // Cancel Batch Unpack
  async function cancelBatchUnpack() {
    if (!confirm('Are you sure you want to cancel the remaining batch extraction? The currently running archive will complete.')) return;
    try {
      const res = await fetch('/api/backup/zip/batch-unpack/cancel', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        showToast(data.message || 'Batch extraction canceled', 'warning');
        pollBatchUnpackStatus();
      }
    } catch (err) {
      console.error(err);
      showToast('Error canceling batch extraction', 'error');
    }
  }

  // Render and handle Batch Unpack status updates
  function renderBatchUnpackStatus(data) {
    if (!data) return;
    const prevData = state.latestBatchUnpackData;
    state.latestBatchUnpackData = data;

    const isRunning = (data.status === 'running' || data.status === 'paused');

    if (els.batchUnpackProgressBox) {
      if (isRunning) {
        els.batchUnpackProgressBox.classList.remove('hidden');
      } else if (data.status === 'completed' || data.status === 'canceled') {
        if (prevData && (prevData.status === 'running' || prevData.status === 'paused')) {
          els.batchUnpackProgressBox.classList.remove('hidden');
          setTimeout(() => {
            if (state.latestBatchUnpackData && state.latestBatchUnpackData.status !== 'running' && state.latestBatchUnpackData.status !== 'paused') {
              if (els.batchUnpackProgressBox) els.batchUnpackProgressBox.classList.add('hidden');
            }
          }, 4000);
        } else {
          els.batchUnpackProgressBox.classList.add('hidden');
        }
      } else {
        els.batchUnpackProgressBox.classList.add('hidden');
      }
    }

    // Toggle buttons
    if (els.toggleBatchUnpackPauseBtn) {
      if (isRunning) {
        els.toggleBatchUnpackPauseBtn.classList.remove('hidden');
        els.toggleBatchUnpackPauseBtn.textContent = data.is_paused ? 'Resume' : 'Pause';
      } else {
        els.toggleBatchUnpackPauseBtn.classList.add('hidden');
      }
    }
    if (els.cancelBatchUnpackBtn) {
      if (isRunning) {
        els.cancelBatchUnpackBtn.classList.remove('hidden');
      } else {
        els.cancelBatchUnpackBtn.classList.add('hidden');
      }
    }

    // Update progress UI
    if (isRunning || data.status === 'completed') {
      if (els.batchUnpackCurrentZip) {
        els.batchUnpackCurrentZip.textContent = data.current_zip_name
          ? `[${data.current_index}/${data.total_zips}] ${data.current_zip_name}`
          : (data.status === 'completed' ? 'All archives extracted' : 'Batch extraction in progress...');
      }
      if (els.batchUnpackPercentText) {
        els.batchUnpackPercentText.textContent = `${Math.round(data.overall_percent || 0)}%`;
      }
      if (els.batchUnpackProgressBar) {
        els.batchUnpackProgressBar.style.width = `${Math.round(data.overall_percent || 0)}%`;
      }
      if (els.batchUnpackCountsText) {
        els.batchUnpackCountsText.textContent = `${data.succeeded} done, ${data.failed} failed of ${data.total_zips}`;
      }
      if (els.batchUnpackStatusText) {
        els.batchUnpackStatusText.textContent = data.is_paused ? 'Paused' : (data.status === 'completed' ? 'Done' : 'Extracting...');
      }
      if (els.zipTasksSummary) {
        els.zipTasksSummary.textContent = isRunning
          ? `Extract: ${data.current_index}/${data.total_zips}`
          : 'Idle';
      }
    }

    // Detect batch completion transition
    if (prevData && (prevData.status === 'running' || prevData.status === 'paused') && data.status === 'completed') {
      showToast(`Batch extraction finished! ${data.succeeded} archives extracted${data.failed ? `, ${data.failed} failed` : ''}.`, data.failed ? 'warning' : 'success');
      if (state.currentPath) {
        loadFolder(state.currentPath, false, true);
      }
    }
  }

  function pollBatchUnpackStatus() {
    pollSystemStatus(true);
  }

  // ==================== DELETE EMPTY FOLDERS MODAL & LOGIC ====================

  async function openEmptyFoldersModal() {
    if (els.transferDropdown) {
      els.transferDropdown.classList.add('hidden');
    }
    if (!els.emptyFoldersModal) return;
    els.emptyFoldersModal.classList.remove('hidden');
    if (els.emptyFoldersCandidatesList) {
      els.emptyFoldersCandidatesList.innerHTML = `
        <div class="col-span-full py-12 flex flex-col items-center justify-center gap-2 text-slate-400">
          <span class="w-6 h-6 border-2 border-rose-400 border-t-transparent rounded-full animate-spin"></span>
          <span class="text-xs">Scanning library for empty folders...</span>
        </div>
      `;
    }
    if (els.confirmEmptyFoldersBtn) els.confirmEmptyFoldersBtn.disabled = true;

    try {
      const res = await fetch('/api/backup/folders/empty/candidates');
      if (!res.ok) throw new Error('Failed to fetch candidate empty folders');
      const data = await res.json();

      state.emptyFoldersCandidates = data.candidates || [];
      state.emptyFoldersSelectedPaths = new Set(state.emptyFoldersCandidates.map(c => c.path));
      state.emptyFoldersFilterQuery = '';
      if (els.emptyFoldersFilterInput) els.emptyFoldersFilterInput.value = '';

      if (els.emptyFoldersCandidateCount) els.emptyFoldersCandidateCount.textContent = data.total_candidates;
      const leafCount = state.emptyFoldersCandidates.filter(c => c.is_leaf).length;
      if (els.emptyFoldersLeafCount) els.emptyFoldersLeafCount.textContent = leafCount;

      renderEmptyFoldersCandidates();
    } catch (err) {
      console.error('Error opening empty folders modal:', err);
      showToast('Could not load empty folders', 'error');
      if (els.emptyFoldersCandidatesList) {
        els.emptyFoldersCandidatesList.innerHTML = `<div class="text-center py-8 text-xs text-rose-400">Error loading empty folders: ${escapeHtml(err.message)}</div>`;
      }
    }
  }

  function closeEmptyFoldersModal() {
    if (els.emptyFoldersModal) els.emptyFoldersModal.classList.add('hidden');
  }

  function renderEmptyFoldersCandidates() {
    if (!els.emptyFoldersCandidatesList) return;
    const q = (state.emptyFoldersFilterQuery || '').toLowerCase().trim();
    const filtered = state.emptyFoldersCandidates.filter(c =>
      c.name.toLowerCase().includes(q) ||
      (c.parent_name && c.parent_name.toLowerCase().includes(q)) ||
      (c.relative_path && c.relative_path.toLowerCase().includes(q)) ||
      c.path.toLowerCase().includes(q)
    );

    if (filtered.length === 0) {
      els.emptyFoldersCandidatesList.innerHTML = `
        <div class="text-center py-8 text-xs text-slate-500">
          ${state.emptyFoldersCandidates.length === 0 ? 'No empty folders found in library.' : 'No candidates match your search filter.'}
        </div>
      `;
      if (els.emptyFoldersSelectedBadge) els.emptyFoldersSelectedBadge.textContent = `0 of ${state.emptyFoldersCandidates.length} selected`;
      if (els.confirmEmptyFoldersBtn) els.confirmEmptyFoldersBtn.disabled = true;
      if (els.confirmEmptyFoldersBtnText) els.confirmEmptyFoldersBtnText.textContent = 'Delete Selected (0)';
      return;
    }

    const selectedCount = state.emptyFoldersSelectedPaths.size;
    const totalCount = state.emptyFoldersCandidates.length;

    if (els.emptyFoldersSelectedBadge) {
      els.emptyFoldersSelectedBadge.textContent = `${selectedCount} of ${totalCount} selected`;
    }
    if (els.confirmEmptyFoldersBtn) {
      els.confirmEmptyFoldersBtn.disabled = selectedCount === 0;
    }
    if (els.confirmEmptyFoldersBtnText) {
      els.confirmEmptyFoldersBtnText.textContent = `Delete Selected (${selectedCount})`;
    }
    if (els.emptyFoldersSelectAllToggle) {
      els.emptyFoldersSelectAllToggle.checked = selectedCount === totalCount && totalCount > 0;
      els.emptyFoldersSelectAllToggle.indeterminate = selectedCount > 0 && selectedCount < totalCount;
    }

    els.emptyFoldersCandidatesList.innerHTML = filtered.map(c => {
      const isChecked = state.emptyFoldersSelectedPaths.has(c.path);
      return `
        <div class="flex items-center justify-between gap-3 p-2 rounded-xl hover:bg-slate-800/50 transition cursor-pointer group" data-path="${escapeHtml(c.path)}">
          <div class="flex items-center gap-2.5 min-w-0 flex-1">
            <input 
              type="checkbox" 
              class="empty-folder-item-checkbox rounded bg-slate-800 border-slate-700 text-rose-600 focus:ring-rose-500 cursor-pointer"
              data-path="${escapeHtml(c.path)}"
              ${isChecked ? 'checked' : ''}
            />
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-1.5 flex-wrap">
                <span class="text-xs font-semibold text-white group-hover:text-rose-300 transition truncate">${escapeHtml(c.name)}</span>
                ${c.is_leaf ? '<span class="px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 text-[9px] font-mono">leaf</span>' : ''}
              </div>
              <div class="text-[11px] text-slate-400 font-mono truncate">📁 ${escapeHtml(c.relative_path || c.name)}</div>
            </div>
          </div>
        </div>
      `;
    }).join('');

    // Attach click events on checkboxes
    els.emptyFoldersCandidatesList.querySelectorAll('.empty-folder-item-checkbox').forEach(cb => {
      cb.addEventListener('change', (e) => {
        const path = e.target.dataset.path;
        if (e.target.checked) {
          state.emptyFoldersSelectedPaths.add(path);
        } else {
          state.emptyFoldersSelectedPaths.delete(path);
        }
        renderEmptyFoldersCandidates();
      });
    });

    // Row click toggle
    els.emptyFoldersCandidatesList.querySelectorAll('[data-path]').forEach(row => {
      row.addEventListener('click', (e) => {
        if (e.target.classList.contains('empty-folder-item-checkbox')) return;
        const cb = row.querySelector('.empty-folder-item-checkbox');
        if (cb) {
          cb.checked = !cb.checked;
          cb.dispatchEvent(new Event('change'));
        }
      });
    });
  }

  async function submitDeleteEmptyFolders() {
    const selected = Array.from(state.emptyFoldersSelectedPaths);
    if (selected.length === 0) return;

    try {
      if (els.confirmEmptyFoldersBtn) {
        els.confirmEmptyFoldersBtn.disabled = true;
      }
      if (els.confirmEmptyFoldersBtnText) {
        els.confirmEmptyFoldersBtnText.textContent = 'Deleting...';
      }

      const res = await fetch('/api/backup/folders/empty/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_paths: selected }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Failed to delete empty folders');
      }

      const data = await res.json();
      closeEmptyFoldersModal();

      if (data.deleted_count > 0) {
        showToast(`Successfully deleted ${data.deleted_count} empty folder${data.deleted_count === 1 ? '' : 's'}!`, 'success');
      } else {
        showToast('No empty folders were deleted', 'info');
      }

      // Refresh current folder view & folder tree
      if (state.currentFolder) {
        loadFolderContents(state.currentFolder);
      } else {
        loadRootFolders();
      }
    } catch (err) {
      console.error('Error deleting empty folders:', err);
      showToast(err.message || 'Error deleting empty folders', 'error');
    } finally {
      if (els.confirmEmptyFoldersBtn) {
        els.confirmEmptyFoldersBtn.disabled = false;
      }
      if (els.confirmEmptyFoldersBtnText) {
        els.confirmEmptyFoldersBtnText.textContent = `Delete Selected (${state.emptyFoldersSelectedPaths.size})`;
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
          const card = Array.from(els.mediaGrid.querySelectorAll('.media-card')).find(c => c.dataset.path === item.source_path);
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
    Array.from(els.mediaGrid.querySelectorAll('.media-card')).forEach(card => {
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
          clearTimeout(globalSearchDebounceTimer);
          globalSearchDebounceTimer = setTimeout(() => performGlobalSearch(), 250);
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
  function initSheetDrag() {
    if (!window.matchMedia('(max-width: 767px)').matches) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const closeFns = {
      transferDropdown: () => setTransferDropdownOpen(false),
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

    // Tasks tab switcher
    const switchTasksTab = (tabName) => {
      const tabs = ['active', 'workers', 'tools'];
      const target = tabs.includes(tabName) ? tabName : 'active';
      try { localStorage.setItem('tasks_active_tab', target); } catch (e) {}

      const btnActive = document.getElementById('tasksTabBtnActive');
      const btnWorkers = document.getElementById('tasksTabBtnWorkers');
      const btnTools = document.getElementById('tasksTabBtnTools');
      const paneActive = document.getElementById('tasksPaneActive');
      const paneWorkers = document.getElementById('tasksPaneWorkers');
      const paneTools = document.getElementById('tasksPaneTools');

      const setTabStyle = (btn, isActive) => {
        if (!btn) return;
        if (isActive) {
          btn.className = 'tasks-tab-btn py-2.5 px-3 border-b-2 font-medium text-xs sm:text-sm flex items-center gap-1.5 transition text-emerald-400 border-emerald-400 cursor-pointer';
        } else {
          btn.className = 'tasks-tab-btn py-2.5 px-3 border-b-2 font-medium text-xs sm:text-sm flex items-center gap-1.5 transition text-slate-400 border-transparent hover:text-slate-200 cursor-pointer';
        }
      };

      setTabStyle(btnActive, target === 'active');
      setTabStyle(btnWorkers, target === 'workers');
      setTabStyle(btnTools, target === 'tools');

      if (paneActive) paneActive.classList.toggle('hidden', target !== 'active');
      if (paneWorkers) paneWorkers.classList.toggle('hidden', target !== 'workers');
      if (paneTools) paneTools.classList.toggle('hidden', target !== 'tools');
      if (target === 'tools') updateBrowserCacheBadge();
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
        const hasActiveQueues = (trData && (trData.active || (trData.queued && trData.queued.length > 0))) ||
          (libOpsData && (libOpsData.active || (libOpsData.queued && libOpsData.queued.length > 0))) ||
          (tData && (tData.active || (tData.queued && tData.queued.length > 0)));

        if (hasActiveQueues) {
          switchTasksTab('active');
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
          els.cleanDbBtn.innerHTML = '<span>⏳</span><span>Starting...</span>';
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

    // Filter Buttons (All, Videos, Images)
    const setFilter = (type) => {
      state.activeFilter = type;
      [els.filterAllBtn, els.filterVideosBtn, els.filterImagesBtn].forEach(btn => {
        if (btn) btn.className = 'px-2.5 sm:px-3 py-0.5 sm:py-1 rounded-md font-medium text-slate-400 hover:text-white transition whitespace-nowrap';
      });
      [els.mobileFilterAllBtn, els.mobileFilterVideosBtn, els.mobileFilterImagesBtn].forEach(btn => {
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
      }

      applyFiltersAndSort();
      updateMobileControlsBadge();
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
    initSheetDrag();
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
  }


  // ==========================================
  // BROWSER UPLOAD FEATURE
  // ==========================================
  const uploadState = {
    queue: [], // { file, relativePath, id, status, size, uploadedBytes, mtime }
    activeUploads: 0,
    maxConcurrent: 3,
    chunkSize: 5 * 1024 * 1024, // 5MB
    totalBytes: 0,
    uploadedBytes: 0,
    isCancelled: false,
    startTime: null,
    targetFolder: null
  };

  function initUploadEvents() {
    const dropzone = document.getElementById('globalDropzone');
    const uploadDropdownBtn = document.getElementById('uploadDropdownBtn');
    const uploadDropdownMenu = document.getElementById('uploadDropdownMenu');
    const uploadFilesBtn = document.getElementById('uploadFilesBtn');
    const uploadFolderBtn = document.getElementById('uploadFolderBtn');
    const hiddenFileInput = document.getElementById('hiddenUploadFileInput');
    const hiddenFolderInput = document.getElementById('hiddenUploadFolderInput');
    const startUploadBtn = document.getElementById('startUploadBtn');
    const cancelUploadReviewBtn = document.getElementById('cancelUploadReviewBtn');
    const cancelUploadReviewBtnTop = document.getElementById('cancelUploadReviewBtnTop');
    const cancelAllUploadsBtn = document.getElementById('cancelAllUploadsBtn');

    // Toggle dropdown
    if (uploadDropdownBtn) {
      uploadDropdownBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        uploadDropdownMenu.classList.toggle('hidden');
      });
    }
    // Outside click/touch listener to close dropdown
    const closeDropdownHandler = (e) => {
      if (uploadDropdownMenu && !uploadDropdownMenu.contains(e.target) && !uploadDropdownBtn.contains(e.target)) {
        uploadDropdownMenu.classList.add('hidden');
      }
    };
    document.addEventListener('click', closeDropdownHandler);
    document.addEventListener('touchstart', closeDropdownHandler, { passive: true });

    if (hiddenFileInput) {
      hiddenFileInput.addEventListener('change', (e) => {
        if (uploadDropdownMenu) uploadDropdownMenu.classList.add('hidden');
        handleFileInput(e);
      });
    }
    if (hiddenFolderInput) {
      hiddenFolderInput.addEventListener('change', (e) => {
        if (uploadDropdownMenu) uploadDropdownMenu.classList.add('hidden');
        handleFileInput(e);
      });
    }

    // Drag and Drop
    let dragCounter = 0;
    document.body.addEventListener('dragenter', (e) => {
      e.preventDefault();
      if (!state.currentPath || state.currentPath === state.basePath) return; // Disallow root upload for safety if preferred, or allow. We allow if state.currentPath exists.
      dragCounter++;
      if (dropzone) dropzone.classList.remove('hidden');
      const textEl = document.getElementById('dropzoneTargetText');
      if (textEl) textEl.textContent = 'Target: ' + state.currentPath;
    });
    
    document.body.addEventListener('dragleave', (e) => {
      e.preventDefault();
      dragCounter--;
      if (dragCounter <= 0) {
        dragCounter = 0;
        if (dropzone) dropzone.classList.add('hidden');
      }
    });
    
    document.body.addEventListener('dragover', (e) => {
      e.preventDefault();
    });
    
    document.body.addEventListener('drop', async (e) => {
      e.preventDefault();
      dragCounter = 0;
      if (dropzone) dropzone.classList.add('hidden');
      if (!state.currentPath || state.currentPath === state.basePath) return;
      
      const items = e.dataTransfer.items;
      if (!items) return;
      
      const filesToUpload = [];
      for (let i = 0; i < items.length; i++) {
        const item = items[i].webkitGetAsEntry();
        if (item) {
          await traverseFileTree(item, item.name, filesToUpload);
        }
      }
      if (filesToUpload.length > 0) {
        showUploadReviewModal(filesToUpload);
      }
    });

    if (startUploadBtn) startUploadBtn.addEventListener('click', startUploadBatch);
    if (cancelUploadReviewBtn) cancelUploadReviewBtn.addEventListener('click', closeUploadReviewModal);
    if (cancelUploadReviewBtnTop) cancelUploadReviewBtnTop.addEventListener('click', closeUploadReviewModal);
    if (cancelAllUploadsBtn) cancelAllUploadsBtn.addEventListener('click', cancelAllUploads);
  }

    // Download from URL (desktop Upload dropdown) now opens the dedicated
    // /downloads page, prefilled with the folder currently being browsed.
    const urlDownloadBtn = document.getElementById('urlDownloadBtn');
    if (urlDownloadBtn) {
      urlDownloadBtn.addEventListener('click', () => {
        uploadDropdownMenu.classList.add('hidden');
        const folder = state.currentPath || state.basePath || '';
        window.location.href = folder
          ? `/downloads?folder=${encodeURIComponent(folder)}`
          : '/downloads';
      });
    }

  function handleFileInput(e) {
    const rawFiles = e.target.files;
    if (!rawFiles || rawFiles.length === 0) {
      console.warn('handleFileInput: no files selected');
      return;
    }
    const files = Array.from(rawFiles);
    
    const filesToUpload = files.map(f => ({
      file: f,
      relativePath: f.webkitRelativePath || f.name,
      size: f.size || 0
    }));
    
    showUploadReviewModal(filesToUpload);
  }

  async function traverseFileTree(item, path, fileList) {
    if (item.isFile) {
      const file = await new Promise((resolve) => item.file(resolve));
      fileList.push({ file, relativePath: path, size: file.size });
    } else if (item.isDirectory) {
      const dirReader = item.createReader();
      const entries = await new Promise((resolve) => {
        const results = [];
        const readEntries = () => {
          dirReader.readEntries((entriesBatch) => {
            if (entriesBatch.length === 0) resolve(results);
            else {
              results.push(...entriesBatch);
              readEntries();
            }
          });
        };
        readEntries();
      });
      for (let entry of entries) {
        await traverseFileTree(entry, path + "/" + entry.name, fileList);
      }
    }
  }

  function showUploadReviewModal(files) {
    try {
      const target = state.currentPath || state.basePath || '/';
      uploadState.queue = files.map((item, idx) => ({
        ...item,
        id: 'upl_' + Date.now() + '_' + idx + '_' + Math.random().toString(36).slice(2, 7),
        status: 'pending',
        uploadedBytes: 0,
        mtime: (item.file && item.file.lastModified) ? item.file.lastModified : Date.now()
      }));
      
      uploadState.totalBytes = uploadState.queue.reduce((sum, f) => sum + (f.size || 0), 0);
      uploadState.targetFolder = target;
      
      const targetLabel = document.getElementById('uploadTargetFolderLabel');
      if (targetLabel) targetLabel.textContent = target;
      const totalFilesLabel = document.getElementById('uploadTotalFilesLabel');
      if (totalFilesLabel) totalFilesLabel.textContent = files.length.toLocaleString();
      const totalSizeLabel = document.getElementById('uploadTotalSizeLabel');
      if (totalSizeLabel) totalSizeLabel.textContent = formatBytes(uploadState.totalBytes);
      
      const warningEl = document.getElementById('uploadWarningText');
      const startBtn = document.getElementById('startUploadBtn');
      if (state.isZip) {
        if (warningEl) {
          warningEl.textContent = 'Cannot upload files directly into a virtual ZIP archive. Navigate to a folder first.';
          warningEl.classList.remove('hidden');
        }
        if (startBtn) startBtn.disabled = true;
      } else {
        if (warningEl) warningEl.classList.add('hidden');
        if (startBtn) startBtn.disabled = false;
      }

      const modal = document.getElementById('uploadReviewModal');
      if (modal) {
        modal.classList.remove('hidden');
        modal.classList.add('flex');
      }
    } catch (err) {
      console.error('Error in showUploadReviewModal:', err);
      showToast('Error displaying upload review: ' + (err.message || 'Unknown'), 'error');
    }
  }

  function closeUploadReviewModal() {
    const modal = document.getElementById('uploadReviewModal');
    if (modal) {
      modal.classList.add('hidden');
      modal.classList.remove('flex');
    }
    uploadState.queue = [];
    const fi = document.getElementById('hiddenUploadFileInput');
    if (fi) fi.value = '';
    const fo = document.getElementById('hiddenUploadFolderInput');
    if (fo) fo.value = '';
  }

  // Mobile Downloads tab: mirror the target-folder warning state and
  // disable Downloads when the current folder is a virtual ZIP archive.
  function syncMobileDownloadsUI() {
    const label = els.mobileDlTargetLabel;
    if (label) label.textContent = state.currentPath || state.basePath || '/';
    const warningEl = els.mobileDlWarningText;
    const startBtn = els.mobileDlStartBtn;
    if (state.isZip) {
      if (warningEl) {
        warningEl.textContent = 'Cannot download into a virtual ZIP archive. Navigate to a folder first.';
        warningEl.classList.remove('hidden');
      }
      if (startBtn) startBtn.disabled = true;
    } else {
      if (warningEl) warningEl.classList.add('hidden');
      if (startBtn) startBtn.disabled = false;
    }
  }

  // Same endpoint as the desktop dialog; the sheet stays open so the queued job
  // shows up in the list below with live progress.
  async function submitMobileUrlDownload() {
    const input = els.mobileDlUrlInput;
    const url = input ? input.value.trim() : '';
    if (!url) {
      showToast('Enter a video or playlist URL', 'error');
      return;
    }
    if (state.isZip) {
      showToast('Navigate to a real folder first', 'error');
      return;
    }
    const target = state.currentPath || state.basePath || '/';
    const startBtn = els.mobileDlStartBtn;
    if (startBtn) startBtn.disabled = true;
    try {
      const res = await fetch('/api/backup/upload/url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, destination_dir: target })
      });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast('Could not queue download: ' + (j.detail || res.status), 'error');
        return;
      }
      if (input) input.value = '';
      showToast('Download queued in background', 'success');
      pollSystemStatus(true);
    } catch (err) {
      showToast('Failed to queue download: ' + (err.message || 'Error'), 'error');
    } finally {
      if (startBtn) startBtn.disabled = false;
    }
  }

  async function startUploadBatch() {
    const collisionPolicy = document.getElementById('uploadCollisionPolicy').value;
    uploadState.collisionPolicy = collisionPolicy;
    
    const modal = document.getElementById('uploadReviewModal');
    if (modal) {
      modal.classList.add('hidden');
      modal.classList.remove('flex');
    }
    
    const count = uploadState.queue.length;
    showToast(`Starting upload for ${count} file${count === 1 ? '' : 's'}...`, 'info');

    const section = document.getElementById('browserUploadsSection');
    if (section) section.classList.remove('hidden');
    
    uploadState.isCancelled = false;
    uploadState.activeUploads = 0;
    uploadState.uploadedBytes = 0;
    uploadState.startTime = Date.now();
    
    updateUploadsUI();
    
    // Reflect the active upload in the header tasks badge.
    updateHeaderTaskBadge();
    
    processUploadQueue();
  }

  async function processUploadQueue() {
    if (uploadState.isCancelled) return;
    
    const pending = uploadState.queue.filter(f => f.status === 'pending');
    if (pending.length === 0 && uploadState.activeUploads === 0) {
      finishUploadBatch();
      return;
    }
    
    while (uploadState.activeUploads < uploadState.maxConcurrent && pending.length > 0) {
      const fileObj = pending.shift();
      uploadState.activeUploads++;
      fileObj.status = 'uploading';
      updateUploadsUI();
      uploadFile(fileObj).finally(() => {
        uploadState.activeUploads--;
        processUploadQueue();
      });
    }
  }

  async function uploadFile(fileObj) {
    const { file, id, relativePath, size, mtime } = fileObj;
    const totalChunks = Math.ceil(size / uploadState.chunkSize) || 1;
    
    try {
      for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex++) {
        if (uploadState.isCancelled || fileObj.status === 'cancelled') {
          await cancelBackendUpload(id);
          return;
        }
        
        const start = chunkIndex * uploadState.chunkSize;
        const end = Math.min(start + uploadState.chunkSize, size);
        const chunk = file.slice(start, end);
        
        let attempts = 0;
        let success = false;
        while (attempts < 3 && !success) {
          if (uploadState.isCancelled || fileObj.status === 'cancelled') return;
          try {
            const formData = new FormData();
            formData.append('upload_id', id);
            formData.append('chunk_index', chunkIndex);
            formData.append('total_chunks', totalChunks);
            formData.append('chunk', chunk, file.name);
            
            const res = await fetch('/api/backup/upload/chunk', { method: 'POST', body: formData });
            if (!res.ok) {
                const text = await res.text();
                let detail = text;
                try {
                  const j = JSON.parse(text);
                  if (j.detail) detail = j.detail;
                } catch (_) {}
                throw new Error(detail);
            }
            success = true;
          } catch (err) {
            attempts++;
            if (attempts >= 3) throw err;
            await new Promise(r => setTimeout(r, 1000 * attempts)); // backoff
          }
        }
        
        fileObj.uploadedBytes = end;
        updateUploadBatchStats();
        updateUploadsUI();
      }
      
      if (uploadState.isCancelled || fileObj.status === 'cancelled') {
        await cancelBackendUpload(id);
        return;
      }
      
      // Finalize
      const finalizeRes = await fetch('/api/backup/upload/finalize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          upload_id: id,
          original_filename: file.name,
          relative_path: relativePath,
          destination_dir: uploadState.targetFolder,
          collision_policy: uploadState.collisionPolicy,
          total_size: size,
          mtime: mtime
        })
      });
      
      if (!finalizeRes.ok) {
          const text = await finalizeRes.text();
          let detail = text;
          try {
            const j = JSON.parse(text);
            if (j.detail) detail = j.detail;
          } catch (_) {}
          throw new Error(detail);
      }
      
      fileObj.status = 'done';
    } catch (err) {
      console.error('Upload failed for', file.name, err);
      fileObj.status = 'error';
      showToast(`Failed to upload ${file.name}: ${err.message || 'Error'}`, 'error');
    }
    updateUploadsUI();
  }

  async function cancelBackendUpload(id) {
    try {
      await fetch('/api/backup/upload/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ upload_id: id })
      });
    } catch (err) { }
  }

  function cancelAllUploads() {
    uploadState.isCancelled = true;
    uploadState.queue.forEach(f => {
      if (f.status === 'uploading' || f.status === 'pending') {
        f.status = 'cancelled';
        if (f.status === 'uploading') cancelBackendUpload(f.id);
      }
    });
    updateUploadsUI();
    finishUploadBatch();
  }

  window.cancelSingleUpload = function(id) {
    const f = uploadState.queue.find(item => item.id === id);
    if (f) {
      if (f.status === 'pending') {
        f.status = 'cancelled';
        updateUploadsUI();
      } else if (f.status === 'uploading') {
        f.status = 'cancelled';
      }
    }
  };

  function updateUploadBatchStats() {
    let totalUploaded = 0;
    uploadState.queue.forEach(f => {
      if (f.status === 'done') totalUploaded += f.size;
      else if (f.status === 'uploading') totalUploaded += f.uploadedBytes;
    });
    uploadState.uploadedBytes = totalUploaded;
  }

  function updateUploadsUI() {
    const progressEl = document.getElementById('uploadBatchProgress');
    const barEl = document.getElementById('uploadBatchProgressBar');
    const speedEl = document.getElementById('uploadBatchSpeed');
    const statusEl = document.getElementById('uploadBatchStatus');
    const listEl = document.getElementById('activeUploadsList');
    
    if (!listEl) return;

    let totalUploaded = uploadState.uploadedBytes;
    const total = uploadState.totalBytes || 1;
    const percent = Math.floor((totalUploaded / total) * 100);
    
    if (progressEl) progressEl.textContent = percent + '%';
    if (barEl) barEl.style.width = percent + '%';
    
    const elapsedSecs = (Date.now() - uploadState.startTime) / 1000;
    const speed = elapsedSecs > 1 ? (totalUploaded / elapsedSecs) : 0;
    if (speedEl) speedEl.textContent = formatBytes(speed) + '/s';
    
    const doneCount = uploadState.queue.filter(f => f.status === 'done').length;
    if (statusEl) statusEl.textContent = `${doneCount} of ${uploadState.queue.length} files`;
    
    // Update header badge dynamically
    updateHeaderTaskBadge();

    // Render list
    listEl.innerHTML = uploadState.queue.slice().sort((a,b) => {
      const order = { 'uploading': 1, 'pending': 2, 'error': 3, 'done': 4, 'cancelled': 5 };
      return order[a.status] - order[b.status];
    }).map(f => {
      let statusIcon = '⏳';
      let rowClass = 'bg-slate-900 border-slate-700/50';
      if (f.status === 'uploading') { statusIcon = '⬆️'; rowClass = 'bg-emerald-950/20 border-emerald-900/30'; }
      if (f.status === 'done') { statusIcon = '✅'; rowClass = 'bg-emerald-950/40 border-emerald-800/40 opacity-70'; }
      if (f.status === 'error') { statusIcon = '❌'; rowClass = 'bg-red-950/30 border-red-900/40'; }
      if (f.status === 'cancelled') { statusIcon = '🚫'; rowClass = 'bg-slate-900/50 border-slate-800/50 opacity-50'; }
      
      const filePercent = f.size > 0 ? Math.floor((f.uploadedBytes / f.size) * 100) : 100;
      
      return `
        <div class="flex items-center gap-2 p-2 rounded-lg border text-xs ${rowClass}">
          <span class="shrink-0 w-4 text-center">${statusIcon}</span>
          <div class="flex-1 min-w-0 flex flex-col">
            <span class="text-slate-300 truncate font-medium">${f.relativePath}</span>
            ${f.status === 'uploading' ? `<div class="h-1 w-full bg-slate-800 rounded-full mt-1 overflow-hidden"><div class="h-full bg-emerald-500" style="width: ${filePercent}%"></div></div>` : ''}
          </div>
          <span class="shrink-0 text-slate-500 text-[10px] w-12 text-right">${formatBytes(f.size)}</span>
          ${(f.status === 'pending' || f.status === 'uploading') ? `
            <button onclick="window.cancelSingleUpload('${f.id}')" class="shrink-0 text-red-400 hover:text-red-300 px-1.5 py-0.5 rounded hover:bg-slate-800 transition">✕</button>
          ` : ''}
        </div>
      `;
    }).join('');
  }

  function finishUploadBatch() {
    setTimeout(() => {
      const section = document.getElementById('browserUploadsSection');
      if (section) section.classList.add('hidden');
      
      updateHeaderTaskBadge();
      
      const successCount = uploadState.queue.filter(f => f.status === 'done').length;
      if (successCount > 0) {
        showToast(`Successfully uploaded ${successCount} files`, 'success');
        if (state.currentPath === uploadState.targetFolder) {
          loadFolder(state.currentPath, false, false, true); // silent refresh
        }
      }
      
      uploadState.queue = [];
    }, 2000);
  }

  // Hook into events
  initUploadEvents();


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

