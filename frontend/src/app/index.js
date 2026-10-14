import "../../../static/styles.css";
import { computeTaskSummary, formatTaskCount, describePrimaryTask } from "../shared/tasks_badge.js";
import { icon, hydrateIcons } from "../shared/icons.js";
// Video Sorter Frontend Application - v2.1
let state = {
  queue: [],
  currentIndex: 0,
  destinations: [],
  recentDestinations: [], // Stored locally in browser localStorage
  selectedFolder: null,
  highlightedIndex: -1,
  filteredFolders: [],
  lanIp: "",
  sourceDir: "",
  destDir: "",
  streamMode: "direct", // 'direct' or 'transcode'
  activeTransfer: null,
  queuedTransfers: [],
  transferHistory: [],
  totalPendingTransfers: 0,
  // Pre-transcoding state
  activeTranscode: null,
  queuedTranscodes: [],
  transcodeHistory: [],
  isTranscodePaused: false,
  totalPendingTranscodes: 0,
  pollTimer: null,
  currentDuplicate: null,
  isComparingDuplicate: false,
  autoTrashDuplicate: localStorage.getItem("sorter_autotrash_duplicates") === "true",
};

// DOM Elements
const videoPlayer = document.getElementById("mainVideoPlayer");
const preloadVideoPlayer = document.getElementById("preloadVideoPlayer");
const videoContainer = document.getElementById("videoContainer");

// Duplicate Alert UI Elements
const duplicateBanner = document.getElementById("duplicateBanner");
const autoTrashDuplicateToggle = document.getElementById("autoTrashDuplicateToggle");
const duplicateBadgeType = document.getElementById("duplicateBadgeType");
const duplicateMatchInfo = document.getElementById("duplicateMatchInfo");
const toggleDuplicateCompareBtn = document.getElementById("toggleDuplicateCompareBtn");
const duplicateCompareText = document.getElementById("duplicateCompareText");
const trashIncomingDuplicateBtn = document.getElementById("trashIncomingDuplicateBtn");
const dismissDuplicateBtn = document.getElementById("dismissDuplicateBtn");
const duplicateCompareContainer = document.getElementById("duplicateCompareContainer");
const compareIncomingName = document.getElementById("compareIncomingName");
const compareIncomingSize = document.getElementById("compareIncomingSize");
const compareExistingName = document.getElementById("compareExistingName");
const compareExistingSize = document.getElementById("compareExistingSize");
const compareExistingVideo = document.getElementById("compareExistingVideo");
const emptyVideoState = document.getElementById("emptyVideoState");
const currentVideoTitle = document.getElementById("currentVideoTitle");
const currentVideoSize = document.getElementById("currentVideoSize");
const currentVideoIndex = document.getElementById("currentVideoIndex");
const streamTypeBadge = document.getElementById("streamTypeBadge");
const transcodeStatusBadge = document.getElementById("transcodeStatusBadge");
const toggleStreamModeBtn = document.getElementById("toggleStreamModeBtn");
const currentSourceDisplay = document.getElementById("currentSourceDisplay");
const destPathDisplay = document.getElementById("destPathDisplay");
const lanIpBadge = document.getElementById("lanIpBadge");
const queueCountBadge = document.getElementById("queueCountBadge");
const undoBtn = document.getElementById("undoBtn");
const shortcutsBtn = document.getElementById("shortcutsBtn");
const prevBtn = document.getElementById("prevBtn");
const skipBtn = document.getElementById("skipBtn");
const renameBtn = document.getElementById("renameBtn");
const renameHeaderBtn = document.getElementById("renameHeaderBtn");
const deleteBtn = document.getElementById("deleteBtn");
const folderSearchInput = document.getElementById("folderSearchInput");
const clearSearchBtn = document.getElementById("clearSearchBtn");
const folderListBox = document.getElementById("folderListBox");
const folderMatchCount = document.getElementById("folderMatchCount");
const primaryMoveBtn = document.getElementById("primaryMoveBtn");
const primaryMoveBtnText = document.getElementById("primaryMoveBtnText");
const recentFoldersContainer = document.getElementById("recentFoldersContainer");
const newFolderInput = document.getElementById("newFolderInput");
const createAndMoveBtn = document.getElementById("createAndMoveBtn");
const refreshDestsBtn = document.getElementById("refreshDestsBtn");
const movingSpinnerOverlay = document.getElementById("movingSpinnerOverlay");
const movingStatusText = document.getElementById("movingStatusText");

// Background Tasks UI (Transfers & Pre-Transcoding)
const transferBadgeBtn = document.getElementById("transferBadgeBtn");
const transferIcon = document.getElementById("transferIcon");
const transferSpinner = document.getElementById("transferSpinner");
const transferDropdown = document.getElementById("transferDropdown");
const transferCountText = document.getElementById("transferCountText");
const transferDropdownSummary = document.getElementById("transferDropdownSummary");
const activeTransferBox = document.getElementById("activeTransferBox");
const activeTransferFilename = document.getElementById("activeTransferFilename");
const activeTransferPercent = document.getElementById("activeTransferPercent");
const activeTransferProgressBar = document.getElementById("activeTransferProgressBar");
const activeTransferTarget = document.getElementById("activeTransferTarget");
const activeTransferSize = document.getElementById("activeTransferSize");
const queuedTransfersList = document.getElementById("queuedTransfersList");
const transferHistoryList = document.getElementById("transferHistoryList");

// Pre-Transcoder UI
const transcodeSummaryText = document.getElementById("transcodeSummaryText");
const toggleTranscodePauseBtn = document.getElementById("toggleTranscodePauseBtn");
const activeTranscodeBox = document.getElementById("activeTranscodeBox");
const activeTranscodeMethodBadge = document.getElementById("activeTranscodeMethodBadge");
const activeTranscodeFilename = document.getElementById("activeTranscodeFilename");
const activeTranscodePercent = document.getElementById("activeTranscodePercent");
const activeTranscodeProgressBar = document.getElementById("activeTranscodeProgressBar");
const activeTranscodeSpeed = document.getElementById("activeTranscodeSpeed");
const activeTranscodeEta = document.getElementById("activeTranscodeEta");
const queuedTranscodesContainer = document.getElementById("queuedTranscodesContainer");
const queuedTranscodesList = document.getElementById("queuedTranscodesList");

// Thumbnail Generator UI
const thumbSummaryText = document.getElementById("thumbSummaryText");
const toggleThumbPauseBtn = document.getElementById("toggleThumbPauseBtn");
const thumbProgressBox = document.getElementById("thumbProgressBox");
const thumbCurrentFile = document.getElementById("thumbCurrentFile");
const thumbPercentText = document.getElementById("thumbPercentText");
const thumbProgressBar = document.getElementById("thumbProgressBar");
const thumbCountsText = document.getElementById("thumbCountsText");
const thumbStatusLabel = document.getElementById("thumbStatusLabel");

// Duplicate Hash Verifier UI
const hashSummaryText = document.getElementById("hashSummaryText");
const toggleHashPauseBtn = document.getElementById("toggleHashPauseBtn");
const hashProgressBox = document.getElementById("hashProgressBox");
const hashCurrentFile = document.getElementById("hashCurrentFile");
const hashPercentText = document.getElementById("hashPercentText");
const hashProgressBar = document.getElementById("hashProgressBar");
const hashCountsText = document.getElementById("hashCountsText");
const hashStatusLabel = document.getElementById("hashStatusLabel");

// WebP Optimizer UI
const webpSummaryText = document.getElementById("webpSummaryText");
const toggleWebpPauseBtn = document.getElementById("toggleWebpPauseBtn");
const webpProgressBox = document.getElementById("webpProgressBox");
const webpCurrentFile = document.getElementById("webpCurrentFile");
const webpPercentText = document.getElementById("webpPercentText");
const webpProgressBar = document.getElementById("webpProgressBar");
const webpCountsText = document.getElementById("webpCountsText");
const webpSavedText = document.getElementById("webpSavedText");

// Indexer UI
const indexSummaryText = document.getElementById("indexSummaryText");
const triggerIndexSyncDropdownBtn = document.getElementById("triggerIndexSyncDropdownBtn");
const indexSyncDropdownIcon = document.getElementById("indexSyncDropdownIcon");
const indexCurrentFolder = document.getElementById("indexCurrentFolder");
const indexPercentText = document.getElementById("indexPercentText");
const indexProgressBar = document.getElementById("indexProgressBar");
const indexCountsText = document.getElementById("indexCountsText");
const indexFilesText = document.getElementById("indexFilesText");

// Delete Empty Folders UI
const cleanDbBtn = document.getElementById("cleanDbBtn");
const deleteEmptyFoldersBtn = document.getElementById("deleteEmptyFoldersBtn");
const deleteEmptyFoldersBtnText = document.getElementById("deleteEmptyFoldersBtnText");
const emptyFoldersModal = document.getElementById("emptyFoldersModal");
const closeEmptyFoldersModalBtn = document.getElementById("closeEmptyFoldersModalBtn");
const cancelEmptyFoldersModalBtn = document.getElementById("cancelEmptyFoldersModalBtn");
const confirmEmptyFoldersBtn = document.getElementById("confirmEmptyFoldersBtn");
const confirmEmptyFoldersBtnText = document.getElementById("confirmEmptyFoldersBtnText");
const emptyFoldersCandidateCount = document.getElementById("emptyFoldersCandidateCount");
const emptyFoldersLeafCount = document.getElementById("emptyFoldersLeafCount");
const emptyFoldersSelectAllToggle = document.getElementById("emptyFoldersSelectAllToggle");
const emptyFoldersSelectedBadge = document.getElementById("emptyFoldersSelectedBadge");
const emptyFoldersFilterInput = document.getElementById("emptyFoldersFilterInput");
const emptyFoldersCandidatesList = document.getElementById("emptyFoldersCandidatesList");

let emptyFoldersCandidates = [];
let emptyFoldersSelectedPaths = new Set();
let emptyFoldersFilterQuery = "";

// Mobile Thumb Bar Buttons
const mobilePrevBtn = document.getElementById("mobilePrevBtn");
const mobileSkipBtn = document.getElementById("mobileSkipBtn");
const mobileUndoBtn = document.getElementById("mobileUndoBtn");
const mobileRenameBtn = document.getElementById("mobileRenameBtn");
const mobileDeleteBtn = document.getElementById("mobileDeleteBtn");

// Gesture Indicators
const seekLeftIndicator = document.getElementById("seekLeftIndicator");
const seekRightIndicator = document.getElementById("seekRightIndicator");

// Modals
const browserModal = document.getElementById("browserModal");
const openFolderBrowserBtn = document.getElementById("openFolderBrowserBtn");
const emptyStateBrowseBtn = document.getElementById("emptyStateBrowseBtn");
const closeBrowserModalBtn = document.getElementById("closeBrowserModalBtn");
const cancelBrowserBtn = document.getElementById("cancelBrowserBtn");
const browserPathInput = document.getElementById("browserPathInput");
const browserGoBtn = document.getElementById("browserGoBtn");
const browserUpBtn = document.getElementById("browserUpBtn");
const browserFolderList = document.getElementById("browserFolderList");
const browserVideoCountIndicator = document.getElementById("browserVideoCountIndicator");
const selectCurrentFolderBtn = document.getElementById("selectCurrentFolderBtn");

const deleteModal = document.getElementById("deleteModal");
const deleteFilenameDisplay = document.getElementById("deleteFilenameDisplay");
const cancelDeleteBtn = document.getElementById("cancelDeleteBtn");
const confirmDeleteBtn = document.getElementById("confirmDeleteBtn");

const renameModal = document.getElementById("renameModal");
const renameInput = document.getElementById("renameInput");
const cancelRenameBtn = document.getElementById("cancelRenameBtn");
const confirmRenameBtn = document.getElementById("confirmRenameBtn");

const shortcutsModal = document.getElementById("shortcutsModal");
const closeShortcutsBtn = document.getElementById("closeShortcutsBtn");
const toastContainer = document.getElementById("toastContainer");

// Local Storage for Recent Folders
function loadLocalRecents() {
  try {
    const saved = localStorage.getItem("video_sorter_recents");
    if (saved) {
      const parsed = JSON.parse(saved);
      if (Array.isArray(parsed)) {
        state.recentDestinations = parsed;
        return;
      }
    }
  } catch (e) {
    console.warn("Could not parse local recents:", e);
  }
  state.recentDestinations = [];
}

function saveLocalRecents() {
  try {
    localStorage.setItem(
      "video_sorter_recents",
      JSON.stringify(state.recentDestinations.slice(0, 10))
    );
  } catch (e) {
    console.warn("Could not save local recents:", e);
  }
}

function addRecentFolder(folder) {
  if (!folder) return;
  const clean = folder.trim();
  if (!clean) return;
  state.recentDestinations = state.recentDestinations.filter((f) => f !== clean);
  state.recentDestinations.unshift(clean);
  saveLocalRecents();
  renderRecentFolders();
}

// Initialize Application
document.addEventListener("DOMContentLoaded", async () => {
  // Static template markup carries icon placeholders; swap in the shared
  // inline SVG set before anything renders.
  hydrateIcons(document);
  setupEventListeners();
  setupVideoGestures();
  setupVideoControlsManager();
  loadLocalRecents();
  await loadConfig();
  await loadDestinations();

  // If source directory is not set, try saved preference
  if (!state.sourceDir) {
    const savedSource = localStorage.getItem("video_sorter_source");
    if (savedSource) {
      await setSourceDirectory(savedSource, false);
    }
  }

  if (state.sourceDir) {
    await refreshQueue();
  } else {
    updateUI();
  }

  // Start background transfers, thumbnails, & duplicate hash polling
  startTransfersPolling();
  startThumbnailPolling();
  startDuplicateHashPolling();
  startWebpPolling();
  startBackgroundIndexPolling();
});

function showToast(message, type = "info", duration = 3000) {
  // Only display toast notifications on failures
  if (type !== "error") return;
  if (!toastContainer) return;

  const toast = document.createElement("div");
  const bg = "bg-red-950/95 border-red-700/80 text-red-200";

  toast.className = `p-3 rounded-xl border shadow-2xl text-xs font-medium flex items-center gap-2 backdrop-blur-md toast-animate pointer-events-auto transition-all ${bg}`;
  toast.innerHTML = `<span>${message}</span>`;
  toastContainer.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(-10px)";
    setTimeout(() => toast.remove(), 250);
  }, duration || 4000);
}

function updateUndoState(canUndo, lastItem = null) {
  if (undoBtn) {
    undoBtn.disabled = !canUndo;
    if (lastItem) undoBtn.title = `Undo last move: ${lastItem} (U)`;
  }
  if (mobileUndoBtn) {
    mobileUndoBtn.disabled = !canUndo;
    if (lastItem) mobileUndoBtn.title = `Undo last move: ${lastItem}`;
  }
}

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

// Render Recent Destinations Carousel (from local storage)
function renderRecentFolders() {
  recentFoldersContainer.innerHTML = "";
  if (!state.recentDestinations || state.recentDestinations.length === 0) {
    recentFoldersContainer.innerHTML = `<span class="text-xs text-slate-500 italic py-1 whitespace-nowrap">No recent folders yet</span>`;
    return;
  }

  state.recentDestinations.slice(0, 6).forEach((folder, idx) => {
    const btn = document.createElement("button");
    const shortcutNum = idx + 1;
    btn.className =
      "min-h-[34px] px-2.5 py-1 bg-slate-800 hover:bg-slate-700 active:scale-95 border border-slate-700 hover:border-emerald-500/50 text-slate-200 rounded-lg text-xs font-medium flex items-center gap-1.5 transition whitespace-nowrap group shrink-0";
    btn.title = `Move to ${folder} (Key: ${shortcutNum})`;
    btn.innerHTML = `
      <span class="text-slate-400 group-hover:text-emerald-400 text-xs">📁</span>
      <span class="max-w-[120px] truncate">${escapeHtml(folder)}</span>
      <kbd class="px-1 py-0.2 rounded bg-slate-900 border border-slate-700 text-[10px] font-mono text-emerald-300">${shortcutNum}</kbd>
    `;
    btn.addEventListener("click", () => {
      executeMove(folder);
    });
    recentFoldersContainer.appendChild(btn);
  });
}

// Filter Destinations List
function filterFolders(query = "") {
  const q = query.trim().toLowerCase();
  if (clearSearchBtn) {
    clearSearchBtn.classList.toggle("hidden", !query);
  }

  if (!q) {
    state.filteredFolders = [...state.destinations];
  } else {
    state.filteredFolders = state.destinations.filter((f) =>
      f.toLowerCase().includes(q)
    );
  }

  folderMatchCount.textContent = state.filteredFolders.length;
  state.highlightedIndex = state.filteredFolders.length > 0 ? 0 : -1;
  renderFolderList();
  updatePrimaryMoveBtn();
}

// Render Folder List (clean without stars)
function renderFolderList() {
  folderListBox.innerHTML = "";

  if (state.filteredFolders.length === 0) {
    const q = folderSearchInput.value.trim();
    if (q) {
      folderListBox.innerHTML = `
        <div class="p-4 text-center text-xs text-slate-400 flex flex-col items-center gap-2">
          <p>No matching folder for "<strong>${escapeHtml(q)}</strong>"</p>
          <button id="createInlineFolderBtn" class="min-h-[42px] px-4 py-2 bg-emerald-600 hover:bg-emerald-500 active:scale-95 text-white rounded-xl text-xs font-semibold shadow">
            + Create "${escapeHtml(q)}" and Move
          </button>
        </div>
      `;
      const btn = document.getElementById("createInlineFolderBtn");
      if (btn) {
        btn.addEventListener("click", () => {
          createAndMoveFolder(q);
        });
      }
    } else {
      folderListBox.innerHTML = `<div class="p-4 text-center text-xs text-slate-500">No destination folders found.</div>`;
    }
    return;
  }

  state.filteredFolders.forEach((folder, idx) => {
    const isSelected = idx === state.highlightedIndex;
    const item = document.createElement("div");
    item.className = `folder-item px-3.5 py-2.5 text-xs cursor-pointer flex items-center justify-between select-none ${
      isSelected ? "selected" : "text-slate-300"
    }`;
    item.dataset.index = idx;

    item.innerHTML = `
      <div class="flex items-center gap-2 truncate min-w-0">
        <span class="text-slate-400 text-sm">📁</span>
        <span class="truncate font-medium text-xs sm:text-sm">${escapeHtml(folder)}</span>
      </div>
      <span class="text-[10px] text-slate-500 opacity-60 font-mono hidden sm:inline">Enter ↵</span>
    `;

    // Row Click -> Move
    item.addEventListener("click", () => {
      state.highlightedIndex = idx;
      updateHighlight();
      executeMove(folder);
    });

    item.addEventListener("mouseenter", () => {
      state.highlightedIndex = idx;
      updateHighlight();
    });

    folderListBox.appendChild(item);
  });

  scrollHighlightedIntoView();
}

function updateHighlight() {
  const items = folderListBox.querySelectorAll(".folder-item");
  items.forEach((item, idx) => {
    if (idx === state.highlightedIndex) {
      item.classList.add("selected");
    } else {
      item.classList.remove("selected");
    }
  });
  updatePrimaryMoveBtn();
}

function scrollHighlightedIntoView() {
  const selectedEl = folderListBox.querySelector(".folder-item.selected");
  if (selectedEl) {
    selectedEl.scrollIntoView({ block: "nearest" });
  }
}

function updatePrimaryMoveBtn() {
  if (state.highlightedIndex >= 0 && state.highlightedIndex < state.filteredFolders.length) {
    const target = state.filteredFolders[state.highlightedIndex];
    primaryMoveBtnText.textContent = `Move to "${target}"`;
    primaryMoveBtn.disabled = !currentVideo();
  } else {
    const q = folderSearchInput.value.trim();
    if (q) {
      primaryMoveBtnText.textContent = `Create "${q}" & Move`;
      primaryMoveBtn.disabled = !currentVideo();
    } else {
      primaryMoveBtnText.textContent = "Select a subfolder above";
      primaryMoveBtn.disabled = true;
    }
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

// ==========================================
// Duplicate Detection Flow (Incoming Sorter)
// ==========================================

let duplicateCheckAbortCtrl = null;

async function checkCurrentVideoDuplicate(video) {
  if (duplicateCheckAbortCtrl) {
    duplicateCheckAbortCtrl.abort();
    duplicateCheckAbortCtrl = null;
  }
  if (!video || !duplicateBanner) return;
  const videoId = video.id;

  duplicateCheckAbortCtrl = new AbortController();
  try {
    const res = await fetch(`/api/sorter/check-duplicate?file_id=${encodeURIComponent(videoId)}`, {
      signal: duplicateCheckAbortCtrl.signal
    });
    if (!res.ok) return;
    const data = await res.json();

    // Ensure we are still showing the same video
    const curr = currentVideo();
    if (!curr || curr.id !== videoId) return;

    if (data.is_duplicate && data.matches && data.matches.length > 0) {
      state.currentDuplicate = data;
      showDuplicateBanner(data, curr);
    } else {
      state.currentDuplicate = null;
      hideDuplicateBanner();
    }
  } catch (err) {
    if (err.name === "AbortError") return;
    console.error("Error checking duplicate:", err);
  } finally {
    if (duplicateCheckAbortCtrl && duplicateCheckAbortCtrl.signal.aborted) {
      duplicateCheckAbortCtrl = null;
    }
  }
}

function showDuplicateBanner(data, incomingVideo) {
  if (!duplicateBanner) return;
  const match = data.matches[0];
  if (!match) return;

  // Auto-trash incoming video if autoTrashDuplicate is enabled and content is exact 100% match
  if (state.autoTrashDuplicate && data.match_type === "content") {
    showToast(`⚡ 100% duplicate detected in Backup v2 — auto-trashing incoming video`, "warning", 3000);
    trashCurrentDuplicate();
    return;
  }

  if (duplicateBadgeType) {
    if (data.match_type === "content") {
      duplicateBadgeType.textContent = "Exact Content Match";
      duplicateBadgeType.className = "text-[10px] font-mono px-1.5 py-0.2 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30";
    } else {
      duplicateBadgeType.textContent = "Same Filename";
      duplicateBadgeType.className = "text-[10px] font-mono px-1.5 py-0.2 rounded bg-sky-500/20 text-sky-300 border border-sky-500/30";
    }
  }

  if (duplicateMatchInfo) {
    duplicateMatchInfo.textContent = `Existing in: 📁 ${match.folder_display} / ${match.name} (${match.size_formatted})`;
    duplicateMatchInfo.title = match.path;
  }

  // Populate compare previews
  if (compareIncomingName) compareIncomingName.textContent = incomingVideo.filename;
  if (compareIncomingSize) compareIncomingSize.textContent = incomingVideo.size_formatted;
  if (compareExistingName) compareExistingName.textContent = match.name;
  if (compareExistingSize) compareExistingSize.textContent = match.size_formatted;
  if (compareExistingVideo) {
    compareExistingVideo.src = match.media_url;
    if (match.thumb_url) compareExistingVideo.poster = match.thumb_url;
  }

  duplicateBanner.classList.remove("hidden");
}

function hideDuplicateBanner() {
  if (!duplicateBanner) return;
  duplicateBanner.classList.add("hidden");
  if (duplicateCompareContainer) duplicateCompareContainer.classList.add("hidden");
  if (compareExistingVideo) {
    try { compareExistingVideo.pause(); } catch (e) {}
    compareExistingVideo.removeAttribute("src");
    compareExistingVideo.load();
  }
  state.isComparingDuplicate = false;
  if (duplicateCompareText) duplicateCompareText.textContent = "Compare";
}

function toggleDuplicateCompare() {
  if (!duplicateCompareContainer) return;
  state.isComparingDuplicate = !state.isComparingDuplicate;
  if (state.isComparingDuplicate) {
    duplicateCompareContainer.classList.remove("hidden");
    if (duplicateCompareText) duplicateCompareText.textContent = "Hide Compare";
    if (compareExistingVideo && compareExistingVideo.src) {
      compareExistingVideo.load();
      compareExistingVideo.play().catch(() => {});
    }
  } else {
    duplicateCompareContainer.classList.add("hidden");
    if (duplicateCompareText) duplicateCompareText.textContent = "Compare";
    if (compareExistingVideo) {
      try { compareExistingVideo.pause(); } catch (e) {}
    }
  }
}

async function trashCurrentDuplicate() {
  const video = currentVideo();
  if (!video) return;

  try {
    const res = await fetch("/api/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_id: video.id }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Failed to delete file");
    }

    showToast(`Deleted duplicate "${escapeHtml(video.filename)}"`, "warning");
    hideDuplicateBanner();

    state.queue = state.queue.filter((v) => v.id !== video.id);
    if (state.currentIndex >= state.queue.length) {
      state.currentIndex = Math.max(0, state.queue.length - 1);
    }
    updateUI();
  } catch (err) {
    showToast(`Delete error: ${err.message}`, "error");
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

// Non-Blocking Move Execution
async function executeMove(destFolder) {
  const video = currentVideo();
  if (!video) return;

  const targetFolder = destFolder.trim();
  if (!targetFolder) return;

  // Save to local recents (in browser localStorage)
  addRecentFolder(targetFolder);

  // 1. Optimistic UI update: Remove current video from local queue immediately (<50ms)
  state.queue = state.queue.filter((v) => v.id !== video.id);
  if (state.currentIndex >= state.queue.length) {
    state.currentIndex = Math.max(0, state.queue.length - 1);
  }

  // Reset folder search input
  folderSearchInput.value = "";
  filterFolders("");

  // Update UI and start playing the next preloaded video immediately
  state.streamMode = "direct";
  updateUI();

  // Subtle toast indicator
  showToast(`Moving "${video.filename}" ➔ <strong>${escapeHtml(targetFolder)}</strong>`, "info", 2500);

  // 2. Dispatch background transfer to server
  try {
    const res = await fetch("/api/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_id: video.id,
        destination_folder: targetFolder,
      }),
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Failed to schedule move");
    }

    // Update undo state
    updateUndoState(true, video.filename);

    // Refresh destinations
    await loadDestinations();

    // Trigger immediate poll of transfer status
    pollTransfers();
  } catch (err) {
    console.error("Move error:", err);
    showToast(`Transfer failed for "${video.filename}": ${err.message}`, "error", 5000);
    state.queue.splice(state.currentIndex, 0, video);
    updateUI();
  }
}

async function createAndMoveFolder(folderName) {
  const cleanName = folderName.trim();
  if (!cleanName) return;

  try {
    const res = await fetch("/api/destinations/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_name: cleanName }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Failed to create folder");
    }

    showToast(`Created folder "${cleanName}"`, "success");
    await executeMove(cleanName);
  } catch (err) {
    showToast(`Failed to create folder: ${err.message}`, "error");
  }
}

// Smart Undo
async function undoLastMove() {
  try {
    const res = await fetch("/api/undo", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Failed to undo move");
    }

    if (data.action === "cancelled_queued") {
      showToast(`Cancelled pending move of "${escapeHtml(data.restored_filename)}"`, "success");
    } else if (data.action === "aborted_active") {
      showToast(`Aborted active transfer of "${escapeHtml(data.restored_filename)}"`, "warning");
    } else {
      showToast(`Restored "${escapeHtml(data.restored_filename)}" to source folder`, "info");
    }

    updateUndoState(data.remaining_undo_count > 0);
    await refreshQueue();
    pollTransfers();
  } catch (err) {
    showToast(`Undo failed: ${err.message}`, "error");
  }
}

function skipVideo() {
  if (state.queue.length === 0) return;
  state.currentIndex = (state.currentIndex + 1) % state.queue.length;
  state.streamMode = "direct";
  updateUI();
}

function prevVideo() {
  if (state.queue.length === 0) return;
  state.currentIndex = (state.currentIndex - 1 + state.queue.length) % state.queue.length;
  state.streamMode = "direct";
  updateUI();
}

let isPollingSystemStatus = false;

// Background Transfers & Unified System Status Polling
function startTransfersPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  pollTransfers(true);
  state.pollTimer = setInterval(() => {
    if (!document.hidden) {
      pollTransfers();
    }
  }, 4000);
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    pollTransfers(true);
  }
});

async function pollTransfers(immediate = false) {
  if (isPollingSystemStatus && !immediate) return;
  if (document.hidden && !immediate) return;

  isPollingSystemStatus = true;
  try {
    const res = await fetch("/api/system/status");
    if (!res.ok) return;
    const sysData = await res.json();
    const data = sysData.transfers || {};

    state.activeTransfer = data.active;
    state.queuedTransfers = data.queued || [];
    state.transferHistory = data.history || [];
    state.totalPendingTransfers = data.total_pending || 0;

    // Pre-transcoding state
    if (data.transcode) {
      state.activeTranscode = data.transcode.active;
      state.queuedTranscodes = data.transcode.queued || [];
      state.transcodeHistory = data.transcode.history || [];
      state.isTranscodePaused = !!data.transcode.is_paused;
      state.totalPendingTranscodes = data.transcode.total_pending || 0;
    }

    // Sync in-memory queue with completed transcodes
    if (state.queue && state.queue.length > 0 && state.transcodeHistory && state.transcodeHistory.length > 0) {
      state.transcodeHistory.forEach((h) => {
        if (h.status === "completed") {
          const targetName = h.target_filename || h.filename.replace(/\.[^/.]+$/, ".mp4");
          const qItem = state.queue.find((v) => v.id === h.filename || v.filename === h.filename);
          if (qItem) {
            qItem.id = targetName;
            qItem.filename = targetName;
            qItem.ext = ".mp4";
            qItem.needs_transcode = false;
          }
        }
      });
    }

    renderTransfersUI();
    renderTranscodeUI();
    updateCurrentVideoTranscodeBadge();
    checkIfCurrentVideoConverted();

    if (sysData.thumbnails) {
      state.latestThumbData = sysData.thumbnails;
      renderThumbnailUI(sysData.thumbnails);
    }
    if (sysData.duplicate_hash) {
      state.latestDupHashData = sysData.duplicate_hash;
      renderDuplicateHashUI(sysData.duplicate_hash);
    }
    if (sysData.webp) {
      state.latestWebpData = sysData.webp;
      renderWebpUI(sysData.webp);
    }
    if (sysData.index) {
      state.latestIndexData = sysData.index;
      renderIndexUI(sysData.index);
    }
    if (sysData.library_backup) {
      state.latestLibBackupData = sysData.library_backup;
    }
    updateHeaderTaskBadge();
  } catch (err) {
    // Silent catch on poll errors
  } finally {
    isPollingSystemStatus = false;
  }
}

function updateCurrentVideoTranscodeBadge() {
  if (!transcodeStatusBadge) return;
  const video = currentVideo();
  if (!video) {
    transcodeStatusBadge.classList.add("hidden");
    return;
  }

  // Is this video actively transcoding right now?
  if (state.activeTranscode && (state.activeTranscode.filename === video.filename || state.activeTranscode.filename === video.id)) {
    transcodeStatusBadge.classList.remove("hidden");
    transcodeStatusBadge.className = "bg-emerald-950 text-emerald-300 border border-emerald-700 px-1.5 py-0.5 rounded font-mono text-[10px] animate-pulse";
    transcodeStatusBadge.textContent = `⚙️ Converting (${state.activeTranscode.percent}%)`;
    return;
  }

  // Is this video queued for transcode?
  const inQueue = state.queuedTranscodes && state.queuedTranscodes.some((q) => q.filename === video.filename || q.filename === video.id);
  if (inQueue) {
    transcodeStatusBadge.classList.remove("hidden");
    transcodeStatusBadge.className = "bg-slate-800 text-slate-300 border border-slate-700 px-1.5 py-0.5 rounded font-mono text-[10px]";
    transcodeStatusBadge.textContent = `⏳ Queued for MP4`;
    return;
  }

  // Was it recently converted?
  const wasConverted = state.transcodeHistory && state.transcodeHistory.some(
    (h) => h.status === "completed" && (h.filename === video.filename || h.filename === video.id || h.target_filename === video.filename)
  );
  if (wasConverted && !video.needs_transcode) {
    transcodeStatusBadge.classList.remove("hidden");
    transcodeStatusBadge.className = "bg-emerald-950 text-emerald-300 border border-emerald-800 px-1.5 py-0.5 rounded font-mono text-[10px]";
    transcodeStatusBadge.textContent = `✨ Auto-MP4`;
    return;
  }

  transcodeStatusBadge.classList.add("hidden");
}

async function checkIfCurrentVideoConverted() {
  const video = currentVideo();
  if (!video) return;

  if (state.transcodeHistory && state.transcodeHistory.length > 0) {
    const matched = state.transcodeHistory.find(
      (h) => (h.filename === video.filename || h.filename === video.id || (h.target_filename && h.target_filename === video.filename)) && h.status === "completed"
    );
    if (matched && state.streamMode === "transcode") {
      const targetName = matched.target_filename || matched.filename.replace(/\.[^/.]+$/, ".mp4");
      video.id = targetName;
      video.filename = targetName;
      video.ext = ".mp4";
      video.needs_transcode = false;

      currentVideoTitle.textContent = video.filename;
      const currentTime = videoPlayer.currentTime;
      const isPaused = videoPlayer.paused;

      loadVideoSource(video, true);

      const restoreTime = () => {
        try {
          if (currentTime > 0 && currentTime < videoPlayer.duration) {
            videoPlayer.currentTime = currentTime;
          }
          if (!isPaused) {
            videoPlayer.play().catch(() => {});
          }
        } catch (e) {}
      };

      if (videoPlayer.readyState >= 1) {
        restoreTime();
      } else {
        videoPlayer.addEventListener("loadedmetadata", restoreTime, { once: true });
      }

      showToast("Auto-converted to MP4! Switched to direct playback.", "success");
    }
  }
}

function renderTranscodeUI() {
  // Pause/Resume button
  if (toggleTranscodePauseBtn) {
    toggleTranscodePauseBtn.textContent = state.isTranscodePaused ? "Resume" : "Pause";
    toggleTranscodePauseBtn.className = state.isTranscodePaused
      ? "px-2 py-0.5 bg-emerald-900/60 hover:bg-emerald-800 text-emerald-200 border border-emerald-700 rounded text-[10px] font-semibold transition"
      : "px-2 py-0.5 bg-slate-800 hover:bg-slate-700 text-emerald-300 hover:text-white border border-slate-700 rounded text-[10px] font-semibold transition";
  }

  // Summary Text
  if (transcodeSummaryText) {
    if (state.isTranscodePaused) {
      transcodeSummaryText.textContent = "Paused";
      transcodeSummaryText.className = "text-[10px] text-amber-400 font-mono";
    } else if (state.activeTranscode) {
      transcodeSummaryText.textContent = `${state.totalPendingTranscodes} active`;
      transcodeSummaryText.className = "text-[10px] text-emerald-400 font-mono";
    } else if (state.queuedTranscodes && state.queuedTranscodes.length > 0) {
      transcodeSummaryText.textContent = `${state.queuedTranscodes.length} queued`;
      transcodeSummaryText.className = "text-[10px] text-slate-400 font-mono";
    } else {
      transcodeSummaryText.textContent = "Idle";
      transcodeSummaryText.className = "text-[10px] text-slate-500 font-mono";
    }
  }

  // Active Transcode Box
  if (state.activeTranscode) {
    activeTranscodeBox.classList.remove("hidden");
    activeTranscodeFilename.textContent = state.activeTranscode.filename;
    activeTranscodePercent.textContent = `${state.activeTranscode.percent}%`;
    activeTranscodeProgressBar.style.width = `${Math.min(100, state.activeTranscode.percent)}%`;

    const method = state.activeTranscode.method === "remux" ? "⚡ Remux" : "⚙️ Encode";
    activeTranscodeMethodBadge.textContent = method;

    const fps = state.activeTranscode.fps > 0 ? `${state.activeTranscode.fps} fps` : "";
    const speed = state.activeTranscode.speed ? `(${state.activeTranscode.speed})` : "";
    activeTranscodeSpeed.textContent = [fps, speed].filter(Boolean).join(" ") || "Starting...";

    activeTranscodeEta.textContent = state.activeTranscode.eta ? `ETA: ${state.activeTranscode.eta}` : "Calculating...";
  } else {
    activeTranscodeBox.classList.add("hidden");
  }

  // Queued Transcodes List
  if (queuedTranscodesList) {
    queuedTranscodesList.innerHTML = "";
    if (state.queuedTranscodes && state.queuedTranscodes.length > 0) {
      queuedTranscodesContainer.classList.remove("hidden");
      state.queuedTranscodes.slice(0, 5).forEach((t) => {
        const row = document.createElement("div");
        row.className = "flex items-center justify-between p-1.5 bg-slate-950/70 rounded-lg text-[11px] text-slate-300";
        row.innerHTML = `
          <div class="flex items-center gap-1.5 truncate min-w-0">
            ${icon('cog', 'w-3.5 h-3.5 text-emerald-400 shrink-0')}
            <span class="truncate max-w-[200px]">${escapeHtml(t.filename)}</span>
          </div>
          <button class="cancelTranscodeBtn text-slate-500 hover:text-red-400 p-0.5 ml-1 leading-none" title="Cancel this conversion">${icon('x', 'w-3.5 h-3.5')}</button>
        `;
        row.querySelector(".cancelTranscodeBtn").addEventListener("click", () => cancelSpecificTranscode(t.id));
        queuedTranscodesList.appendChild(row);
      });
    } else {
      queuedTranscodesContainer.classList.add("hidden");
    }
  }

  // Update Combined Header Badge Text
  updateHeaderTaskBadge();
}

function updateHeaderTaskBadge() {
  if (!transferBadgeBtn) return;
  // Always keep button visible so the background tasks modal can be opened anytime
  transferBadgeBtn.classList.remove("hidden");

  const taskSources = {
    transfers: { active: state.activeTransfer, queued: state.queuedTransfers || [] },
    transcode: {
      active: state.activeTranscode,
      queue_length: (state.queuedTranscodes || []).length,
    },
    thumbnails: state.latestThumbData,
    webp: state.latestWebpData,
    index: state.latestIndexData,
    backup: state.latestLibBackupData,
  };
  const tasksSummary = computeTaskSummary(taskSources);
  const tasksLabel = describePrimaryTask(taskSources);
  const badgeText = formatTaskCount(tasksSummary.count);
  const badgeStatus = tasksSummary.active ? "Active" : "Idle";

  if (tasksSummary.active) {
    if (transferSpinner) transferSpinner.classList.remove("hidden");
    if (transferIcon) transferIcon.classList.add("hidden");
    transferBadgeBtn.classList.add("text-emerald-300", "border-emerald-500/40");
    transferBadgeBtn.classList.remove("text-slate-300");
  } else {
    if (transferSpinner) transferSpinner.classList.add("hidden");
    if (transferIcon) transferIcon.classList.remove("hidden");
    transferBadgeBtn.classList.remove("text-emerald-300", "border-emerald-500/40");
    transferBadgeBtn.classList.add("text-slate-300");
  }
  transferBadgeBtn.title = `View background activity (${badgeStatus})${tasksLabel ? ` - ${tasksLabel}` : ''}`;
  if (transferCountText) {
    transferCountText.textContent = badgeText;
    transferCountText.classList.toggle("hidden", !badgeText);
  }

  // Update tab badges and empty state in tasks modal
  const activeQueueCount = tasksSummary.count;
  const tabBadgeActive = document.getElementById("tabBadgeActive");
  const activeQueuesEmptyState = document.getElementById("activeQueuesEmptyState");
  if (tabBadgeActive) {
    if (activeQueueCount > 0) {
      tabBadgeActive.textContent = activeQueueCount;
      tabBadgeActive.classList.remove("hidden");
    } else {
      tabBadgeActive.classList.add("hidden");
    }
  }
  if (activeQueuesEmptyState) {
    activeQueuesEmptyState.classList.toggle("hidden", activeQueueCount > 0);
  }

  const tabBadgeWorkers = document.getElementById("tabBadgeWorkers");
  if (tabBadgeWorkers) {
    const isThumbGenerating = state.latestThumbData && state.latestThumbData.status === "generating" && state.latestThumbData.pending_count > 0;
    const isWebpConverting = state.latestWebpData && state.latestWebpData.status === "converting" && state.latestWebpData.pending_count > 0;
    const isIndexing = state.latestIndexData && state.latestIndexData.status === "crawling";
    const isAnyWorkerActive = isThumbGenerating || isWebpConverting || isIndexing || (state.latestHashData && (state.latestHashData.status === "checking" || state.latestHashData.pending_count > 0));
    if (isAnyWorkerActive) {
      tabBadgeWorkers.textContent = "Active";
      tabBadgeWorkers.className = "px-1.5 py-0.2 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 text-[10px] font-mono";
      tabBadgeWorkers.classList.remove("hidden");
    } else {
      tabBadgeWorkers.textContent = "Idle";
      tabBadgeWorkers.className = "px-1.5 py-0.2 rounded-full bg-slate-800 text-slate-400 border border-slate-700 text-[10px] font-mono";
      tabBadgeWorkers.classList.remove("hidden");
    }
  }
}

function startThumbnailPolling() {
  // Handled by unified pollTransfers
}

function pollThumbnailStatus() {
  pollTransfers(true);
}

function renderThumbnailUI(data) {
  if (!thumbSummaryText) return;
  const isPaused = data.is_paused;
  const isGenerating = data.status === "generating";
  const isDone = data.status === "complete" || (data.pending_count === 0 && data.total_files > 0);

  if (toggleThumbPauseBtn) {
    toggleThumbPauseBtn.textContent = isPaused ? "Resume" : "Pause";
    toggleThumbPauseBtn.className = isPaused
      ? "px-2 py-0.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-[10px] font-semibold transition"
      : "px-2 py-0.5 bg-slate-800 hover:bg-slate-700 text-emerald-300 hover:text-white border border-slate-700 rounded text-[10px] font-semibold transition";
  }

  if (isPaused) {
    thumbSummaryText.textContent = "Paused";
    if (thumbStatusLabel) thumbStatusLabel.textContent = "Paused";
  } else if (isGenerating) {
    thumbSummaryText.textContent = "Generating";
    if (thumbStatusLabel) thumbStatusLabel.textContent = "Active";
  } else if (isDone) {
    thumbSummaryText.textContent = "All Cached";
    if (thumbStatusLabel) thumbStatusLabel.textContent = "Complete";
  } else {
    thumbSummaryText.textContent = "Idle";
    if (thumbStatusLabel) thumbStatusLabel.textContent = "Idle";
  }

  if (thumbCurrentFile) {
    thumbCurrentFile.textContent = data.current_file || (isDone ? "All files cached" : (isPaused ? "Generation paused" : "Idle"));
  }
  if (thumbPercentText) thumbPercentText.textContent = `${Math.round(data.percent || 0)}%`;
  if (thumbProgressBar) thumbProgressBar.style.width = `${data.percent || 0}%`;
  if (thumbCountsText) thumbCountsText.textContent = `${data.cached_count || 0} / ${data.total_files || 0}`;

  updateHeaderTaskBadge();
}

async function toggleThumbnailPause() {
  try {
    const res = await fetch("/api/backup/thumbnails/pause", { method: "POST" });
    if (res.ok) {
      pollThumbnailStatus();
    }
  } catch (err) {
    console.error("Error toggling thumbnail pause:", err);
  }
}

function startDuplicateHashPolling() {
  // Handled by unified pollTransfers
}

function pollDuplicateHashStatus() {
  pollTransfers(true);
}

function renderDuplicateHashUI(data) {
  if (!hashSummaryText) return;
  const isPaused = data.is_paused;
  const isHashing = data.status === "hashing";
  const isDone = (data.pending_count === 0 && data.total_candidates > 0) || (data.status === "idle" && data.total_candidates > 0 && data.pending_count === 0);

  if (toggleHashPauseBtn) {
    toggleHashPauseBtn.textContent = isPaused ? "Resume" : "Pause";
    toggleHashPauseBtn.className = isPaused
      ? "px-2 py-0.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-[10px] font-semibold transition"
      : "px-2 py-0.5 bg-slate-800 hover:bg-slate-700 text-emerald-300 hover:text-white border border-slate-700 rounded text-[10px] font-semibold transition";
  }

  if (isPaused) {
    hashSummaryText.textContent = "Paused";
    if (hashStatusLabel) hashStatusLabel.textContent = "Paused";
  } else if (isHashing) {
    hashSummaryText.textContent = "Verifying";
    if (hashStatusLabel) hashStatusLabel.textContent = "Active";
  } else if (isDone) {
    hashSummaryText.textContent = "100% Pre-Hashed";
    if (hashStatusLabel) hashStatusLabel.textContent = "Complete";
  } else {
    hashSummaryText.textContent = "Idle";
    if (hashStatusLabel) hashStatusLabel.textContent = "Idle";
  }

  if (hashCurrentFile) {
    hashCurrentFile.textContent = data.current_file || (isDone ? `All ${data.total_candidates} duplicate candidates verified in SQLite` : (isPaused ? "Hash verification paused" : "Idle"));
  }
  if (hashPercentText) hashPercentText.textContent = `${Math.round(data.percent || 0)}%`;
  if (hashProgressBar) hashProgressBar.style.width = `${data.percent || 0}%`;
  if (hashCountsText) hashCountsText.textContent = `${data.hashed_count || 0} / ${data.total_candidates || 0}`;
}

async function toggleDuplicateHashPause() {
  try {
    const res = await fetch("/api/backup/duplicates/hash-pause", { method: "POST" });
    if (res.ok) {
      pollDuplicateHashStatus();
    }
  } catch (err) {
    console.error("Error toggling hash verification pause:", err);
  }
}

function startWebpPolling() {
  // Handled by unified pollTransfers
}

function pollWebpStatus() {
  pollTransfers(true);
}

function renderWebpUI(data) {
  if (!webpSummaryText) return;
  const isPaused = data.is_paused || data.status === "paused";
  const isConverting = data.status === "converting";
  const isDone = data.pending_count === 0 && data.total_images > 0;

  if (toggleWebpPauseBtn) {
    toggleWebpPauseBtn.textContent = isPaused ? "Resume" : "Pause";
    toggleWebpPauseBtn.className = isPaused
      ? "px-2 py-0.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-[10px] font-semibold transition"
      : "px-2 py-0.5 bg-slate-800 hover:bg-slate-700 text-teal-300 hover:text-white border border-slate-700 rounded text-[10px] font-semibold transition";
  }

  if (isPaused) {
    webpSummaryText.textContent = "Paused";
    webpSummaryText.className = "text-[10px] text-amber-400 font-mono font-bold";
  } else if (isConverting) {
    webpSummaryText.textContent = `Optimizing (${data.concurrency || 1}w)`;
    webpSummaryText.className = "text-[10px] text-teal-400 font-mono font-bold animate-pulse";
  } else if (isDone) {
    webpSummaryText.textContent = "100% WebP";
    webpSummaryText.className = "text-[10px] text-emerald-400 font-mono font-bold";
  } else {
    webpSummaryText.textContent = "Idle";
    webpSummaryText.className = "text-[10px] text-slate-400 font-mono";
  }

  if (webpCurrentFile) {
    webpCurrentFile.textContent = data.current_file || (isDone ? `All ${data.total_images} images processed` : (isPaused ? "WebP optimization paused" : "Idle"));
  }
  if (webpPercentText) webpPercentText.textContent = `${Math.round(data.percent || 0)}%`;
  if (webpProgressBar) webpProgressBar.style.width = `${data.percent || 0}%`;
  if (webpCountsText) {
    const tot = data.total_images || 0;
    const pend = data.pending_count || 0;
    const opt = data.optimized_count || Math.max(0, tot - pend);
    const conv = data.converted_count || 0;
    if (pend === 0 && tot > 0) {
      if (conv > 0) {
        webpCountsText.textContent = `${tot} / ${tot} WebP (${conv} converted)`;
      } else {
        webpCountsText.textContent = `${tot} / ${tot} WebP`;
      }
    } else {
      if (conv > 0) {
        webpCountsText.textContent = `${opt} / ${tot} WebP (${conv} converted)`;
      } else {
        webpCountsText.textContent = `${opt} / ${tot} WebP`;
      }
    }
  }
  if (webpSavedText) {
    const sBytes = data.saved_bytes || 0;
    if (sBytes <= 0) {
      webpSavedText.textContent = "Saved: 0 B";
    } else {
      webpSavedText.textContent = `Saved: ${data.saved_formatted || "0 B"}`;
    }
  }
}

async function toggleWebpPause() {
  try {
    const res = await fetch("/api/backup/webp/pause", { method: "POST" });
    if (res.ok) {
      pollWebpStatus();
    }
  } catch (err) {
    console.error("Error toggling WebP pause:", err);
  }
}

function startBackgroundIndexPolling() {
  // Handled by unified pollTransfers
}

function pollIndexStatus() {
  pollTransfers(true);
}

function renderIndexUI(data) {
  if (!data) return;
  const isCrawling = data.status === "crawling";
  const scanned = data.scanned_folders || 0;
  const total = data.total_folders || 0;
  const filesCount = data.total_files || 0;
  const pct = typeof data.percent === "number" ? data.percent : (total > 0 ? (scanned / total * 100) : 100);

  if (indexSummaryText) {
    indexSummaryText.textContent = isCrawling ? "Indexing" : "Indexed";
    indexSummaryText.className = isCrawling ? "text-[10px] text-amber-400 font-mono font-medium animate-pulse" : "text-[10px] text-emerald-400 font-mono";
  }
  if (triggerIndexSyncDropdownBtn) {
    triggerIndexSyncDropdownBtn.disabled = isCrawling;
    if (isCrawling) {
      triggerIndexSyncDropdownBtn.classList.add("opacity-50", "pointer-events-none");
    } else {
      triggerIndexSyncDropdownBtn.classList.remove("opacity-50", "pointer-events-none");
    }
  }
  if (indexSyncDropdownIcon) {
    if (isCrawling) indexSyncDropdownIcon.classList.add("animate-spin");
    else indexSyncDropdownIcon.classList.remove("animate-spin");
  }
  if (indexCurrentFolder) {
    if (isCrawling && data.current_folder) {
      indexCurrentFolder.textContent = `Scanning: ${data.current_folder}`;
      indexCurrentFolder.title = data.current_folder;
    } else if (isCrawling) {
      indexCurrentFolder.textContent = "Scanning folders...";
      indexCurrentFolder.title = "";
    } else {
      indexCurrentFolder.textContent = "All folders indexed";
      indexCurrentFolder.title = "";
    }
  }
  if (indexPercentText) {
    indexPercentText.textContent = `${Math.min(100, Math.max(0, pct)).toFixed(0)}%`;
  }
  if (indexProgressBar) {
    indexProgressBar.style.width = `${Math.min(100, Math.max(0, pct))}%`;
    if (isCrawling) {
      indexProgressBar.className = "bg-gradient-to-r from-amber-500 to-emerald-400 h-1.5 rounded-full transition-all duration-200";
    } else {
      indexProgressBar.className = "bg-gradient-to-r from-emerald-500 to-teal-400 h-1.5 rounded-full transition-all duration-200";
    }
  }
  if (indexCountsText) {
    indexCountsText.textContent = `${scanned.toLocaleString()} / ${total.toLocaleString()} folders`;
  }
  if (indexFilesText) {
    indexFilesText.textContent = `${filesCount.toLocaleString()} files`;
  }

  updateHeaderTaskBadge();
}

if (triggerIndexSyncDropdownBtn) {
  triggerIndexSyncDropdownBtn.addEventListener("click", async () => {
    try {
      if (indexSyncDropdownIcon) indexSyncDropdownIcon.classList.add("animate-spin");
      showToast("Starting library index scan...", "info");
      const res = await fetch("/api/backup/index/sync", { method: "POST" });
      const data = await res.json();
      if (data.success) {
        showToast("Background crawler started", "success");
      }
      pollIndexStatus();
    } catch (e) {
      showToast("Failed to trigger index sync: " + e.message, "error");
    }
  });
}

// ==================== DELETE EMPTY FOLDERS MODAL & LOGIC ====================

async function openEmptyFoldersModal() {
  if (transferDropdown) {
    transferDropdown.classList.add("hidden");
  }
  if (!emptyFoldersModal) return;
  emptyFoldersModal.classList.remove("hidden");
  if (emptyFoldersCandidatesList) {
    emptyFoldersCandidatesList.innerHTML = `
      <div class="col-span-full py-12 flex flex-col items-center justify-center gap-2 text-slate-400">
        <span class="w-6 h-6 border-2 border-rose-400 border-t-transparent rounded-full animate-spin"></span>
        <span class="text-xs">Scanning library for empty folders...</span>
      </div>
    `;
  }
  if (confirmEmptyFoldersBtn) confirmEmptyFoldersBtn.disabled = true;

  try {
    const res = await fetch("/api/backup/folders/empty/candidates");
    if (!res.ok) throw new Error("Failed to fetch candidate empty folders");
    const data = await res.json();

    emptyFoldersCandidates = data.candidates || [];
    emptyFoldersSelectedPaths = new Set(emptyFoldersCandidates.map(c => c.path));
    emptyFoldersFilterQuery = "";
    if (emptyFoldersFilterInput) emptyFoldersFilterInput.value = "";

    if (emptyFoldersCandidateCount) emptyFoldersCandidateCount.textContent = data.total_candidates;
    const leafCount = emptyFoldersCandidates.filter(c => c.is_leaf).length;
    if (emptyFoldersLeafCount) emptyFoldersLeafCount.textContent = leafCount;

    renderEmptyFoldersCandidates();
  } catch (err) {
    console.error("Error opening empty folders modal:", err);
    showToast("Could not load empty folders", "error");
    if (emptyFoldersCandidatesList) {
      emptyFoldersCandidatesList.innerHTML = `<div class="text-center py-8 text-xs text-rose-400">Error loading empty folders: ${escapeHtml(err.message)}</div>`;
    }
  }
}

function closeEmptyFoldersModal() {
  if (emptyFoldersModal) emptyFoldersModal.classList.add("hidden");
}

function renderEmptyFoldersCandidates() {
  if (!emptyFoldersCandidatesList) return;
  const q = (emptyFoldersFilterQuery || "").toLowerCase().trim();
  const filtered = emptyFoldersCandidates.filter(c =>
    c.name.toLowerCase().includes(q) ||
    (c.parent_name && c.parent_name.toLowerCase().includes(q)) ||
    (c.relative_path && c.relative_path.toLowerCase().includes(q)) ||
    c.path.toLowerCase().includes(q)
  );

  if (filtered.length === 0) {
    emptyFoldersCandidatesList.innerHTML = `
      <div class="text-center py-8 text-xs text-slate-500">
        ${emptyFoldersCandidates.length === 0 ? "No empty folders found in library." : "No candidates match your search filter."}
      </div>
    `;
    if (emptyFoldersSelectedBadge) emptyFoldersSelectedBadge.textContent = `0 of ${emptyFoldersCandidates.length} selected`;
    if (confirmEmptyFoldersBtn) confirmEmptyFoldersBtn.disabled = true;
    if (confirmEmptyFoldersBtnText) confirmEmptyFoldersBtnText.textContent = "Delete Selected (0)";
    return;
  }

  const selectedCount = emptyFoldersSelectedPaths.size;
  const totalCount = emptyFoldersCandidates.length;

  if (emptyFoldersSelectedBadge) {
    emptyFoldersSelectedBadge.textContent = `${selectedCount} of ${totalCount} selected`;
  }
  if (confirmEmptyFoldersBtn) {
    confirmEmptyFoldersBtn.disabled = selectedCount === 0;
  }
  if (confirmEmptyFoldersBtnText) {
    confirmEmptyFoldersBtnText.textContent = `Delete Selected (${selectedCount})`;
  }
  if (emptyFoldersSelectAllToggle) {
    emptyFoldersSelectAllToggle.checked = selectedCount === totalCount && totalCount > 0;
    emptyFoldersSelectAllToggle.indeterminate = selectedCount > 0 && selectedCount < totalCount;
  }

  emptyFoldersCandidatesList.innerHTML = filtered.map(c => {
    const isChecked = emptyFoldersSelectedPaths.has(c.path);
    return `
      <div class="flex items-center justify-between gap-3 p-2 rounded-xl hover:bg-slate-800/50 transition cursor-pointer group" data-path="${escapeHtml(c.path)}">
        <div class="flex items-center gap-2.5 min-w-0 flex-1">
          <input 
            type="checkbox" 
            class="empty-folder-item-checkbox rounded bg-slate-800 border-slate-700 text-rose-600 focus:ring-rose-500 cursor-pointer"
            data-path="${escapeHtml(c.path)}"
            ${isChecked ? "checked" : ""}
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
  }).join("");

  emptyFoldersCandidatesList.querySelectorAll(".empty-folder-item-checkbox").forEach(cb => {
    cb.addEventListener("change", (e) => {
      const path = e.target.dataset.path;
      if (e.target.checked) {
        emptyFoldersSelectedPaths.add(path);
      } else {
        emptyFoldersSelectedPaths.delete(path);
      }
      renderEmptyFoldersCandidates();
    });
  });

  emptyFoldersCandidatesList.querySelectorAll("[data-path]").forEach(row => {
    row.addEventListener("click", (e) => {
      if (e.target.classList.contains("empty-folder-item-checkbox")) return;
      const cb = row.querySelector(".empty-folder-item-checkbox");
      if (cb) {
        cb.checked = !cb.checked;
        cb.dispatchEvent(new Event("change"));
      }
    });
  });
}

async function submitDeleteEmptyFolders() {
  const selected = Array.from(emptyFoldersSelectedPaths);
  if (selected.length === 0) return;

  try {
    if (confirmEmptyFoldersBtn) confirmEmptyFoldersBtn.disabled = true;
    if (confirmEmptyFoldersBtnText) confirmEmptyFoldersBtnText.textContent = "Deleting...";

    const res = await fetch("/api/backup/folders/empty/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_paths: selected }),
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || "Failed to delete empty folders");
    }

    const data = await res.json();
    closeEmptyFoldersModal();

    if (data.deleted_count > 0) {
      showToast(`Successfully deleted ${data.deleted_count} empty folder${data.deleted_count === 1 ? "" : "s"}!`, "success");
    } else {
      showToast("No empty folders were deleted", "info");
    }

    // Refresh subfolders list in Sorter if available
    if (typeof loadSubfolders === "function") {
      loadSubfolders();
    }
  } catch (err) {
    console.error("Error deleting empty folders:", err);
    showToast(err.message || "Error deleting empty folders", "error");
  } finally {
    if (confirmEmptyFoldersBtn) confirmEmptyFoldersBtn.disabled = false;
    if (confirmEmptyFoldersBtnText) confirmEmptyFoldersBtnText.textContent = `Delete Selected (${emptyFoldersSelectedPaths.size})`;
  }
}

if (cleanDbBtn) {
  cleanDbBtn.addEventListener("click", async () => {
    try {
      cleanDbBtn.disabled = true;
      const originalText = cleanDbBtn.innerHTML;
      cleanDbBtn.innerHTML = `${icon('loader-circle', 'w-3.5 h-3.5 animate-spin')}<span>Starting...</span>`;
      const res = await fetch("/api/system/clean-db", { method: "POST" });
      if (res.ok) {
        showToast("Database deep clean and thumbnail sweep started in the background.", "success");
      } else {
        showToast("Failed to start deep clean", "error");
      }
      setTimeout(() => {
        cleanDbBtn.disabled = false;
        cleanDbBtn.innerHTML = originalText;
      }, 2000);
    } catch (e) {
      console.error(e);
    }
  });
}

if (deleteEmptyFoldersBtn) {
  deleteEmptyFoldersBtn.addEventListener("click", openEmptyFoldersModal);
}
if (closeEmptyFoldersModalBtn) {
  closeEmptyFoldersModalBtn.addEventListener("click", closeEmptyFoldersModal);
}
if (cancelEmptyFoldersModalBtn) {
  cancelEmptyFoldersModalBtn.addEventListener("click", closeEmptyFoldersModal);
}
if (emptyFoldersModal) {
  emptyFoldersModal.addEventListener("click", (e) => {
    if (e.target === emptyFoldersModal) closeEmptyFoldersModal();
  });
}
if (confirmEmptyFoldersBtn) {
  confirmEmptyFoldersBtn.addEventListener("click", submitDeleteEmptyFolders);
}
if (emptyFoldersFilterInput) {
  emptyFoldersFilterInput.addEventListener("input", (e) => {
    emptyFoldersFilterQuery = e.target.value;
    renderEmptyFoldersCandidates();
  });
}
if (emptyFoldersSelectAllToggle) {
  emptyFoldersSelectAllToggle.addEventListener("change", (e) => {
    if (e.target.checked) {
      emptyFoldersSelectedPaths = new Set(emptyFoldersCandidates.map(c => c.path));
    } else {
      emptyFoldersSelectedPaths.clear();
    }
    renderEmptyFoldersCandidates();
  });
}

async function toggleTranscodePause() {
  try {
    const res = await fetch("/api/transcode/pause", { method: "POST" });
    const data = await res.json();
    state.isTranscodePaused = data.is_paused;
    renderTranscodeUI();
    showToast(data.is_paused ? "Paused pre-transcoding" : "Resumed pre-transcoding", "info");
    pollTransfers();
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

async function cancelSpecificTranscode(taskId) {
  try {
    const res = await fetch("/api/transcode/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: taskId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to cancel conversion");
    showToast("Cancelled conversion task", "info");
    pollTransfers();
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

function renderTransfersUI() {
  const pendingCount = state.totalPendingTransfers;

  transferDropdownSummary.textContent = `${pendingCount} pending`;

  // Active Transfer Box
  if (state.activeTransfer) {
    activeTransferBox.classList.remove("hidden");
    activeTransferFilename.textContent = state.activeTransfer.filename;
    activeTransferPercent.textContent = `${state.activeTransfer.percent}%`;
    activeTransferProgressBar.style.width = `${Math.min(100, state.activeTransfer.percent)}%`;
    activeTransferTarget.textContent = `➔ /${state.activeTransfer.destination_folder}`;
    const copiedMB = (state.activeTransfer.bytes_copied / (1024 * 1024)).toFixed(1);
    const totalMB = (state.activeTransfer.total_bytes / (1024 * 1024)).toFixed(1);
    activeTransferSize.textContent = `${copiedMB} / ${totalMB} MB`;
  } else {
    activeTransferBox.classList.add("hidden");
  }

  // Queued Transfers List
  queuedTransfersList.innerHTML = "";
  if (state.queuedTransfers.length > 0) {
    state.queuedTransfers.forEach((item) => {
      const row = document.createElement("div");
      row.className = "flex items-center justify-between p-1.5 bg-slate-950/70 rounded-lg text-[11px] text-slate-300";
      row.innerHTML = `
        <div class="flex items-center gap-1.5 truncate min-w-0">
          ${icon('loader-circle', 'w-3.5 h-3.5 text-emerald-400 shrink-0')}
          <span class="truncate max-w-[150px]">${escapeHtml(item.filename)}</span>
          <span class="text-[10px] text-slate-500">➔ /${escapeHtml(item.destination_folder)}</span>
        </div>
        <button class="cancelTaskBtn text-slate-500 hover:text-red-400 p-0.5 ml-1 leading-none" title="Cancel this transfer">${icon('x', 'w-3.5 h-3.5')}</button>
      `;
      const cancelBtn = row.querySelector(".cancelTaskBtn");
      cancelBtn.addEventListener("click", () => cancelSpecificTransfer(item.id));
      queuedTransfersList.appendChild(row);
    });
  }

  // Completed History List
  transferHistoryList.innerHTML = "";
  if (state.transferHistory.length === 0) {
    transferHistoryList.innerHTML = `<span class="text-slate-600 italic">No transfers yet</span>`;
  } else {
    state.transferHistory.slice(0, 5).forEach((h) => {
      const row = document.createElement("div");
      row.className = "flex items-center justify-between text-[11px] py-0.5";
      const statusTone = h.status === "completed"
        ? "text-emerald-400"
        : h.status === "cancelled"
          ? "text-slate-500"
          : "text-red-400";
      const statusIcon = icon(
        { completed: 'circle-check', cancelled: 'ban' }[h.status] || 'circle-x',
        `w-3 h-3 ${statusTone}`
      );
      row.innerHTML = `
        <span class="truncate max-w-[190px] text-slate-300 flex items-center gap-1">${statusIcon} ${escapeHtml(h.filename)}</span>
        <span class="text-[10px] text-slate-500 font-mono">/${escapeHtml(h.destination_folder)}</span>
      `;
      transferHistoryList.appendChild(row);
    });
  }
}

async function cancelSpecificTransfer(taskId) {
  try {
    const res = await fetch("/api/transfers/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: taskId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to cancel transfer");
    showToast("Cancelled transfer", "info");
    await refreshQueue();
    pollTransfers();
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

// Rename Dialog
function openRenameModal() {
  const video = currentVideo();
  if (!video) return;
  renameInput.value = video.filename;
  renameModal.classList.remove("hidden");
  setTimeout(() => {
    renameInput.focus();
    renameInput.select();
  }, 50);
}

function closeRenameModal() {
  renameModal.classList.add("hidden");
}

async function submitRename() {
  const video = currentVideo();
  const newName = renameInput.value.trim();
  if (!video || !newName || newName === video.filename) {
    closeRenameModal();
    return;
  }

  try {
    const res = await fetch("/api/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_id: video.id, new_name: newName }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Failed to rename file");
    }

    showToast(`Renamed to "${escapeHtml(data.new_filename)}"`, "success");
    closeRenameModal();
    await refreshQueue();
  } catch (err) {
    showToast(`Rename error: ${err.message}`, "error");
  }
}

// Delete Dialog
function openDeleteModal() {
  const video = currentVideo();
  if (!video) return;
  deleteFilenameDisplay.textContent = `${video.filename} (${video.size_formatted})`;
  deleteModal.classList.remove("hidden");
  confirmDeleteBtn.focus();
}

function closeDeleteModal() {
  deleteModal.classList.add("hidden");
}

async function submitDelete() {
  const video = currentVideo();
  if (!video) return;

  try {
    const res = await fetch("/api/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_id: video.id }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Failed to delete file");
    }

    showToast(`Deleted "${escapeHtml(video.filename)}"`, "warning");
    closeDeleteModal();

    state.queue = state.queue.filter((v) => v.id !== video.id);
    if (state.currentIndex >= state.queue.length) {
      state.currentIndex = Math.max(0, state.queue.length - 1);
    }
    updateUI();
  } catch (err) {
    showToast(`Delete error: ${err.message}`, "error");
  }
}

// Source Directory Browser Modal
let browserCurrentPath = "";

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

async function openBrowser(path = null) {
  browserModal.classList.remove("hidden");
  loadQuickNav();
  await browseDirectory(path || state.sourceDir || state.destDir || "/");
}

function closeBrowser() {
  browserModal.classList.add("hidden");
}

async function browseDirectory(path) {
  browserFolderList.innerHTML = `<div class="p-4 text-center text-xs text-slate-500">Loading...</div>`;
  try {
    const res = await fetch(`/api/browse?path=${encodeURIComponent(path)}`, { cache: 'no-store' });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Cannot browse directory");
    }

    browserCurrentPath = data.current_path;
    browserPathInput.value = data.current_path;
    browserUpBtn.disabled = !data.parent_path;
    browserUpBtn.dataset.parent = data.parent_path || "";
    browserVideoCountIndicator.textContent = `${data.video_count_here} video(s) found in this folder`;

    browserFolderList.innerHTML = "";
    if (data.folders.length === 0) {
      browserFolderList.innerHTML = `<div class="p-4 text-center text-xs text-slate-500">No subdirectories here</div>`;
    } else {
      data.folders.forEach((f) => {
        const row = document.createElement("div");
        row.className = "flex items-center justify-between p-2.5 hover:bg-slate-800 active:bg-slate-700 rounded-xl cursor-pointer text-xs text-slate-300 transition min-h-[44px]";
        const hasEdgeSpace = f.name !== f.name.trim();
        const marker = hasEdgeSpace
          ? `<span class="ml-1 px-1 rounded bg-amber-500/20 text-amber-300 text-[9px] font-mono" title="Folder name contains leading/trailing spaces">␠</span>`
          : "";
        row.innerHTML = `
          <div class="flex items-center gap-2 truncate min-w-0">
            <span class="text-emerald-400 text-sm">📁</span>
            <span class="truncate font-medium text-xs sm:text-sm">${escapeHtml(f.name)}</span>${marker}
          </div>
          <span class="text-[10px] text-slate-500 font-mono">Open ➔</span>
        `;
        row.addEventListener("click", () => {
          browseDirectory(f.path);
        });
        browserFolderList.appendChild(row);
      });
    }
  } catch (err) {
    browserFolderList.innerHTML = `<div class="p-4 text-center text-xs text-red-400">Error: ${escapeHtml(err.message)}</div>`;
  }
}

async function setSourceDirectory(path, notify = true) {
  try {
    const res = await fetch("/api/source/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_dir: path }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Failed to open directory");
    }

    state.sourceDir = data.source_dir;
    state.queue = data.videos || [];
    state.currentIndex = 0;
    localStorage.setItem("video_sorter_source", state.sourceDir);

    if (notify) {
      showToast(`Loaded ${data.count} videos`, "success");
    }
    updateUI();
  } catch (err) {
    if (notify) {
      showToast(`Error: ${err.message}`, "error");
    }
  }
}

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

// Event Listeners setup
function setupEventListeners() {
  const transferBackdrop = document.getElementById("transferBackdrop");
  const closeTransferDropdownBtn = document.getElementById("closeTransferDropdownBtn");
  const openFolderBrowserMobileBtn = document.getElementById("openFolderBrowserMobileBtn");

  if (openFolderBrowserMobileBtn) {
    openFolderBrowserMobileBtn.addEventListener("click", () => openBrowser());
  }

  if (closeTransferDropdownBtn) {
    closeTransferDropdownBtn.addEventListener("click", () => {
      transferDropdown.classList.add("hidden");
      if (transferBackdrop) transferBackdrop.classList.add("hidden");
    });
  }

  if (transferBackdrop) {
    transferBackdrop.addEventListener("click", () => {
      transferDropdown.classList.add("hidden");
      transferBackdrop.classList.add("hidden");
    });
  }

  // Tasks tab switcher
  function switchTasksTab(tabName) {
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
  }
  window.switchTasksTab = switchTasksTab;

  // Toggle Transfer Dropdown / Modal with smart tab selection
  if (transferBadgeBtn && transferDropdown) {
    transferBadgeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const isHidden = transferDropdown.classList.toggle("hidden");
      if (transferBackdrop) {
        if (!isHidden) {
          transferBackdrop.classList.remove("hidden");
        } else {
          transferBackdrop.classList.add("hidden");
        }
      }
      if (!isHidden) {
        const hasActiveQueues = (state.totalPendingTransfers || 0) > 0 || (state.totalPendingTranscodes || 0) > 0 || !!state.activeTransfer || !!state.activeTranscode;
        if (hasActiveQueues) {
          switchTasksTab('active');
        } else {
          let saved = 'workers';
          try { saved = localStorage.getItem('tasks_active_tab') || 'workers'; } catch (e) {}
          switchTasksTab(saved);
        }
        pollTransfers(true);
      }
    });

    if (transferDropdown) {
      transferDropdown.addEventListener("click", (e) => {
        if (e.target === transferDropdown) {
          transferDropdown.classList.add("hidden");
          if (transferBackdrop) transferBackdrop.classList.add("hidden");
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

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !transferDropdown.classList.contains("hidden")) {
        transferDropdown.classList.add("hidden");
        if (transferBackdrop) transferBackdrop.classList.add("hidden");
      }
    });
  }

  // Pre-Transcoder Pause/Resume Toggle
  if (toggleTranscodePauseBtn) {
    toggleTranscodePauseBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleTranscodePause();
    });
  }

  // Thumbnail Generator Pause/Resume Toggle
  if (toggleThumbPauseBtn) {
    toggleThumbPauseBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleThumbnailPause();
    });
  }

  // Duplicate Hash Verifier Pause/Resume Toggle
  if (toggleHashPauseBtn) {
    toggleHashPauseBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleDuplicateHashPause();
    });
  }

  // WebP Optimizer Pause/Resume Toggle
  if (toggleWebpPauseBtn) {
    toggleWebpPauseBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleWebpPause();
    });
  }

  // Folder Search Input
  folderSearchInput.addEventListener("input", (e) => {
    filterFolders(e.target.value);
  });

  if (clearSearchBtn) {
    clearSearchBtn.addEventListener("click", () => {
      folderSearchInput.value = "";
      filterFolders("");
      folderSearchInput.focus();
    });
  }

  folderSearchInput.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (state.filteredFolders.length > 0) {
        state.highlightedIndex = (state.highlightedIndex + 1) % state.filteredFolders.length;
        updateHighlight();
        scrollHighlightedIntoView();
      }
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (state.filteredFolders.length > 0) {
        state.highlightedIndex = (state.highlightedIndex - 1 + state.filteredFolders.length) % state.filteredFolders.length;
        updateHighlight();
        scrollHighlightedIntoView();
      }
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (state.highlightedIndex >= 0 && state.highlightedIndex < state.filteredFolders.length) {
        executeMove(state.filteredFolders[state.highlightedIndex]);
      } else {
        const q = folderSearchInput.value.trim();
        if (q) {
          createAndMoveFolder(q);
        }
      }
    } else if (e.key === "Escape") {
      folderSearchInput.blur();
    }
  });

  // Primary Move Button
  primaryMoveBtn.addEventListener("click", () => {
    if (state.highlightedIndex >= 0 && state.highlightedIndex < state.filteredFolders.length) {
      executeMove(state.filteredFolders[state.highlightedIndex]);
    } else {
      const q = folderSearchInput.value.trim();
      if (q) {
        createAndMoveFolder(q);
      }
    }
  });

  // Inline New Folder Create
  createAndMoveBtn.addEventListener("click", () => {
    const val = newFolderInput.value.trim();
    if (val) {
      createAndMoveFolder(val);
      newFolderInput.value = "";
    }
  });
  newFolderInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const val = newFolderInput.value.trim();
      if (val) {
        createAndMoveFolder(val);
        newFolderInput.value = "";
      }
    }
  });

  // Toolbar Actions (Desktop)
  if (prevBtn) prevBtn.addEventListener("click", prevVideo);
  if (skipBtn) skipBtn.addEventListener("click", skipVideo);
  if (renameBtn) renameBtn.addEventListener("click", openRenameModal);
  if (deleteBtn) deleteBtn.addEventListener("click", openDeleteModal);
  if (undoBtn) undoBtn.addEventListener("click", undoLastMove);

  // Mobile Bottom Bar Actions
  if (mobilePrevBtn) mobilePrevBtn.addEventListener("click", prevVideo);
  if (mobileSkipBtn) mobileSkipBtn.addEventListener("click", skipVideo);
  if (mobileUndoBtn) mobileUndoBtn.addEventListener("click", undoLastMove);
  if (mobileRenameBtn) mobileRenameBtn.addEventListener("click", openRenameModal);
  if (mobileDeleteBtn) mobileDeleteBtn.addEventListener("click", openDeleteModal);

  renameHeaderBtn.addEventListener("click", openRenameModal);
  currentVideoTitle.addEventListener("click", openRenameModal);

  refreshDestsBtn.addEventListener("click", () => {
    loadDestinations();
    showToast("Refreshed subfolders list", "info");
  });

  // Toggle Stream Mode
  toggleStreamModeBtn.addEventListener("click", () => {
    const video = currentVideo();
    if (!video) return;
    loadVideoSource(video, state.streamMode !== "direct");
  });

  // Browser Modal Events
  openFolderBrowserBtn.addEventListener("click", () => openBrowser());
  emptyStateBrowseBtn.addEventListener("click", () => openBrowser());
  closeBrowserModalBtn.addEventListener("click", closeBrowser);
  cancelBrowserBtn.addEventListener("click", closeBrowser);

  browserUpBtn.addEventListener("click", () => {
    const parent = browserUpBtn.dataset.parent;
    if (parent) browseDirectory(parent);
  });
  browserGoBtn.addEventListener("click", () => {
    browseDirectory(browserPathInput.value.trim());
  });
  browserPathInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") browseDirectory(browserPathInput.value.trim());
  });
  selectCurrentFolderBtn.addEventListener("click", async () => {
    await setSourceDirectory(browserCurrentPath);
    closeBrowser();
  });

  // Rename Modal Events
  cancelRenameBtn.addEventListener("click", closeRenameModal);
  confirmRenameBtn.addEventListener("click", submitRename);
  renameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitRename();
    if (e.key === "Escape") closeRenameModal();
  });

  // Delete Modal Events
  cancelDeleteBtn.addEventListener("click", closeDeleteModal);
  confirmDeleteBtn.addEventListener("click", submitDelete);

  // Shortcuts Modal
  shortcutsBtn.addEventListener("click", () => shortcutsModal.classList.remove("hidden"));
  closeShortcutsBtn.addEventListener("click", () => shortcutsModal.classList.add("hidden"));

  // Duplicate Alert Banner Events
  if (toggleDuplicateCompareBtn) toggleDuplicateCompareBtn.addEventListener("click", toggleDuplicateCompare);
  if (trashIncomingDuplicateBtn) trashIncomingDuplicateBtn.addEventListener("click", trashCurrentDuplicate);
  if (dismissDuplicateBtn) dismissDuplicateBtn.addEventListener("click", hideDuplicateBanner);
  if (autoTrashDuplicateToggle) {
    autoTrashDuplicateToggle.checked = !!state.autoTrashDuplicate;
    autoTrashDuplicateToggle.addEventListener("change", (e) => {
      state.autoTrashDuplicate = e.target.checked;
      localStorage.setItem("sorter_autotrash_duplicates", e.target.checked ? "true" : "false");
      if (state.autoTrashDuplicate && state.currentDuplicate && state.currentDuplicate.match_type === "content") {
        trashCurrentDuplicate();
      }
    });
  }

  // Global Keyboard Shortcuts
  window.addEventListener("keydown", (e) => {
    const activeTag = document.activeElement ? document.activeElement.tagName : "";
    const isInput = activeTag === "INPUT" || activeTag === "TEXTAREA";

    if (e.key === "Escape") {
      closeBrowser();
      closeRenameModal();
      closeDeleteModal();
      shortcutsModal.classList.add("hidden");
      const themeModal = document.getElementById("themeCustomizerModal");
      if (themeModal && !themeModal.classList.contains("hidden")) {
        themeModal.classList.add("hidden");
        return;
      }
      if (transferDropdown) transferDropdown.classList.add("hidden");
      return;
    }

    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !isInput) {
      e.preventDefault();
      undoLastMove();
      return;
    }

    if (isInput) return;

    // Recent Destinations Shortcuts: 1..6 (No modifier keys)
    if (!e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      if (e.key >= "1" && e.key <= "6") {
        const idx = parseInt(e.key, 10) - 1;
        if (idx >= 0 && idx < state.recentDestinations.length) {
          e.preventDefault();
          executeMove(state.recentDestinations[idx]);
          return;
        }
      }
    }

    switch (e.key.toLowerCase()) {
      case " ":
        e.preventDefault();
        if (videoPlayer.paused) {
          videoPlayer.play().catch(() => {});
        } else {
          videoPlayer.pause();
        }
        break;
      case "s":
      case "arrowright":
        e.preventDefault();
        skipVideo();
        break;
      case "p":
      case "arrowleft":
        e.preventDefault();
        prevVideo();
        break;
      case "u":
        e.preventDefault();
        undoLastMove();
        break;
      case "r":
        e.preventDefault();
        openRenameModal();
        break;
      case "d":
        e.preventDefault();
        openDeleteModal();
        break;
      case "x":
        if (state.currentDuplicate && duplicateBanner && !duplicateBanner.classList.contains("hidden")) {
          e.preventDefault();
          trashCurrentDuplicate();
        }
        break;
      case "/":
        e.preventDefault();
        folderSearchInput.focus();
        folderSearchInput.select();
        break;
      case "l":
        videoPlayer.loop = !videoPlayer.loop;
        showToast(videoPlayer.loop ? "Loop enabled" : "Loop disabled", "info", 1500);
        break;
      case "f":
        if (document.fullscreenElement) {
          document.exitFullscreen();
        } else {
          videoPlayer.requestFullscreen().catch(() => {});
        }
        break;
      case "t":
        e.preventDefault();
        const themeBtn = document.getElementById("themeModalBtn");
        if (themeBtn) themeBtn.click();
        break;
      case "?":
        shortcutsModal.classList.toggle("hidden");
        break;
    }
  });

  initThemeCustomizer();
  setupSorterTouchSeek();
}

function initThemeCustomizer() {
  const themeModalBtn = document.getElementById("themeModalBtn");
  const themeModal = document.getElementById("themeCustomizerModal");
  const closeThemeModalBtn = document.getElementById("closeThemeModalBtn");
  const applyThemeModalCloseBtn = document.getElementById("applyThemeModalCloseBtn");
  const resetThemeDefaultsBtn = document.getElementById("resetThemeDefaultsBtn");
  const themeAccentPicker = document.getElementById("themeAccentPicker");
  const themeBasePicker = document.getElementById("themeBasePicker");
  const mobileDoubleTapSeekToggle = document.getElementById("mobileDoubleTapSeekToggle");

  if (!themeModal) return;

  function renderPickers() {
    if (!window.THEME_MANAGER) return;
    const cur = window.THEME_MANAGER.getCurrent();
    const accents = window.THEME_MANAGER.getAccents();
    const bases = window.THEME_MANAGER.getBases();

    if (themeAccentPicker) {
      themeAccentPicker.innerHTML = accents.map(acc => {
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

      themeAccentPicker.querySelectorAll('.theme-accent-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const accKey = btn.dataset.accent;
          const currentBase = window.THEME_MANAGER.getCurrent().base;
          window.THEME_MANAGER.setTheme(accKey, currentBase);
          renderPickers();
          showToast(`🎨 Accent changed to ${accKey.charAt(0).toUpperCase() + accKey.slice(1)}`, 'success', 2000);
        });
      });
    }

    if (themeBasePicker) {
      themeBasePicker.innerHTML = bases.map(b => {
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

      themeBasePicker.querySelectorAll('.theme-base-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const baseKey = btn.dataset.base;
          const currentAcc = window.THEME_MANAGER.getCurrent().accent;
          window.THEME_MANAGER.setTheme(currentAcc, baseKey);
          renderPickers();
          showToast(`🌌 Theme changed to ${btn.querySelector('span:last-child').textContent}`, 'success', 2000);
        });
      });
    }

    if (mobileDoubleTapSeekToggle) {
      mobileDoubleTapSeekToggle.checked = localStorage.getItem('lib_touch_double_tap_seek') !== 'false';
    }
  }

  function openModal() {
    renderPickers();
    document.body.classList.add("modal-scroll-lock");
    themeModal.classList.remove("hidden");
    themeModal.classList.add("flex");
  }

  function closeModal() {
    document.body.classList.remove("modal-scroll-lock");
    themeModal.classList.add("hidden");
    themeModal.classList.remove("flex");
  }

  if (themeModalBtn) themeModalBtn.addEventListener("click", openModal);
  if (closeThemeModalBtn) closeThemeModalBtn.addEventListener("click", closeModal);
  if (applyThemeModalCloseBtn) applyThemeModalCloseBtn.addEventListener("click", closeModal);
  themeModal.addEventListener("click", (e) => {
    if (e.target === themeModal) closeModal();
  });

  if (resetThemeDefaultsBtn) {
    resetThemeDefaultsBtn.addEventListener("click", () => {
      if (window.THEME_MANAGER) window.THEME_MANAGER.setTheme('amber', 'stone');
      localStorage.setItem('lib_touch_double_tap_seek', 'true');
      if (mobileDoubleTapSeekToggle) mobileDoubleTapSeekToggle.checked = true;
      renderPickers();
      showToast('🎨 Theme reset to default', 'info', 2000);
    });
  }

  if (mobileDoubleTapSeekToggle) {
    mobileDoubleTapSeekToggle.addEventListener("change", (e) => {
      localStorage.setItem('lib_touch_double_tap_seek', e.target.checked ? 'true' : 'false');
      showToast(e.target.checked ? 'Double-tap seek enabled (±5s)' : 'Double-tap seek disabled', 'info', 2000);
    });
  }
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

function escapeHtml(text) {
  if (!text) return "";
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
