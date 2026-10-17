import { getBrowserAnimCacheStats } from './service_worker.js';
import { els, persistAnimatedThumbs, state } from './state.js';
import { escapeHtml, formatBytes, shouldPlayAnimatedThumb, showToast } from './utils.js';
import { renderListExpander, sheetListSlice } from './sheet.js';
import { pollSystemStatus } from './tasks.js';

// Animated WebP preview playback and background generation queue.
// Extracted from the former monolithic library index.js.

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
  if (els.animWorkerCacheText) {
    // On-disk animated WebP cache: regenerable, so it is capped and trimmed.
    const used = data.cache_size_formatted || formatBytes(data.cache_bytes || 0);
    const cap = data.cache_max_size_formatted;
    els.animWorkerCacheText.textContent = cap ? `${used} of ${cap}` : used;
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

export { animEnqueueTimer, cancelAllAnimPreviews, enqueueAnimatedThumbnail, flushAnimEnqueue, generateAnimatedPreviewForTarget, hasHoverPointer, isElementInViewport, observeAnimatedCard, pauseAllGalleryAnimations, pendingAnimEnqueue, renderAnimatedThumbStatus, resumeVisibleGalleryAnimations, setAnimatedPreviews, setupAnimObserver, syncAnimatedPreviewsUI, syncCardAnimationState, toggleAnimPause, triggerAllAnimatedPreviews, triggerFolderAnimatedPreviews, triggerSelectedAnimatedPreviews, updateBrowserCacheBadge, updateCardsAnimatedStatus, updateCardsAnimatedThumbnails };
