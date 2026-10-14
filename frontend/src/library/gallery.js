import { els, nameCollator, state } from './state.js';
import { formatDuration, getResolutionLabel } from './utils.js';
import { hasHoverPointer, observeAnimatedCard, syncCardAnimationState } from './animations.js';
import { loadFolder } from './folders.js';
import { gridGapPx, gridWidth, layoutJustifiedRows, mediaColumnCount, targetRowHeight, tileRatio } from './layout.js';
import { openFancybox, openMediaInfoModal } from './lightbox.js';
import { attachLongPress, openContextMenu, openDeleteModal, openRenameModal, rangeSelect, toggleSelect, updateCardSelectStates, updateSelectAllBtn, updateSelectionToolbar } from './management.js';
import { performGlobalSearch, setSearchScope } from './search.js';
import { updateMobileControlsBadge } from './theme.js';

// Gallery filtering, media card rendering and infinite scroll.
// Extracted from the former monolithic library index.js.

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

export { applyFiltersAndSort, parentDirectoryOf, renderCards, setupInfiniteScroll, updateCardsTranscodeStatus };
