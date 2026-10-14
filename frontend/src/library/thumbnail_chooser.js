import { els, state } from './state.js';
import { formatDuration, showToast } from './utils.js';
import { syncCardAnimationState } from './animations.js';

// Video poster-frame chooser modal (candidate frames + scrubber).
// Extracted from the former monolithic library index.js.

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

export { activeThumbChooserAbortCtrl, applySelectedThumbnail, closeThumbnailChooser, onThumbChooserScrub, openThumbnailChooser, renderThumbChooserCandidates, renderThumbChooserSkeletons, selectThumbChooserCandidate, thumbChooserDebounceTimer, updateThumbChooserPreview, updateThumbChooserTimeDisplay, useCurrentThumbChooserPosition };
