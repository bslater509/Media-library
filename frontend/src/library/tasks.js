import { icon } from '../shared/icons.js';
import { computeTaskSummary, describePrimaryTask, formatTaskCount } from '../shared/tasks_badge.js';
import { els, state } from './state.js';
import { urlDownloadRowHtml, urlDownloadRowsHtml, urlDownloadsActivityCount, urlDownloadsSummaryText } from './url_downloads.js';
import { escapeHtml, safeAttrEscape, showToast } from './utils.js';
import { renderAnimatedThumbStatus, updateCardsAnimatedStatus } from './animations.js';
import { updateCardsTranscodeStatus } from './gallery.js';
import { renderIndexStatus } from './search.js';
import { renderListExpander, sheetListSlice } from './sheet.js';
import { updateMobileControlsBadge } from './theme.js';
import { uploadState } from './uploads.js';
import { pollDuplicateHashStatus, renderBatchUnpackStatus, renderBatchZipStatus, renderDuplicateHashStatus, renderLibBackupStatus, renderMetadataStatus, renderThumbnailStatus, renderVisualHashStatus, renderWebpStatus, renderZipStatus } from './workers.js';
import { handleZipStatusUpdate } from './zip.js';

// Unified system status polling, transfers, transcodes and URL downloads.
// Extracted from the former monolithic library index.js.

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
      if (data.metadata) {
        state.latestMetadataData = data.metadata;
        renderMetadataStatus(data.metadata);
      }
      if (data.thumb_hash) {
        state.latestThumbHashData = data.thumb_hash;
        renderVisualHashStatus(data.thumb_hash);
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
  const metaData = state.latestMetadataData;
  const hashData = state.latestThumbHashData;

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
    metadata: metaData,
    thumbHash: hashData,
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

  // Update tab badges and empty state in tasks modal. The tab badge mirrors
  // the same active+queued item count as the header pill, so a nonzero badge
  // always has a matching row somewhere in the Active & Queues pane.
  const activeQueueCount = (trData && trData.active ? 1 : 0) +
    (trData && trData.queued ? trData.queued.length : 0) +
    (libOpsData && libOpsData.active ? 1 : 0) +
    (libOpsData && libOpsData.queued ? libOpsData.queued.length : 0) +
    (tData && tData.active ? 1 : 0) +
    (tData && tData.queued ? tData.queued.length : 0) +
    (animData && animData.active ? 1 : 0) +
    (animData && animData.queued ? animData.queued.length : 0) +
    (uploadState.activeUploads || 0) +
    uploadState.queue.filter((f) => f.status === 'pending').length +
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
    const isAnyWorkerActive =
      (idxData && idxData.status === 'crawling') ||
      (thData && thData.status === 'generating' && thData.pending_count > 0) ||
      (animData && (animData.status === 'generating' || animData.total_queued > 0)) ||
      (wData && wData.status === 'converting' && wData.pending_count > 0) ||
      (metaData && metaData.status === 'probing') ||
      (hashData && (hashData.status === 'hashing' || hashData.status === 'migrating')) ||
      (state.latestDupHashData && (state.latestDupHashData.status === 'hashing' || state.latestDupHashData.pending_count > 0));
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

  updateZipTasksSummary();

  // Footer status line reflects real activity instead of a static claim.
  const globalStatusEl = document.getElementById('tasksModalGlobalStatus');
  if (globalStatusEl) {
    globalStatusEl.textContent = tasksSummary.active
      ? `Active${tasksLabel ? ` · ${tasksLabel}` : ''}`
      : 'System ready · All background services idle';
  }
}
// Single owner of the "Active Archive Operations" summary so the ZIP task
// panel and the two batch runners cannot overwrite each other.
function updateZipTasksSummary() {
  if (!els.zipTasksSummary) return;
  const zipData = state.latestZipData;
  const batchZipData = state.latestBatchZipData;
  const batchUnpackData = state.latestBatchUnpackData;
  const parts = [];
  const activeZip = zipData && Array.isArray(zipData.tasks)
    ? zipData.tasks.filter((t) => t.status === 'running').length
    : 0;
  if (activeZip > 0) parts.push(`${activeZip} active`);
  if (batchZipData && (batchZipData.status === 'running' || batchZipData.status === 'paused')) {
    parts.push(`Batch: ${batchZipData.current_index}/${batchZipData.total_folders}`);
  }
  if (batchUnpackData && (batchUnpackData.status === 'running' || batchUnpackData.status === 'paused')) {
    parts.push(`Extract: ${batchUnpackData.current_index}/${batchUnpackData.total_zips}`);
  }
  els.zipTasksSummary.textContent = parts.length ? parts.join(' · ') : 'Idle';
}
// Non-MP4 count badge on the Tools card's "Batch Convert Videos to MP4"
// button. Refreshed when the Tools tab is opened (index-only summary query).
async function updateConvertAllBadge() {
  const badge = document.getElementById('convertAllLibraryBadge');
  if (!badge) return;
  try {
    const res = await fetch('/api/backup/transcode/summary');
    if (!res.ok) return;
    const data = await res.json();
    const n = data.pending_count || 0;
    badge.textContent = n > 99 ? '99+' : String(n);
    badge.classList.toggle('hidden', n === 0);
  } catch (e) {
    /* transient network hiccup; retried next time the tab opens */
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
        <span class="truncate text-slate-300 font-mono text-[11px] flex-1 min-w-0">${escapeHtml(q.filename)}</span>
        <span class="text-[10px] text-emerald-400 font-mono shrink-0">→ /${escapeHtml(q.destination_folder)}</span>
      </div>
    `).join('') + renderListExpander('queuedTransfers', queued.length, 10);
  } else {
    els.queuedTransfersContainer.classList.add('hidden');
    els.queuedTransfersList.innerHTML = '';
    if (els.queuedTransfersCount) els.queuedTransfersCount.textContent = '0';
  }
}
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
// URL download jobs render on two in-library surfaces: the desktop
// Background Tasks modal (Active & Queues) and the mobile Controls & Settings
// "Downloads" tab. The /downloads page remains the full management surface
// (history, retry, clear), so the modal shows only live/queued jobs + cancel.
function renderUrlDownloadsStatus(data) {
  renderMobileUrlDownloadsStatus(data);
  renderDesktopUrlDownloadsStatus(data);
}
function renderDesktopUrlDownloadsStatus(data) {
  const section = document.getElementById('urlDownloadsSection');
  const listEl = document.getElementById('urlDownloadsList');
  if (!section || !listEl) return;
  const count = urlDownloadsActivityCount(data);
  section.classList.toggle('hidden', count === 0);
  if (count === 0) {
    listEl.innerHTML = '';
    return;
  }
  const summaryEl = document.getElementById('urlDownloadsSummary');
  if (summaryEl) summaryEl.textContent = urlDownloadsSummaryText(data);
  listEl.innerHTML = [...((data && data.active) || []), ...((data && data.queued) || [])]
    .map((item) => urlDownloadRowHtml(item, { retryable: false }))
    .join('');
  bindUrlDownloadCancelButtons(listEl);
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
    ? 'px-2.5 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-[10px] font-semibold transition cursor-pointer'
    : 'px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-emerald-300 hover:text-white border border-slate-700 rounded-lg text-[10px] font-semibold transition cursor-pointer';

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
        <span class="truncate text-slate-300 font-mono text-[11px] flex-1 min-w-0">${escapeHtml(q.filename)}</span>
        <button onclick="window.libraryApp.cancelTranscode('${safeAttrEscape(q.id)}')" class="text-[10px] text-red-400 hover:text-red-300 px-1.5 py-0.5 bg-red-950/40 hover:bg-red-900/60 rounded border border-red-800/50 shrink-0">Cancel</button>
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
      const statusIcon = icon(isSuccess ? 'check' : 'x', 'w-3 h-3');
      const color = isSuccess ? 'text-emerald-400' : 'text-red-400';
      return `
        <div class="flex items-center justify-between gap-2 text-[10px] font-mono py-0.5 border-b border-slate-800/50 last:border-0">
          <span class="truncate text-slate-300 flex-1 min-w-0">${escapeHtml(h.filename)}</span>
          <span class="${color} shrink-0 flex items-center gap-1">${statusIcon} ${escapeHtml(h.status)}</span>
        </div>
      `;
    }).join('') + renderListExpander('transcodeHistory', history.length, 8);
  } else {
    els.transcodeHistoryList.innerHTML = `<span class="text-slate-600 italic">No conversions yet</span>`;
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

export { LIBOP_BADGES, LIBOP_LABELS, bindUrlDownloadCancelButtons, cancelAllTranscodes, cancelTranscode, cancelUrlDownloadJob, closeConvertAllModal, isPollingSystem, openConvertAllModal, pollSystemStatus, pollTranscodeStatus, pollTransfersStatus, renderDesktopUrlDownloadsStatus, renderLibraryOpsStatus, renderMobileUrlDownloadsStatus, renderTranscodeStatus, renderTransferStatus, renderUrlDownloadsStatus, submitConvertAll, systemPollTimer, toggleDuplicateHashPause, updateConvertAllBadge, updateHeaderTaskBadge, updateZipTasksSummary };
