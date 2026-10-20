import { icon } from '../shared/icons.js';
import { computeTaskSummary, describePrimaryTask, formatTaskCount } from '../shared/tasks_badge.js';
import { currentVideo, refreshQueue } from './config.js';
import { activeTranscodeBox, activeTranscodeEta, activeTranscodeFilename, activeTranscodeMethodBadge, activeTranscodePercent, activeTranscodeProgressBar, activeTranscodeSpeed, activeTransferBox, activeTransferFilename, activeTransferPercent, activeTransferProgressBar, activeTransferSize, activeTransferTarget, cleanDbBtn, currentVideoTitle, faceWorkerCacheText, faceWorkerCountsText, faceWorkerCurrentFile, faceWorkerEtaText, faceWorkerHintText, faceWorkerRateText, faceWorkerPeopleText, faceWorkerPercentText, faceWorkerProgressBar, faceWorkerStatusLabel, faceWorkerSummaryText, hashCountsText, hashCurrentFile, hashPercentText, hashProgressBar, hashStatusLabel, hashSummaryText, indexCountsText, indexCurrentFolder, indexFilesText, indexForcePruneBtn, indexPercentText, indexProgressBar, indexStaleText, indexStaleWarning, indexSummaryText, indexSyncDropdownIcon, metaWorkerCountsText, metaWorkerCurrentFile, metaWorkerPercentText, metaWorkerProgressBar, metaWorkerStatusLabel, metaWorkerSummaryText, queuedTranscodesContainer, queuedTranscodesList, queuedTransfersList, thumbCountsText, thumbCurrentFile, thumbPercentText, thumbProgressBar, thumbStatusLabel, thumbSummaryText, toggleHashPauseBtn, toggleThumbPauseBtn, toggleTranscodePauseBtn, toggleWebpPauseBtn, transcodeStatusBadge, transcodeSummaryText, transferBadgeBtn, transferCountText, transferDropdownSummary, transferHistoryList, transferIcon, transferSpinner, triggerIndexSyncDropdownBtn, vhashCountsText, vhashCurrentFile, vhashPercentText, vhashProgressBar, vhashStatusLabel, vhashSummaryText, videoPlayer, webpCountsText, webpCurrentFile, webpPercentText, webpProgressBar, webpSavedText, webpSummaryText } from './elements.js';
import { escapeHtml, showToast, state } from './state.js';
import { loadVideoSource } from './video.js';

// Background status polling and worker/transfer rendering.
// Split out of the former monolithic sorter index.js.

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
  if (sysData.thumb_hash) {
    state.latestThumbHashData = sysData.thumb_hash;
    renderVisualHashUI(sysData.thumb_hash);
  }
  if (sysData.metadata) {
    state.latestMetadataData = sysData.metadata;
    renderMetadataUI(sysData.metadata);
  }
  if (sysData.faces) {
    state.latestFacesData = sysData.faces;
    renderFacesUI(sysData.faces);
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
  metadata: state.latestMetadataData,
  thumbHash: state.latestThumbHashData,
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

// Update tab badges and empty state in tasks modal. The Active tab badge /
// empty state track only the queue rows the Active & Queues pane actually
// renders (transfers + transcodes), so a nonzero badge always has a matching
// row; worker-only sweeps (index, thumbnails, WebP, hashing) light the
// Background Workers tab instead.
const activeQueueCount =
  (state.activeTransfer ? 1 : 0) +
  (Array.isArray(state.queuedTransfers) ? state.queuedTransfers.length : 0) +
  (state.activeTranscode ? 1 : 0) +
  (Array.isArray(state.queuedTranscodes) ? state.queuedTranscodes.length : 0);
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
  const isDupHashing = state.latestDupHashData && (state.latestDupHashData.status === "hashing" || state.latestDupHashData.pending_count > 0);
  const isThumbHashing = state.latestThumbHashData && (state.latestThumbHashData.status === "hashing" || state.latestThumbHashData.status === "migrating");
  const isMetadataProbing = state.latestMetadataData && state.latestMetadataData.status === "probing";
  const isAnyWorkerActive = isThumbGenerating || isWebpConverting || isIndexing || isDupHashing || isThumbHashing || isMetadataProbing;
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

// Footer status line reflects real activity instead of a static claim.
const globalStatusEl = document.getElementById("tasksModalGlobalStatus");
if (globalStatusEl) {
  globalStatusEl.textContent = tasksSummary.active
    ? `Active${tasksLabel ? ` · ${tasksLabel}` : ''}`
    : 'System ready · All background services idle';
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
const isPaused = data.is_paused || data.status === "paused";
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
// Media Metadata Probe (dimensions / duration / codec indexing)
function renderMetadataUI(data) {
if (!metaWorkerSummaryText) return;
const isPaused = data.status === "paused";
const isProbing = data.status === "probing";
const pending = data.pending_count || 0;
const total = data.total_files || 0;
const zipPending = data.zip_pending_count || 0;
const isDone = !isProbing && pending === 0 && total > 0;

if (isPaused) {
  metaWorkerSummaryText.textContent = "Paused";
  if (metaWorkerStatusLabel) metaWorkerStatusLabel.textContent = "Paused";
} else if (isProbing) {
  metaWorkerSummaryText.textContent = "Probing";
  if (metaWorkerStatusLabel) metaWorkerStatusLabel.textContent = "Active";
} else if (isDone) {
  metaWorkerSummaryText.textContent = "100% Probed";
  if (metaWorkerStatusLabel) metaWorkerStatusLabel.textContent = "Complete";
} else {
  metaWorkerSummaryText.textContent = "Idle";
  if (metaWorkerStatusLabel) metaWorkerStatusLabel.textContent = "Idle";
}

if (metaWorkerCurrentFile) {
  if (isProbing && data.current_file) {
    metaWorkerCurrentFile.textContent = data.current_file;
    metaWorkerCurrentFile.title = data.current_file;
  } else if (isDone) {
    metaWorkerCurrentFile.textContent = `All ${total.toLocaleString()} files probed`;
  } else {
    metaWorkerCurrentFile.textContent = data.current_file || (isPaused ? "Metadata probing paused" : "Idle");
    metaWorkerCurrentFile.title = isPaused ? "" : (data.current_file || "");
  }
}

const pct = typeof data.percent === "number" ? data.percent : (total > 0 ? 100 : 0);
if (metaWorkerPercentText) metaWorkerPercentText.textContent = `${pct.toFixed(1)}%`;
if (metaWorkerProgressBar) metaWorkerProgressBar.style.width = `${pct}%`;
if (metaWorkerCountsText) {
  const processed = Math.max(0, total - pending);
  let text = `${processed.toLocaleString()} / ${total.toLocaleString()} probed`;
  if (zipPending > 0) text += ` · ${zipPending.toLocaleString()} ZIP pending`;
  metaWorkerCountsText.textContent = text;
}
}
// Visual Hash Indexer (64-bit pHash for visual duplicate detection)
function renderVisualHashUI(data) {
if (!vhashSummaryText) return;
const isPaused = data.status === "paused";
const isMigrating = data.status === "migrating";
const isHashing = data.status === "hashing";
const pending = data.pending_count || 0;
const total = data.total_thumbnails || 0;
const isDone = !isHashing && !isMigrating && !isPaused && pending === 0 && total > 0;

if (isPaused) {
  vhashSummaryText.textContent = "Paused";
  if (vhashStatusLabel) vhashStatusLabel.textContent = "Paused";
} else if (isMigrating) {
  vhashSummaryText.textContent = "Migrating";
  if (vhashStatusLabel) vhashStatusLabel.textContent = "Active";
} else if (isHashing) {
  vhashSummaryText.textContent = "Hashing";
  if (vhashStatusLabel) vhashStatusLabel.textContent = "Active";
} else if (isDone) {
  vhashSummaryText.textContent = "100% Hashed";
  if (vhashStatusLabel) vhashStatusLabel.textContent = "Complete";
} else {
  vhashSummaryText.textContent = "Idle";
  if (vhashStatusLabel) vhashStatusLabel.textContent = "Idle";
}

if (vhashCurrentFile) {
  if ((isHashing || isMigrating) && data.current_file) {
    vhashCurrentFile.textContent = data.current_file;
    vhashCurrentFile.title = data.current_file;
  } else if (isDone) {
    vhashCurrentFile.textContent = `All ${total.toLocaleString()} posters hashed`;
  } else {
    vhashCurrentFile.textContent = data.current_file || (isPaused ? "Visual hashing paused" : "Idle");
    vhashCurrentFile.title = isPaused ? "" : (data.current_file || "");
  }
}

const pct = typeof data.percent === "number" ? data.percent : (total > 0 ? 100 : 0);
if (vhashPercentText) vhashPercentText.textContent = `${pct.toFixed(1)}%`;
if (vhashProgressBar) vhashProgressBar.style.width = `${pct}%`;
if (vhashCountsText) {
  vhashCountsText.textContent = `${(data.hashed_count || 0).toLocaleString()} / ${total.toLocaleString()} hashed`;
}
}
// Face Recognition / People (opt-in; dormant until FACE_RECOGNITION_ENABLED)
function formatFaceEta(seconds) {
  const total = Math.max(0, Number(seconds) || 0);
  if (!total) return "—";
  const hours = Math.floor(total / 3600);
  const minutes = Math.round((total % 3600) / 60);
  if (hours >= 24) return `${Math.floor(hours / 24)}d ${hours % 24}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${Math.max(1, minutes)}m`;
}

function renderFacesUI(data) {
if (!faceWorkerSummaryText) return;
const status = data.status || "idle";
const enabled = data.enabled !== false;
const engineReady = data.engine_available !== false && !(data.engine && data.engine.available === false);
const isPaused = data.is_paused || status === "paused";
const isScanning = status === "scanning";
const pending = data.pending_count || 0;
const total = data.total_files || 0;
const isDone = !isScanning && pending === 0 && total > 0;

if (!enabled) {
  faceWorkerSummaryText.textContent = "Off";
  if (faceWorkerStatusLabel) faceWorkerStatusLabel.textContent = "Off";
} else if (!engineReady || status === "unavailable") {
  faceWorkerSummaryText.textContent = "Engine unavailable";
  if (faceWorkerStatusLabel) faceWorkerStatusLabel.textContent = "Blocked";
} else if (isPaused) {
  faceWorkerSummaryText.textContent = "Paused";
  if (faceWorkerStatusLabel) faceWorkerStatusLabel.textContent = "Paused";
} else if (isScanning) {
  faceWorkerSummaryText.textContent = "Scanning";
  if (faceWorkerStatusLabel) faceWorkerStatusLabel.textContent = "Active";
} else if (isDone) {
  faceWorkerSummaryText.textContent = "100% Scanned";
  if (faceWorkerStatusLabel) faceWorkerStatusLabel.textContent = "Complete";
} else {
  faceWorkerSummaryText.textContent = "Idle";
  if (faceWorkerStatusLabel) faceWorkerStatusLabel.textContent = "Idle";
}

if (faceWorkerCurrentFile) {
  if (isScanning && data.current_file) {
    faceWorkerCurrentFile.textContent = data.current_file;
    faceWorkerCurrentFile.title = data.current_file;
  } else if (!enabled) {
    faceWorkerCurrentFile.textContent = "Face recognition disabled";
    faceWorkerCurrentFile.title = "";
  } else if (isPaused) {
    faceWorkerCurrentFile.textContent = "Face scanning paused";
    faceWorkerCurrentFile.title = "";
  } else if (isDone) {
    faceWorkerCurrentFile.textContent = `All ${total.toLocaleString()} files scanned`;
    faceWorkerCurrentFile.title = "";
  } else {
    faceWorkerCurrentFile.textContent = "Idle";
    faceWorkerCurrentFile.title = "";
  }
}

const pct = typeof data.percent === "number" ? data.percent : (total > 0 ? 100 : 0);
if (faceWorkerPercentText) faceWorkerPercentText.textContent = `${pct.toFixed(1)}%`;
if (faceWorkerProgressBar) faceWorkerProgressBar.style.width = `${pct}%`;
if (faceWorkerCountsText) {
  const processed = Math.max(0, total - pending);
  faceWorkerCountsText.textContent = `${processed.toLocaleString()} / ${total.toLocaleString()} scanned`;
}
if (faceWorkerPeopleText) {
  faceWorkerPeopleText.textContent = `${(data.people_total || 0).toLocaleString()} people · ${(data.faces_total || 0).toLocaleString()} faces`;
}
if (faceWorkerRateText) {
  const factor = Number(data.realtime_factor) || 0;
  faceWorkerRateText.textContent = isScanning && factor > 0 ? `${factor.toFixed(1)}x realtime` : "— realtime";
}
if (faceWorkerEtaText) {
  const eta = Number(data.eta_seconds) || 0;
  faceWorkerEtaText.textContent = isScanning && eta > 0 ? `ETA ${formatFaceEta(eta)}` : "ETA —";
}
if (faceWorkerCacheText) {
  faceWorkerCacheText.textContent = `${data.cache_size_formatted || "0 B"} / ${data.cache_max_size_formatted || "—"} crops`;
}
if (faceWorkerHintText) {
  if (!enabled) {
    faceWorkerHintText.textContent = "Set FACE_RECOGNITION_ENABLED=true and restart to scan faces. ZIP members are skipped.";
  } else if (!engineReady) {
    faceWorkerHintText.textContent = data.engine_reason || "Waiting for the face models to download.";
  } else {
    faceWorkerHintText.textContent = "Open the People page to name clusters and search by person.";
  }
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
const staleFolders = data.stale_folders || 0;
const staleFiles = data.stale_files || 0;
// Idle with rows pointing at folders the mount no longer has, and whose
// prune the mount-safety guard refused: the index needs a manual prune.
const isStale = !isCrawling && staleFolders > 0;
const pct = typeof data.percent === "number" ? data.percent : (total > 0 ? (scanned / total * 100) : 100);

if (indexSummaryText) {
  if (isCrawling) {
    indexSummaryText.textContent = "Indexing";
    indexSummaryText.className = "text-[10px] text-amber-400 font-mono font-medium animate-pulse";
  } else if (isStale) {
    indexSummaryText.textContent = "Index stale";
    indexSummaryText.className = "text-[10px] text-amber-400 font-mono font-medium";
  } else {
    indexSummaryText.textContent = "Indexed";
    indexSummaryText.className = "text-[10px] text-emerald-400 font-mono";
  }
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
  } else if (isStale) {
    indexCurrentFolder.textContent = `${staleFolders.toLocaleString()} folders missing on mount`;
    indexCurrentFolder.title = data.prune_reason || "";
  } else {
    indexCurrentFolder.textContent = "All folders indexed";
    indexCurrentFolder.title = "";
  }
}
if (indexStaleWarning) {
  indexStaleWarning.classList.toggle("hidden", !isStale);
}
if (indexStaleText) {
  if (isStale) {
    const fileNote = staleFiles > 0 ? ` (${staleFiles.toLocaleString()} files)` : "";
    indexStaleText.textContent = `${staleFolders.toLocaleString()} indexed folders${fileNote} no longer exist on the mount. Prune was blocked by the mount-safety guard${data.prune_reason ? `: ${data.prune_reason}` : ""}.`;
  } else {
    indexStaleText.textContent = "";
  }
}
if (indexPercentText) {
  indexPercentText.textContent = `${Math.min(100, Math.max(0, pct)).toFixed(0)}%`;
  indexPercentText.className = isStale
    ? "text-amber-400 font-mono text-[10px] font-bold shrink-0"
    : "text-emerald-400 font-mono text-[10px] font-bold shrink-0";
}
if (indexProgressBar) {
  indexProgressBar.style.width = `${Math.min(100, Math.max(0, pct))}%`;
  if (isCrawling) {
    indexProgressBar.className = "bg-gradient-to-r from-amber-500 to-emerald-400 h-1.5 rounded-full transition-all duration-200";
  } else if (isStale) {
    indexProgressBar.className = "bg-gradient-to-r from-amber-500 to-amber-400 h-1.5 rounded-full transition-all duration-200";
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

// Drop index rows for folders the mount no longer has. The crawler's automatic
// prune refuses to remove more than ~10% of the index at once (transient-mount
// protection), so a genuinely large remote deletion is confirmed by hand here.
if (indexForcePruneBtn) {
indexForcePruneBtn.addEventListener("click", async () => {
  const originalLabel = indexForcePruneBtn.textContent;
  indexForcePruneBtn.disabled = true;
  indexForcePruneBtn.classList.add("opacity-60", "pointer-events-none");
  indexForcePruneBtn.textContent = "Pruning...";
  showToast("Removing missing folders from the index...", "info");
  try {
    const res = await fetch("/api/backup/index/prune", { method: "POST" });
    const data = await res.json();
    const stats = (data && data.stats) || {};
    if (stats.aborted) {
      showToast(stats.reason || "Prune was aborted", "error");
    } else {
      showToast(
        `Pruned ${(stats.pruned_folders || 0).toLocaleString()} missing folders and ${(stats.pruned_files || 0).toLocaleString()} file rows`,
        "success"
      );
    }
    await pollTransfers(true);
  } catch (e) {
    showToast("Failed to prune index: " + e.message, "error");
  } finally {
    indexForcePruneBtn.disabled = false;
    indexForcePruneBtn.classList.remove("opacity-60", "pointer-events-none");
    indexForcePruneBtn.textContent = originalLabel || "Remove missing folders from index";
  }
});
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

export { cancelSpecificTranscode, cancelSpecificTransfer, checkIfCurrentVideoConverted, isPollingSystemStatus, pollDuplicateHashStatus, pollIndexStatus, pollThumbnailStatus, pollTransfers, pollWebpStatus, renderDuplicateHashUI, renderIndexUI, renderMetadataUI, renderThumbnailUI, renderTranscodeUI, renderTransfersUI, renderVisualHashUI, renderWebpUI, startBackgroundIndexPolling, startDuplicateHashPolling, startThumbnailPolling, startTransfersPolling, startWebpPolling, toggleDuplicateHashPause, toggleThumbnailPause, toggleTranscodePause, toggleWebpPause, updateCurrentVideoTranscodeBadge, updateHeaderTaskBadge };
