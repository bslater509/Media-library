import { els, state } from './state.js';
import { escapeHtml, showToast } from './utils.js';
import { loadFolder } from './folders.js';
import { applyFiltersAndSort } from './gallery.js';
import { pollSystemStatus, updateHeaderTaskBadge } from './tasks.js';

// Background worker cards: thumbnails, hashing, metadata, WebP, backup and ZIP status.
// Extracted from the former monolithic library index.js.

// Poll Thumbnail Status & Update UI
function pollThumbnailStatus() {
  pollSystemStatus(true);
}
// Render Thumbnail Generator Status
function renderThumbnailStatus(data) {
  if (!els.thumbSummaryText) return;
  const isPaused = data.is_paused || data.status === 'paused';
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
      ? 'px-2.5 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-[10px] font-semibold transition cursor-pointer shrink-0'
      : 'px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-emerald-300 hover:text-white border border-slate-700 rounded-lg text-[10px] font-semibold transition cursor-pointer shrink-0';
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
      ? 'px-2.5 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-[10px] font-semibold transition cursor-pointer shrink-0'
      : 'px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-emerald-300 hover:text-white border border-slate-700 rounded-lg text-[10px] font-semibold transition cursor-pointer shrink-0';
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
// Render Media Metadata Probe worker status (natural-ratio tile dimensions).
function renderMetadataStatus(data) {
  if (!els.metaWorkerSummaryText) return;
  const isPaused = data.status === 'paused';
  const isProbing = data.status === 'probing';
  const pending = data.pending_count || 0;
  const total = data.total_files || 0;
  const zipPending = data.zip_pending_count || 0;
  const isDone = !isProbing && pending === 0 && total > 0;

  if (isPaused) {
    els.metaWorkerSummaryText.textContent = 'Paused';
    if (els.metaWorkerStatusLabel) els.metaWorkerStatusLabel.textContent = 'Paused';
  } else if (isProbing) {
    els.metaWorkerSummaryText.textContent = 'Probing';
    if (els.metaWorkerStatusLabel) els.metaWorkerStatusLabel.textContent = 'Active';
  } else if (isDone) {
    els.metaWorkerSummaryText.textContent = '100% Probed';
    if (els.metaWorkerStatusLabel) els.metaWorkerStatusLabel.textContent = 'Complete';
  } else {
    els.metaWorkerSummaryText.textContent = 'Idle';
    if (els.metaWorkerStatusLabel) els.metaWorkerStatusLabel.textContent = 'Idle';
  }

  if (els.metaWorkerCurrentFile) {
    if (isProbing && data.current_file) {
      els.metaWorkerCurrentFile.textContent = data.current_file;
      els.metaWorkerCurrentFile.title = data.current_file;
    } else if (isPaused) {
      els.metaWorkerCurrentFile.textContent = 'Metadata probing paused';
      els.metaWorkerCurrentFile.title = '';
    } else if (isDone) {
      els.metaWorkerCurrentFile.textContent = `All ${total.toLocaleString()} files probed`;
      els.metaWorkerCurrentFile.title = '';
    } else {
      els.metaWorkerCurrentFile.textContent = 'Idle';
      els.metaWorkerCurrentFile.title = '';
    }
  }

  const pct = typeof data.percent === 'number' ? data.percent : (total > 0 ? 100 : 0);
  if (els.metaWorkerPercentText) els.metaWorkerPercentText.textContent = `${pct.toFixed(1)}%`;
  if (els.metaWorkerProgressBar) els.metaWorkerProgressBar.style.width = `${pct}%`;
  if (els.metaWorkerCountsText) {
    const processed = Math.max(0, total - pending);
    let text = `${processed.toLocaleString()} / ${total.toLocaleString()} probed`;
    if (zipPending > 0) text += ` · ${zipPending.toLocaleString()} ZIP pending`;
    els.metaWorkerCountsText.textContent = text;
  }
}
// Render Visual Hash Indexer worker status (64-bit pHash for visual duplicates).
function renderVisualHashStatus(data) {
  if (!els.vhashSummaryText) return;
  const isPaused = data.status === 'paused';
  const isMigrating = data.status === 'migrating';
  const isHashing = data.status === 'hashing';
  const pending = data.pending_count || 0;
  const total = data.total_thumbnails || 0;
  const isDone = !isHashing && !isMigrating && !isPaused && pending === 0 && total > 0;

  if (isPaused) {
    els.vhashSummaryText.textContent = 'Paused';
    if (els.vhashStatusLabel) els.vhashStatusLabel.textContent = 'Paused';
  } else if (isMigrating) {
    els.vhashSummaryText.textContent = 'Migrating';
    if (els.vhashStatusLabel) els.vhashStatusLabel.textContent = 'Active';
  } else if (isHashing) {
    els.vhashSummaryText.textContent = 'Hashing';
    if (els.vhashStatusLabel) els.vhashStatusLabel.textContent = 'Active';
  } else if (isDone) {
    els.vhashSummaryText.textContent = '100% Hashed';
    if (els.vhashStatusLabel) els.vhashStatusLabel.textContent = 'Complete';
  } else {
    els.vhashSummaryText.textContent = 'Idle';
    if (els.vhashStatusLabel) els.vhashStatusLabel.textContent = 'Idle';
  }

  if (els.vhashCurrentFile) {
    if ((isHashing || isMigrating) && data.current_file) {
      els.vhashCurrentFile.textContent = data.current_file;
      els.vhashCurrentFile.title = data.current_file;
    } else if (isPaused) {
      els.vhashCurrentFile.textContent = 'Visual hashing paused';
      els.vhashCurrentFile.title = '';
    } else if (isDone) {
      els.vhashCurrentFile.textContent = `All ${total.toLocaleString()} posters hashed`;
      els.vhashCurrentFile.title = '';
    } else {
      els.vhashCurrentFile.textContent = 'Idle';
      els.vhashCurrentFile.title = '';
    }
  }

  const pct = typeof data.percent === 'number' ? data.percent : (total > 0 ? 100 : 0);
  if (els.vhashPercentText) els.vhashPercentText.textContent = `${pct.toFixed(1)}%`;
  if (els.vhashProgressBar) els.vhashProgressBar.style.width = `${pct}%`;
  if (els.vhashCountsText) {
    const hashed = data.hashed_count || 0;
    els.vhashCountsText.textContent = `${hashed.toLocaleString()} / ${total.toLocaleString()} hashed`;
  }
}
// Trigger a whole-library face scan ("Scan all" on the Face Recognition card).
async function triggerFaceScanAll() {
  try {
    if (els.triggerFaceScanAllBtn) els.triggerFaceScanAllBtn.disabled = true;
    showToast('Queueing the library for face scanning...', 'info');
    const res = await fetch('/api/people/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ all: true }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
    showToast(`Queued ${data.queued} file(s) for face scanning`, 'success');
  } catch (e) {
    showToast('Could not start the face scan: ' + e.message, 'error');
  } finally {
    // renderFaceStatus() owns the button's disabled state on the next poll.
    pollSystemStatus(true);
  }
}

// Coarse "how long is left" label for the face sweep.
function formatEta(seconds) {
  const total = Math.max(0, Number(seconds) || 0);
  if (!total) return '—';
  const hours = Math.floor(total / 3600);
  const minutes = Math.round((total % 3600) / 60);
  if (hours >= 24) return `${Math.floor(hours / 24)}d ${hours % 24}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${Math.max(1, minutes)}m`;
}

// Render Face Recognition / People worker status (opt-in, off by default).
function renderFaceStatus(data) {
  if (!els.faceWorkerSummaryText) return;
  const status = data.status || 'idle';
  const enabled = data.enabled !== false;
  const engineReady = data.engine_available !== false && !(data.engine && data.engine.available === false);
  const isPaused = data.is_paused || status === 'paused';
  const isScanning = status === 'scanning';
  const pending = data.pending_count || 0;
  const total = data.total_files || 0;
  const isDone = !isScanning && pending === 0 && total > 0;
  const faces = data.faces_total || 0;
  const people = data.people_total || 0;

  if (!enabled) {
    els.faceWorkerSummaryText.textContent = 'Off';
    if (els.faceWorkerStatusLabel) els.faceWorkerStatusLabel.textContent = 'Off';
  } else if (!engineReady || status === 'unavailable') {
    els.faceWorkerSummaryText.textContent = 'Engine unavailable';
    if (els.faceWorkerStatusLabel) els.faceWorkerStatusLabel.textContent = 'Blocked';
  } else if (isPaused) {
    els.faceWorkerSummaryText.textContent = 'Paused';
    if (els.faceWorkerStatusLabel) els.faceWorkerStatusLabel.textContent = 'Paused';
  } else if (isScanning) {
    els.faceWorkerSummaryText.textContent = 'Scanning';
    if (els.faceWorkerStatusLabel) els.faceWorkerStatusLabel.textContent = 'Active';
  } else if (isDone) {
    els.faceWorkerSummaryText.textContent = '100% Scanned';
    if (els.faceWorkerStatusLabel) els.faceWorkerStatusLabel.textContent = 'Complete';
  } else {
    els.faceWorkerSummaryText.textContent = 'Idle';
    if (els.faceWorkerStatusLabel) els.faceWorkerStatusLabel.textContent = 'Idle';
  }

  if (els.faceWorkerCurrentFile) {
    if (isScanning && data.current_file) {
      els.faceWorkerCurrentFile.textContent = data.current_file;
      els.faceWorkerCurrentFile.title = data.current_file;
    } else if (isPaused) {
      els.faceWorkerCurrentFile.textContent = 'Face scanning paused';
      els.faceWorkerCurrentFile.title = '';
    } else if (!enabled) {
      els.faceWorkerCurrentFile.textContent = 'Face recognition disabled';
      els.faceWorkerCurrentFile.title = '';
    } else if (isDone) {
      els.faceWorkerCurrentFile.textContent = `All ${total.toLocaleString()} files scanned`;
      els.faceWorkerCurrentFile.title = '';
    } else {
      els.faceWorkerCurrentFile.textContent = 'Idle';
      els.faceWorkerCurrentFile.title = '';
    }
  }

  const pct = typeof data.percent === 'number' ? data.percent : (total > 0 ? 100 : 0);
  if (els.faceWorkerPercentText) els.faceWorkerPercentText.textContent = `${pct.toFixed(1)}%`;
  if (els.faceWorkerProgressBar) els.faceWorkerProgressBar.style.width = `${pct}%`;
  if (els.faceWorkerCountsText) {
    const processed = Math.max(0, total - pending);
    els.faceWorkerCountsText.textContent = `${processed.toLocaleString()} / ${total.toLocaleString()} scanned`;
  }
  if (els.faceWorkerPeopleText) {
    els.faceWorkerPeopleText.textContent = `${people.toLocaleString()} people · ${faces.toLocaleString()} faces`;
  }
  // How fast the sweep is going, so a parallel crawl's gain is visible instead
  // of just "it feels quicker". Rate and ETA only mean something while scanning.
  // Footage per second of wall clock says more than files per minute: a queue of
  // 90-minute films and a queue of clips are not the same work.
  const factor = Number(data.realtime_factor) || 0;
  const perMinute = Number(data.files_per_min) || 0;
  if (els.faceWorkerRateText) {
    els.faceWorkerRateText.textContent = isScanning && factor > 0 ? `${factor.toFixed(1)}x realtime` : '— realtime';
    els.faceWorkerRateText.title = perMinute > 0
      ? `${perMinute.toFixed(1)} files/min over the last few minutes`
      : 'How much footage the sweep clears per minute of real time';
  }
  if (els.faceWorkerEtaText) {
    const eta = Number(data.eta_seconds) || 0;
    els.faceWorkerEtaText.textContent = isScanning && eta > 0 ? `ETA ${formatEta(eta)}` : 'ETA —';
  }
  if (els.faceWorkerCacheText) {
    els.faceWorkerCacheText.textContent = `${data.cache_size_formatted || '0 B'} / ${data.cache_max_size_formatted || '—'} crops`;
  }
  if (els.faceWorkerHintText) {
    if (!enabled) {
      els.faceWorkerHintText.textContent = 'Set FACE_RECOGNITION_ENABLED=true and restart to scan faces. ZIP members are skipped.';
    } else if (!engineReady) {
      els.faceWorkerHintText.textContent = data.engine_reason || 'Waiting for the face models to download.';
    } else {
      els.faceWorkerHintText.textContent = 'Images are read from cached thumbnails, videos from sampled frames (each hit keeps its timestamp).';
    }
  }
  if (els.triggerFaceScanAllBtn) {
    els.triggerFaceScanAllBtn.disabled = !enabled || !engineReady;
  }
}

// Poll WebP Optimizer Status
function pollWebpStatus() {
  pollSystemStatus(true);
}// Render WebP Optimizer Status
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
      ? 'px-2.5 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-[10px] font-semibold transition cursor-pointer shrink-0'
      : 'px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-teal-300 hover:text-white border border-slate-700 rounded-lg text-[10px] font-semibold transition cursor-pointer shrink-0';
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
function pollZipTasksStatus() {
  pollSystemStatus(true);
}
// Render ZIP Tasks in dropdown panel
function renderZipStatus(data) {
  if (!els.zipTasksList) return;
  const tasks = data.tasks || [];

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
          <span class="font-medium text-slate-200 truncate max-w-[180px]">${escapeHtml(t.filename)}</span>
          <span class="${statusColor} font-mono text-[10px]">${t.status === 'running' ? Math.round(t.percent) + '%' : escapeHtml(t.status)}</span>
        </div>
        <div class="w-full bg-slate-800 rounded-full h-1 overflow-hidden">
          <div class="bg-sky-500 h-1 rounded-full transition-all" style="width: ${t.percent || 0}%"></div>
        </div>
        <span class="text-[10px] text-slate-400 truncate">${typeLabel}: ${escapeHtml(t.message || t.status)}</span>
      </div>
    `;
  }).join('');
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

export { pollBatchUnpackStatus, pollBatchZipStatus, pollDuplicateHashStatus, pollLibBackupStatus, pollThumbnailStatus, pollWebpStatus, pollZipTasksStatus, renderBatchUnpackStatus, renderBatchZipStatus, renderDuplicateHashStatus, renderFaceStatus, renderLibBackupStatus, renderMetadataStatus, renderThumbnailStatus, renderVisualHashStatus, renderWebpStatus, renderZipStatus, toggleThumbPause, toggleWebpPause, triggerFaceScanAll, triggerFolderThumbnails, triggerFolderWebp, triggerLibBackup, triggerLibRestore };
