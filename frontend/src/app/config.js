import { browseDirectory } from './browser.js';
import { checkCurrentVideoDuplicate, hideDuplicateBanner } from './duplicates.js';
import { currentSourceDisplay, currentVideoIndex, currentVideoSize, currentVideoTitle, destPathDisplay, emptyVideoState, folderSearchInput, lanIpBadge, preloadVideoPlayer, primaryMoveBtn, queueCountBadge, streamTypeBadge, toggleStreamModeBtn, videoPlayer } from './elements.js';
import { filterFolders, renderRecentFolders, updatePrimaryMoveBtn } from './folders.js';
import { updateCurrentVideoTranscodeBadge } from './polling.js';
import { showToast, state, updateUndoState } from './state.js';
import { loadVideoSource, preloadNextVideo } from './video.js';

// Config, destinations and the incoming queue.
// Split out of the former monolithic sorter index.js.

async function loadConfig() {
try {
  const res = await fetch("/api/config");
  const data = await res.json();
  state.lanIp = data.lan_ip;
  state.sourceDir = data.source_dir || "";
  state.destDir = data.destination_dir;

  lanIpBadge.textContent = `http://${state.lanIp}:${window.location.port || 8888}`;
  destPathDisplay.textContent = state.destDir;
  destPathDisplay.title = state.destDir;
  updateUndoState(data.can_undo, data.last_undo_item);
} catch (err) {
  console.error("Failed to load config:", err);
}
}
async function loadDestinations() {
try {
  const res = await fetch("/api/destinations");
  const data = await res.json();
  state.destinations = data.folders || [];
  renderRecentFolders();
  filterFolders(folderSearchInput.value);
} catch (err) {
  console.error("Failed to load destinations:", err);
}
}
async function refreshQueue() {
if (!state.sourceDir) return;
try {
  const res = await fetch("/api/queue", { cache: 'no-store' });
  const data = await res.json();
  state.queue = data.videos || [];
  if (state.currentIndex >= state.queue.length) {
    state.currentIndex = Math.max(0, state.queue.length - 1);
  }
  updateUI();
} catch (err) {
  console.error("Failed to refresh queue:", err);
  showToast("Failed to refresh queue", "error");
}
}
function currentVideo() {
if (state.queue && state.queue.length > 0 && state.currentIndex < state.queue.length) {
  return state.queue[state.currentIndex];
}
return null;
}
function updateUI() {
const video = currentVideo();
currentSourceDisplay.textContent = state.sourceDir || "None";
currentSourceDisplay.title = state.sourceDir || "No source directory";
const mobileSource = document.getElementById("currentSourceDisplayMobile");
if (mobileSource) {
  const folderName = state.sourceDir ? state.sourceDir.split("/").filter(Boolean).pop() : "Src";
  mobileSource.textContent = folderName || "Src";
  mobileSource.title = state.sourceDir || "No source directory";
}
queueCountBadge.textContent = `${state.queue.length} videos`;

if (!video) {
  emptyVideoState.classList.remove("hidden");
  videoPlayer.pause();
  videoPlayer.removeAttribute("src");
  videoPlayer.load();

  if (preloadVideoPlayer) {
    preloadVideoPlayer.removeAttribute("src");
    preloadVideoPlayer.dataset.loadedId = "";
  }

  currentVideoTitle.textContent = "No video in queue";
  currentVideoSize.textContent = "0 MB";
  currentVideoIndex.textContent = "0 / 0";
  streamTypeBadge.textContent = "Inactive";
  streamTypeBadge.className = "bg-slate-800 text-slate-400 border border-slate-700 px-1.5 py-0.5 rounded font-mono text-[10px]";
  toggleStreamModeBtn.classList.add("hidden");
  primaryMoveBtn.disabled = true;
  hideDuplicateBanner();
  return;
}

emptyVideoState.classList.add("hidden");
currentVideoTitle.textContent = video.filename;
currentVideoSize.textContent = video.size_formatted;
currentVideoIndex.textContent = `${state.currentIndex + 1} / ${state.queue.length}`;

const isDirectCapable = !video.needs_transcode;
loadVideoSource(video, isDirectCapable);
updatePrimaryMoveBtn();
updateCurrentVideoTranscodeBadge();

// Instant Next Video Preloading
preloadNextVideo();

// Real-time Duplicate Check against Backup v2
hideDuplicateBanner();
checkCurrentVideoDuplicate(video);
}
async function loadQuickNav() {
const container = document.getElementById("quickNavButtons");
if (!container) return;
try {
  const res = await fetch("/api/quick-nav");
  const data = await res.json();
  if (data.shortcuts && data.shortcuts.length > 0) {
    container.innerHTML = "";
    data.shortcuts.forEach((item) => {
      const btn = document.createElement("button");
      btn.className = "quickNavBtn min-h-[36px] px-2.5 py-1 bg-slate-800 hover:bg-slate-700 active:bg-slate-600 text-slate-300 rounded-lg whitespace-nowrap text-xs font-medium transition cursor-pointer";
      btn.dataset.path = item.path;
      btn.textContent = item.label;
      btn.title = item.path;
      btn.addEventListener("click", () => {
        browseDirectory(item.path);
      });
      container.appendChild(btn);
    });
  }
} catch (err) {
  console.error("Failed to load quick nav:", err);
}
}

export { currentVideo, loadConfig, loadDestinations, loadQuickNav, refreshQueue, updateUI };
