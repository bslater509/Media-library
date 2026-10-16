import { icon } from '../shared/icons.js';
import { els, state } from './state.js';
import { escapeHtml, formatBitrate, formatBytes, formatDuration, formatTimestamp, getAspectRatio, getResolutionLabel, safeAttrEscape, showToast } from './utils.js';
import { pauseAllGalleryAnimations, resumeVisibleGalleryAnimations, syncCardAnimationState } from './animations.js';
import { parentDirectoryOf, toggleFavorite } from './gallery.js';
import { openMoveModal } from './management.js';

// Fullscreen lightbox viewer and media info modal.
// Extracted from the former monolithic library index.js.

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

  const slide = state.fancyboxInstance ? state.fancyboxInstance.getSlide() : null;
  if (slide && slide.customData && slide.customData.file) {
    const isFav = Boolean(slide.customData.file.is_favorite);
    container.querySelectorAll('.lightbox-fav-btn').forEach((btn) => {
      btn.classList.toggle('text-amber-400', isFav);
      btn.classList.toggle('fill-amber-400', isFav);
      btn.setAttribute('title', isFav ? 'Remove from Favorites (F)' : 'Add to Favorites (F)');
    });
  }

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
    const item = {
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
              <button type="button" onclick="window.libraryApp.convertLightboxVideoToX265()" class="lb-btn lightbox-x265-btn" title="Convert to x265 / HEVC (S)" aria-label="Convert to x265">
                ${icon('shrink')}
              </button>
            ` : ''}
            <button type="button" onclick="window.libraryApp.toggleCurrentLightboxFavorite()" class="lb-btn lightbox-fav-btn ${f.is_favorite ? 'text-amber-400 fill-amber-400' : ''}" title="${f.is_favorite ? 'Remove from Favorites (F)' : 'Add to Favorites (F)'}" aria-label="Toggle favorite">
              ${icon('star')}
            </button>
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
    // Fancybox sizes a video slide from its own `videoRatio` and falls back to
    // 16:9, which letterboxes portrait clips. Use the probed dimensions so each
    // clip is framed at its real shape (unprobed files keep the 16:9 default).
    if (isVid && f.width && f.height) {
      item.videoRatio = f.width / f.height;
    }
    return item;
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

function toggleCurrentLightboxFavorite() {
  if (!state.fancyboxInstance) return;
  const slide = state.fancyboxInstance.getSlide();
  if (!slide || !slide.customData) return;
  const path = slide.customData.path;
  toggleFavorite(path);
}

// ---------------------------------------------------------------------------
// In-place H.265 (x265) conversion
//
// The lightbox button queues a background HEVC re-encode of the file on
// screen, shows live progress in a floating panel over the viewer, and — once
// done — swaps the (now replaced) video back in without a manual reload.
// ---------------------------------------------------------------------------

let x265Watcher = null; // { path, startPath, timer, misses }

function buildX265Panel() {
  const container = document.querySelector('.fancybox__container');
  if (!container) return null;
  let panel = container.querySelector('.lb-x265-panel');
  if (!panel) {
    panel = document.createElement('div');
    panel.className = 'lb-x265-panel';
    panel.innerHTML = `
      <div class="lb-x265-panel__row">
        ${icon('shrink', 'lb-x265-panel__icon')}
        <span class="lb-x265-panel__title">Converting to x265…</span>
        <button type="button" class="lb-x265-panel__cancel" title="Cancel conversion" aria-label="Cancel conversion">${icon('x')}</button>
      </div>
      <div class="lb-x265-panel__track"><div class="lb-x265-panel__fill"></div></div>
      <div class="lb-x265-panel__meta">
        <span class="lb-x265-panel__pct">0%</span>
        <span class="lb-x265-panel__stats"></span>
      </div>
    `;
    panel.querySelector('.lb-x265-panel__cancel').addEventListener('click', cancelX265Conversion);
    container.appendChild(panel);
  }
  return panel;
}

function renderX265Panel(job) {
  const panel = buildX265Panel();
  if (!panel || !job) return;
  const pct = Math.max(0, Math.min(100, job.percent || 0));
  const fill = panel.querySelector('.lb-x265-panel__fill');
  const pctEl = panel.querySelector('.lb-x265-panel__pct');
  const statsEl = panel.querySelector('.lb-x265-panel__stats');
  const titleEl = panel.querySelector('.lb-x265-panel__title');
  if (fill) fill.style.width = `${pct}%`;
  if (pctEl) pctEl.textContent = `${pct.toFixed(1)}%`;
  if (titleEl) titleEl.textContent = `Converting to x265 · ${job.filename || ''}`;
  const bits = [];
  if (job.fps) bits.push(`${job.fps} fps`);
  if (job.speed) bits.push(job.speed);
  if (job.eta) bits.push(`ETA ${job.eta}`);
  if (statsEl) statsEl.textContent = bits.join(' · ');
}

function removeX265Panel() {
  const panel = document.querySelector('.lb-x265-panel');
  if (panel) panel.remove();
}

function stopX265Watch() {
  if (x265Watcher && x265Watcher.timer) clearTimeout(x265Watcher.timer);
  x265Watcher = null;
  removeX265Panel();
}

async function cancelX265Conversion() {
  const path = x265Watcher ? x265Watcher.path : null;
  if (!path) return;
  const res = await fetch('/api/backup/transcode/x265/cancel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  }).catch(() => null);
  stopX265Watch();
  if (res && res.ok) showToast('x265 conversion cancelled', 'info');
  else showToast('Conversion already finished or could not be cancelled', 'info');
}

async function convertLightboxVideoToX265() {
  const slide = state.fancyboxInstance ? state.fancyboxInstance.getSlide() : null;
  const file = slide && slide.customData ? slide.customData.file : null;
  const path = slide && slide.customData ? slide.customData.path : null;
  if (!path || !file || file.type !== 'video') {
    showToast('x265 conversion is only available for videos', 'info');
    return;
  }
  if (String(file.video_codec || '').toLowerCase() === 'hevc') {
    showToast('This video is already H.265/HEVC', 'info');
    return;
  }
  try {
    const res = await fetch('/api/backup/transcode/x265', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(data.detail || 'Could not start x265 conversion', 'error');
      return;
    }
    showToast('Converting to x265 in the background…', 'info');
    x265Watcher = { path, startPath: path, timer: null, misses: 0 };
    renderX265Panel({ filename: file.name, percent: 0 });
    pollX265Status();
  } catch (e) {
    showToast('Could not start x265 conversion', 'error');
  }
}

async function pollX265Status() {
  if (!x265Watcher) return;
  let job = null;
  try {
    const res = await fetch(
      `/api/backup/transcode/x265/status?path=${encodeURIComponent(x265Watcher.path)}`
    );
    if (res.ok) {
      const data = await res.json();
      job = data.job || null;
    }
  } catch (e) {
    /* transient network hiccup — retried below */
  }
  if (!x265Watcher) return;

  if (job) {
    x265Watcher.misses = 0;
    renderX265Panel(job);
    if (job.status === 'converting' || job.status === 'queued') {
      x265Watcher.timer = setTimeout(pollX265Status, 1000);
      return;
    }
    handleX265Finished(job);
    return;
  }

  x265Watcher.misses += 1;
  if (x265Watcher.misses > 30) {
    stopX265Watch();
    showToast('Lost track of the x265 conversion', 'error');
    return;
  }
  x265Watcher.timer = setTimeout(pollX265Status, 1500);
}

function syncCardAfterX265(oldPath, finalPath, job) {
  const card = Array.from(document.querySelectorAll('.media-card'))
    .find((c) => c.dataset.path === oldPath || c.dataset.path === finalPath);
  if (!card) return;
  card.dataset.path = finalPath;
  const nameEl = card.querySelector('.card-filename');
  if (nameEl) {
    const newName = finalPath.split('/').pop();
    nameEl.textContent = newName;
    nameEl.title = newName;
  }
  const sizeEl = card.querySelector('.card-filesize');
  if (sizeEl && job && job.size_after) {
    const txt = formatBytes(job.size_after);
    sizeEl.textContent = txt;
  }
  const img = card.querySelector('img');
  if (img) {
    img.dataset.fallbackThumb = `/api/backup/thumbnail?path=${encodeURIComponent(finalPath)}&v=${Date.now()}`;
    img.src = img.dataset.fallbackThumb;
  }
}

async function handleX265Finished(job) {
  const oldPath = x265Watcher.startPath;
  const finalPath = job.target_path || oldPath;
  const wasOpen = Boolean(state.fancyboxInstance);
  stopX265Watch();

  if (job.status === 'cancelled') {
    showToast('x265 conversion cancelled', 'info');
    return;
  }
  if (job.status !== 'completed') {
    showToast(job.error || 'x265 conversion failed', 'error');
    return;
  }
  if (job.method === 'skipped') {
    showToast(job.message || 'Already H.265/HEVC', 'info');
    return;
  }

  const saved = (job.size_before || 0) - (job.size_after || 0);
  const savedText = saved > 0 ? ` · saved ${formatBytes(saved)}` : '';
  showToast(`Converted to x265/HEVC${savedText}`, 'success');

  // Refresh the in-memory records so the grid, lightbox and any later action
  // all point at the replaced file.
  const newName = finalPath.split('/').pop();
  [state.allFiles, state.filteredFiles].forEach((list) => {
    if (!list) return;
    const f = list.find((x) => x.path === oldPath || x.path === finalPath);
    if (!f) return;
    f.path = finalPath;
    f.name = newName;
    f.ext = '.mp4';
    f.video_codec = 'hevc';
    f.needs_transcode = false;
    f.has_thumb = false;
    f.has_animated = false;
    if (job.size_after) f.size = job.size_after;
    f.media_url = `/api/backup/media?path=${encodeURIComponent(finalPath)}`;
    f.thumb_url = `/api/backup/thumbnail?path=${encodeURIComponent(finalPath)}&v=${Date.now()}`;
  });
  syncCardAfterX265(oldPath, finalPath, job);

  if (!wasOpen) return;

  // Re-open the viewer on the converted clip so the new bytes are loaded
  // (same filename for MP4 sources; a fresh .mp4 name otherwise).
  try {
    state.fancyboxInstance.close();
  } catch (e) {
    /* instance may already be tearing down */
  }
  const idx = state.filteredFiles ? state.filteredFiles.findIndex((f) => f.path === finalPath) : -1;
  if (idx >= 0) {
    setTimeout(() => openFancybox(idx), 180);
  }
}

export {
  LIGHTBOX_BAR_IDLE_MS,
  buildX265Panel,
  cancelX265Conversion,
  captureCurrentFrameAsPoster,
  closeMediaInfoModal,
  convertLightboxVideoToX265,
  downloadMediaFile,
  handleX265Finished,
  initLightboxBarIdle,
  lightboxBarHovered,
  lightboxBarIdleTimer,
  openFancybox,
  openMediaInfoModal,
  openMediaInfoModalByPath,
  openMoveModalFromLightbox,
  pollX265Status,
  revealLightboxBar,
  setLightboxVideoSpeed,
  setupVideoTouchSeek,
  showMobileSeekRipple,
  stopLightboxBarIdle,
  syncActiveLightboxVideo,
  syncLightboxBar,
  toggleCurrentLightboxFavorite,
  toggleLightboxVideoLoop,
};
