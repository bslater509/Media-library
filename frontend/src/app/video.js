import { icon } from '../shared/icons.js';
import { currentVideo } from './config.js';
import { preloadVideoPlayer, seekLeftIndicator, seekRightIndicator, streamTypeBadge, toggleStreamModeBtn, videoContainer, videoPlayer } from './elements.js';
import { showToast, state } from './state.js';

// Video playback, gestures and controls auto-hide.
// Split out of the former monolithic sorter index.js.

function loadVideoSource(video, direct) {
const encodedId = encodeURIComponent(video.id);
const targetSrc = direct ? `/api/stream/${encodedId}` : `/api/transcode/${encodedId}`;

if (direct) {
  state.streamMode = "direct";
  streamTypeBadge.textContent = "Direct";
  streamTypeBadge.className = "bg-emerald-950 text-emerald-300 border border-emerald-800 px-1.5 py-0.5 rounded font-mono text-[10px]";
  toggleStreamModeBtn.textContent = "Transcode";
  toggleStreamModeBtn.classList.remove("hidden");
} else {
  state.streamMode = "transcode";
  streamTypeBadge.textContent = "ffmpeg";
  streamTypeBadge.className = "bg-amber-950 text-amber-300 border border-amber-800 px-1.5 py-0.5 rounded font-mono text-[10px]";
  toggleStreamModeBtn.textContent = "Direct";
  toggleStreamModeBtn.classList.remove("hidden");
}

if (videoPlayer.getAttribute("data-playing-id") !== video.id || videoPlayer.getAttribute("data-mode") !== state.streamMode) {
  videoPlayer.setAttribute("data-playing-id", video.id);
  videoPlayer.setAttribute("data-mode", state.streamMode);
  hideVideoControls();
  videoPlayer.src = targetSrc;
  videoPlayer.load();
  videoPlayer.play().catch((e) => {
    console.log("Autoplay waiting for user gesture:", e.message);
  });
}
}
// Next-Video Instant Preloader
function preloadNextVideo() {
if (!preloadVideoPlayer) return;
const nextIdx = state.currentIndex + 1;
if (nextIdx < state.queue.length) {
  const nextVideo = state.queue[nextIdx];
  const encodedId = encodeURIComponent(nextVideo.id);
  const nextSrc = !nextVideo.needs_transcode
    ? `/api/stream/${encodedId}`
    : `/api/transcode/${encodedId}`;

  if (preloadVideoPlayer.dataset.loadedId !== nextVideo.id) {
    preloadVideoPlayer.dataset.loadedId = nextVideo.id;
    preloadVideoPlayer.src = nextSrc;
    preloadVideoPlayer.load();
  }
} else {
  preloadVideoPlayer.removeAttribute("src");
  preloadVideoPlayer.dataset.loadedId = "";
}
}
// Fallback to ffmpeg transcode if direct playback fails
videoPlayer.addEventListener("error", (e) => {
const video = currentVideo();
if (video && state.streamMode === "direct") {
  console.warn("Direct stream error, falling back to on-the-fly ffmpeg transcode...");
  loadVideoSource(video, false);
} else if (video && state.streamMode === "transcode") {
  console.error("Playback error on transcoded stream for", video.filename);
  showToast(`Playback failed for "${video.filename}"`, "error");
}
});
// Touch Gestures on Video Player
function setupVideoGestures() {
if (!videoContainer) return;

let lastTapTime = 0;
let touchStartX = 0;
let touchStartY = 0;
let touchStartTime = 0;

videoContainer.addEventListener("touchstart", (e) => {
  if (e.touches.length !== 1) return;
  const touch = e.touches[0];
  touchStartX = touch.clientX;
  touchStartY = touch.clientY;
  touchStartTime = Date.now();
}, { passive: true });

videoContainer.addEventListener("touchend", (e) => {
  if (e.changedTouches.length !== 1) return;
  const touch = e.changedTouches[0];
  const deltaX = touch.clientX - touchStartX;
  const deltaY = touch.clientY - touchStartY;
  const elapsed = Date.now() - touchStartTime;

  // Detect Double Tap to Seek
  if (Math.abs(deltaX) < 25 && Math.abs(deltaY) < 25 && elapsed < 350) {
    const now = Date.now();
    const rect = videoContainer.getBoundingClientRect();
    const clickX = touch.clientX - rect.left;
    const isLeft = clickX < rect.width / 2;

    if (now - lastTapTime < 300) {
      if (isLeft) {
        seekVideo(-5);
        showSeekIndicator("left");
      } else {
        seekVideo(5);
        showSeekIndicator("right");
      }
      lastTapTime = 0;
    } else {
      lastTapTime = now;
    }
  }
}, { passive: true });
}
function seekVideo(seconds) {
if (!videoPlayer) return;
const newTime = Math.max(0, Math.min(videoPlayer.duration || 99999, videoPlayer.currentTime + seconds));
videoPlayer.currentTime = newTime;
}
function showSeekIndicator(side) {
const el = side === "left" ? seekLeftIndicator : seekRightIndicator;
if (!el) return;
el.classList.remove("hidden", "seek-indicator-anim");
void el.offsetWidth;
el.classList.add("seek-indicator-anim");
setTimeout(() => el.classList.add("hidden"), 500);
}
// Video Controls Auto-Hide Manager
let controlsHideTimeout = null;
let isInteractingWithControls = false;
function showVideoControls(autoHide = true) {
if (!videoPlayer) return;
videoPlayer.controls = true;
if (controlsHideTimeout) {
  clearTimeout(controlsHideTimeout);
  controlsHideTimeout = null;
}
if (autoHide && !videoPlayer.paused && !isInteractingWithControls) {
  controlsHideTimeout = setTimeout(() => {
    if (!videoPlayer.paused && !isInteractingWithControls) {
      videoPlayer.controls = false;
    }
  }, 2000);
}
}
function hideVideoControls() {
if (!videoPlayer) return;
if (controlsHideTimeout) {
  clearTimeout(controlsHideTimeout);
  controlsHideTimeout = null;
}
if (!videoPlayer.paused && !isInteractingWithControls) {
  videoPlayer.controls = false;
}
}
function setupVideoControlsManager() {
if (!videoContainer || !videoPlayer) return;

// Initially ensure controls are hidden when playback starts
videoPlayer.controls = false;

// Whenever a video begins playing or resumes, hide controls immediately
videoPlayer.addEventListener("play", () => {
  hideVideoControls();
});

// When video is paused, reveal controls so user can inspect or scrub
videoPlayer.addEventListener("pause", () => {
  showVideoControls(false);
});

// Reveal controls when mouse moves over the player
const onMouseMove = () => {
  showVideoControls(true);
};
videoContainer.addEventListener("mousemove", onMouseMove);
videoPlayer.addEventListener("mousemove", onMouseMove);

// Instantly hide controls when mouse leaves the player area (if playing)
videoContainer.addEventListener("mouseleave", () => {
  hideVideoControls();
});

// Keep controls visible while user is dragging or holding mouse down
videoContainer.addEventListener("mousedown", () => {
  isInteractingWithControls = true;
  showVideoControls(false);
});

window.addEventListener("mouseup", () => {
  if (isInteractingWithControls) {
    isInteractingWithControls = false;
    if (!videoPlayer.paused) {
      showVideoControls(true);
    }
  }
});

// On touch devices: reveal controls on tap, then auto-hide
videoContainer.addEventListener("touchstart", () => {
  showVideoControls(true);
}, { passive: true });
}
function setupSorterTouchSeek() {
if (!videoPlayer) return;
const container = videoContainer || videoPlayer.parentElement || videoPlayer;
if (getComputedStyle(container).position === 'static') {
  container.style.position = 'relative';
}

let lastTapTime = 0;
let lastTapX = 0;
let lastTapY = 0;

videoPlayer.addEventListener('touchend', (e) => {
  const isDoubleTapEnabled = localStorage.getItem('lib_touch_double_tap_seek') !== 'false';
  if (!isDoubleTapEnabled) return;

  const touch = e.changedTouches && e.changedTouches[0];
  if (!touch) return;

  const now = Date.now();
  const timeDiff = now - lastTapTime;
  const distDiff = Math.hypot(touch.clientX - lastTapX, touch.clientY - lastTapY);

  if (timeDiff > 40 && timeDiff < 320 && distDiff < 45) {
    const rect = videoPlayer.getBoundingClientRect();
    const clickX = touch.clientX - rect.left;
    const ratio = clickX / rect.width;

    if (ratio <= 0.42) {
      e.preventDefault();
      e.stopPropagation();
      videoPlayer.currentTime = Math.max(0, videoPlayer.currentTime - 5);
      showSorterSeekRipple(container, 'left', '-5s');
    } else if (ratio >= 0.58) {
      e.preventDefault();
      e.stopPropagation();
      videoPlayer.currentTime = Math.min(videoPlayer.duration || 999999, videoPlayer.currentTime + 5);
      showSorterSeekRipple(container, 'right', '+5s');
    }
    lastTapTime = 0;
  } else {
    lastTapTime = now;
    lastTapX = touch.clientX;
    lastTapY = touch.clientY;
  }
}, { passive: false });
}
function showSorterSeekRipple(container, side, text) {
const old = container.querySelector('.mobile-seek-ripple-container');
if (old) old.remove();

const ripple = document.createElement('div');
ripple.className = `mobile-seek-ripple-container ${side}`;
const rippleGlyph = side === 'left' ? '⏪' : '⏩';
ripple.innerHTML = `
  <div class="mobile-seek-ripple-bubble">
    <span class="icon">${rippleGlyph}</span>
    <span class="label font-mono font-bold">${text}</span>
  </div>
`;
container.appendChild(ripple);
setTimeout(() => {
  if (ripple.parentNode) ripple.remove();
}, 650);
}

export { controlsHideTimeout, hideVideoControls, isInteractingWithControls, loadVideoSource, preloadNextVideo, seekVideo, setupSorterTouchSeek, setupVideoControlsManager, setupVideoGestures, showSeekIndicator, showSorterSeekRipple, showVideoControls };
