import { els, state } from './state.js';
import { escapeHtml, showToast } from './utils.js';
import { loadFolder, renderSidebarTree } from './folders.js';
import { applyFiltersAndSort } from './gallery.js';
import { pollBatchUnpackStatus, pollBatchZipStatus, pollZipTasksStatus } from './workers.js';

// ZIP compression, unpacking, batch ZIP/unpack and empty-folder cleanup.
// Extracted from the former monolithic library index.js.

// Handle ZIP task completion and progression
function handleZipStatusUpdate(data) {
  if (!data) return;

  // Check if extraction of current folder completed
  if (state.isZip && state.zipExtracting) {
    const currentTask = (data.tasks || []).find(t => t.path === state.currentPath && t.type === 'extract');
    if (currentTask) {
      if (els.zipExtractBanner) {
        els.zipExtractBanner.classList.remove('hidden');
        const pct = Math.round(currentTask.percent || 0);
        els.zipExtractPercent.textContent = `${pct}%`;
        els.zipExtractProgressBar.style.width = `${pct}%`;
        if (currentTask.message) els.zipExtractMsg.textContent = currentTask.message;
      }
      if (currentTask.status === 'completed') {
        state.zipExtracting = false;
        if (els.zipExtractBanner) els.zipExtractBanner.classList.add('hidden');
        loadFolder(state.currentPath, false);
      }
    } else {
      // Task completed or no longer running
      state.zipExtracting = false;
      if (els.zipExtractBanner) els.zipExtractBanner.classList.add('hidden');
    }
  }

  // Check compression progress for actively compressing folder
  if (state.compressingFolder) {
    const compTask = (data.tasks || []).find(t => t.path === state.compressingFolder && t.type === 'compress');
    if (compTask) {
      if (els.zipExtractBanner) {
        els.zipExtractBanner.classList.remove('hidden');
        const pct = Math.round(compTask.percent || 0);
        els.zipExtractPercent.textContent = `${pct}%`;
        els.zipExtractProgressBar.style.width = `${pct}%`;
        if (compTask.message) els.zipExtractMsg.textContent = compTask.message;
      }
      if (compTask.status === 'completed') {
        const targetZip = state.compressingTargetZip;
        state.compressingFolder = null;
        state.compressingTargetZip = null;
        if (els.zipExtractBanner) els.zipExtractBanner.classList.add('hidden');
        showToast('Folder replaced with ZIP archive! Opening...', 'success');
        loadFolder(targetZip, true, true);
      } else if (compTask.status === 'failed') {
        showToast(`Compression failed: ${compTask.error || compTask.message}`, 'error');
        state.compressingFolder = null;
        state.compressingTargetZip = null;
        if (els.zipExtractBanner) els.zipExtractBanner.classList.add('hidden');
      }
    } else {
      // Task completed before first poll or no longer in active list
      const targetZip = state.compressingTargetZip;
      state.compressingFolder = null;
      state.compressingTargetZip = null;
      if (els.zipExtractBanner) els.zipExtractBanner.classList.add('hidden');
      showToast('Compression finished! Opening ZIP archive...', 'success');
      loadFolder(targetZip, true, true);
    }
  }
}
// Open Compress ZIP Modal
async function openCompressZipModal() {
  if (!state.currentPath || state.isRoot || state.isZip) return;
  try {
    if (els.compressFolderZipBtn) {
      els.compressFolderZipBtn.disabled = true;
      if (els.zipBtnText) els.zipBtnText.textContent = 'Checking...';
    }
    const res = await fetch(`/api/backup/zip/check?folder_path=${encodeURIComponent(state.currentPath)}`);
    const check = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(check.detail || 'Server error checking folder eligibility', 'error');
      return;
    }
    if (!check.eligible) {
      showToast(check.reason || 'This folder cannot be compressed to ZIP', 'warning');
      return;
    }

    const folderName = state.currentPath.split('/').filter(Boolean).pop() || 'folder';
    if (els.compressFolderName) els.compressFolderName.textContent = folderName;
    if (els.compressImageCount) els.compressImageCount.textContent = `${check.image_count} image${check.image_count === 1 ? '' : 's'}`;
    if (els.compressTotalSize) els.compressTotalSize.textContent = check.total_size_formatted || '0 MB';
    if (els.compressTargetZip) els.compressTargetZip.textContent = `${folderName}.zip`;

    if (els.compressZipModal) {
      els.compressZipModal.classList.remove('hidden');
    }
  } catch (err) {
    console.error('Error checking compressibility:', err);
    showToast('Failed to check folder eligibility', 'error');
  } finally {
    if (els.compressFolderZipBtn) {
      els.compressFolderZipBtn.disabled = false;
      applyFiltersAndSort();
    }
  }
}
function closeCompressZipModal() {
  if (els.compressZipModal) els.compressZipModal.classList.add('hidden');
}
async function submitCompressZip() {
  if (!state.currentPath) return;
  const folderToCompress = state.currentPath;
  const folderName = folderToCompress.split('/').filter(Boolean).pop() || 'folder';
  try {
    if (els.confirmCompressZipBtn) els.confirmCompressZipBtn.disabled = true;
    const res = await fetch('/api/backup/zip/compress', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_path: folderToCompress }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Compression failed');
    }
    state.compressingFolder = folderToCompress;
    state.compressingTargetZip = data.target_zip || (folderToCompress + '.zip');
    closeCompressZipModal();
    showToast(data.message || 'Compressing folder to ZIP...', 'info');

    // Show compression progress banner
    if (els.zipExtractBanner) {
      els.zipExtractBanner.classList.remove('hidden');
      if (els.zipExtractMsg) els.zipExtractMsg.textContent = `Compressing ${folderName} to ZIP and replacing folder...`;
      if (els.zipExtractPercent) els.zipExtractPercent.textContent = '0%';
      if (els.zipExtractProgressBar) els.zipExtractProgressBar.style.width = '0%';
    }

    pollZipTasksStatus();
  } catch (err) {
    console.error('Error starting compression:', err);
    showToast(err.message || 'Failed to start compression', 'error');
  } finally {
    if (els.confirmCompressZipBtn) els.confirmCompressZipBtn.disabled = false;
  }
}
// Open Unpack ZIP Modal
function openUnpackZipModal() {
  if (!state.currentPath || !state.isZip) return;
  const fileName = state.currentPath.split('/').filter(Boolean).pop() || 'archive.zip';
  const folderName = fileName.replace(/\.zip$/i, '');
  if (els.unpackZipFileName) els.unpackZipFileName.textContent = fileName;
  if (els.unpackZipSource) els.unpackZipSource.textContent = fileName;
  if (els.unpackZipTarget) els.unpackZipTarget.textContent = folderName;

  if (els.unpackZipModal) els.unpackZipModal.classList.remove('hidden');
}
function closeUnpackZipModal() {
  if (els.unpackZipModal) els.unpackZipModal.classList.add('hidden');
}
async function submitUnpackZip() {
  if (!state.currentPath || !state.isZip) return;
  try {
    if (els.confirmUnpackZipBtn) els.confirmUnpackZipBtn.disabled = true;
    const res = await fetch('/api/backup/zip/unpack', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ zip_path: state.currentPath }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Unpack failed');
    }
    showToast(data.message || 'Unpacking started in background', 'info');
    closeUnpackZipModal();
    pollZipTasksStatus();
    if (data.target_folder) {
      setTimeout(() => loadFolder(data.target_folder, true), 1500);
    }
  } catch (err) {
    console.error('Error starting unpack:', err);
    showToast(err.message || 'Failed to start unpack', 'error');
  } finally {
    if (els.confirmUnpackZipBtn) els.confirmUnpackZipBtn.disabled = false;
  }
}
// Open Batch ZIP Modal
async function openBatchZipModal() {
  if (!els.batchZipModal) return;
  els.batchZipModal.classList.remove('hidden');
  if (els.batchZipCandidatesList) {
    els.batchZipCandidatesList.innerHTML = `
      <div class="col-span-full py-12 flex flex-col items-center justify-center gap-2 text-slate-400">
        <span class="w-6 h-6 border-2 border-sky-400 border-t-transparent rounded-full animate-spin"></span>
        <span class="text-xs">Scanning library for candidate image folders...</span>
      </div>
    `;
  }
  if (els.confirmBatchZipBtn) els.confirmBatchZipBtn.disabled = true;

  try {
    const res = await fetch('/api/backup/zip/batch/candidates');
    if (!res.ok) throw new Error('Failed to fetch candidate folders');
    const data = await res.json();

    state.batchZipCandidates = data.candidates || [];
    state.batchZipSelectedPaths = new Set(state.batchZipCandidates.map(c => c.path));
    state.batchZipFilterQuery = '';
    if (els.batchZipFilterInput) els.batchZipFilterInput.value = '';

    if (els.batchZipCandidateCount) els.batchZipCandidateCount.textContent = data.total_candidates;
    if (els.batchZipImageCount) els.batchZipImageCount.textContent = data.total_images;
    if (els.batchZipTotalSize) els.batchZipTotalSize.textContent = data.total_size_formatted;

    renderBatchZipCandidates();
  } catch (err) {
    console.error('Error opening batch zip modal:', err);
    showToast('Could not load candidate folders', 'error');
    if (els.batchZipCandidatesList) {
      els.batchZipCandidatesList.innerHTML = `<div class="text-center py-8 text-xs text-rose-400">Error loading candidates: ${escapeHtml(err.message)}</div>`;
    }
  }
}
function closeBatchZipModal() {
  if (els.batchZipModal) els.batchZipModal.classList.add('hidden');
}
function renderBatchZipCandidates() {
  if (!els.batchZipCandidatesList) return;
  const q = (state.batchZipFilterQuery || '').toLowerCase().trim();
  const filtered = state.batchZipCandidates.filter(c => 
    c.name.toLowerCase().includes(q) || 
    (c.parent_name && c.parent_name.toLowerCase().includes(q)) ||
    c.path.toLowerCase().includes(q)
  );

  if (filtered.length === 0) {
    els.batchZipCandidatesList.innerHTML = `
      <div class="text-center py-8 text-xs text-slate-500">
        ${state.batchZipCandidates.length === 0 ? 'No qualifying image-only folders found in library.' : 'No candidates match your search filter.'}
      </div>
    `;
    if (els.batchZipSelectedBadge) els.batchZipSelectedBadge.textContent = `0 of ${state.batchZipCandidates.length} selected`;
    if (els.confirmBatchZipBtn) els.confirmBatchZipBtn.disabled = true;
    if (els.confirmBatchZipBtnText) els.confirmBatchZipBtnText.textContent = 'Compress Selected (0)';
    return;
  }

  const selectedCount = state.batchZipSelectedPaths.size;
  const totalCount = state.batchZipCandidates.length;

  if (els.batchZipSelectedBadge) {
    els.batchZipSelectedBadge.textContent = `${selectedCount} of ${totalCount} selected`;
  }
  if (els.confirmBatchZipBtn) {
    els.confirmBatchZipBtn.disabled = selectedCount === 0;
  }
  if (els.confirmBatchZipBtnText) {
    els.confirmBatchZipBtnText.textContent = `Compress Selected (${selectedCount})`;
  }
  if (els.batchZipSelectAllToggle) {
    els.batchZipSelectAllToggle.checked = selectedCount === totalCount && totalCount > 0;
    els.batchZipSelectAllToggle.indeterminate = selectedCount > 0 && selectedCount < totalCount;
  }

  els.batchZipCandidatesList.innerHTML = filtered.map(c => {
    const isChecked = state.batchZipSelectedPaths.has(c.path);
    return `
      <div class="flex items-center justify-between gap-3 p-2 rounded-xl hover:bg-slate-800/50 transition cursor-pointer group" data-path="${escapeHtml(c.path)}">
        <div class="flex items-center gap-2.5 min-w-0 flex-1">
          <input 
            type="checkbox" 
            class="batch-zip-item-checkbox rounded bg-slate-800 border-slate-700 text-sky-600 focus:ring-sky-500 cursor-pointer"
            data-path="${escapeHtml(c.path)}"
            ${isChecked ? 'checked' : ''}
          />
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-1.5 text-xs font-semibold text-white truncate">
              <span>📁</span>
              <span class="truncate">${escapeHtml(c.name)}</span>
            </div>
            <div class="text-[10px] text-slate-400 font-mono truncate">
              ${c.parent_name ? `${escapeHtml(c.parent_name)} / ` : ''}${escapeHtml(c.target_zip)}
            </div>
          </div>
        </div>
        <div class="flex items-center gap-2 shrink-0 text-right">
          <span class="text-[11px] px-2 py-0.5 rounded-full bg-slate-800 text-sky-300 font-mono text-[10px]">
            ${c.image_count} img${c.image_count === 1 ? '' : 's'}
          </span>
          <span class="text-[11px] text-slate-400 font-mono w-16">
            ${c.total_size_formatted}
          </span>
        </div>
      </div>
    `;
  }).join('');

  // Attach checkbox toggle events
  els.batchZipCandidatesList.querySelectorAll('.batch-zip-item-checkbox').forEach(cb => {
    cb.addEventListener('change', (e) => {
      const p = cb.getAttribute('data-path');
      if (cb.checked) {
        state.batchZipSelectedPaths.add(p);
      } else {
        state.batchZipSelectedPaths.delete(p);
      }
      renderBatchZipCandidates();
    });
  });

  // Clicking row toggles checkbox
  els.batchZipCandidatesList.querySelectorAll('div[data-path]').forEach(row => {
    row.addEventListener('click', (e) => {
      if (e.target.tagName === 'INPUT') return;
      const p = row.getAttribute('data-path');
      const cb = row.querySelector('.batch-zip-item-checkbox');
      if (cb) {
        cb.checked = !cb.checked;
        if (cb.checked) {
          state.batchZipSelectedPaths.add(p);
        } else {
          state.batchZipSelectedPaths.delete(p);
        }
        renderBatchZipCandidates();
      }
    });
  });
}
// Submit Batch ZIP
async function submitBatchZip() {
  const selected = Array.from(state.batchZipSelectedPaths);
  if (selected.length === 0) return;

  try {
    if (els.confirmBatchZipBtn) els.confirmBatchZipBtn.disabled = true;
    const res = await fetch('/api/backup/zip/batch/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_paths: selected }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Failed to start batch compression');
    }

    showToast(data.message || `Started batch compression for ${selected.length} folders`, 'info');
    closeBatchZipModal();
    pollBatchZipStatus();
  } catch (err) {
    console.error('Error starting batch zip:', err);
    showToast(err.message || 'Error starting batch compression', 'error');
  } finally {
    if (els.confirmBatchZipBtn) els.confirmBatchZipBtn.disabled = false;
  }
}
// Toggle Batch ZIP Pause / Resume
async function toggleBatchZipPause() {
  try {
    const res = await fetch('/api/backup/zip/batch/pause', { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      showToast(data.message || (data.status === 'paused' ? 'Batch compression paused' : 'Batch compression resumed'), 'info');
      pollBatchZipStatus();
    }
  } catch (err) {
    console.error(err);
    showToast('Error toggling batch compression pause', 'error');
  }
}
// Cancel Batch ZIP
async function cancelBatchZip() {
  if (!confirm('Are you sure you want to cancel the remaining batch compression? Currently running folder will complete.')) return;
  try {
    const res = await fetch('/api/backup/zip/batch/cancel', { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      showToast(data.message || 'Batch compression canceled', 'warning');
      pollBatchZipStatus();
    }
  } catch (err) {
    console.error(err);
    showToast('Error canceling batch compression', 'error');
  }
}
// ===== Batch Unpack (Extract All ZIPs) =====
async function openBatchUnpackModal() {
  if (!els.batchUnpackModal) return;
  els.batchUnpackModal.classList.remove('hidden');
  if (els.batchUnpackCandidatesList) {
    els.batchUnpackCandidatesList.innerHTML = `
      <div class="col-span-full py-12 flex flex-col items-center justify-center gap-2 text-slate-400">
        <span class="w-6 h-6 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin"></span>
        <span class="text-xs">Scanning library for ZIP archives...</span>
      </div>
    `;
  }
  if (els.confirmBatchUnpackBtn) els.confirmBatchUnpackBtn.disabled = true;

  try {
    const res = await fetch('/api/backup/zip/batch-unpack/candidates');
    if (!res.ok) throw new Error('Failed to fetch candidate archives');
    const data = await res.json();

    state.batchUnpackCandidates = data.candidates || [];
    state.batchUnpackSelectedPaths = new Set(state.batchUnpackCandidates.map(c => c.path));
    state.batchUnpackFilterQuery = '';
    if (els.batchUnpackFilterInput) els.batchUnpackFilterInput.value = '';

    if (els.batchUnpackCandidateCount) els.batchUnpackCandidateCount.textContent = data.total_candidates;
    if (els.batchUnpackFileCount) els.batchUnpackFileCount.textContent = data.total_files;
    if (els.batchUnpackTotalSize) els.batchUnpackTotalSize.textContent = data.total_size_formatted;

    renderBatchUnpackCandidates();
  } catch (err) {
    console.error('Error opening batch unpack modal:', err);
    showToast('Could not load candidate archives', 'error');
    if (els.batchUnpackCandidatesList) {
      els.batchUnpackCandidatesList.innerHTML = `<div class="text-center py-8 text-xs text-rose-400">Error loading candidates: ${escapeHtml(err.message)}</div>`;
    }
  }
}
function closeBatchUnpackModal() {
  if (els.batchUnpackModal) els.batchUnpackModal.classList.add('hidden');
}
function renderBatchUnpackCandidates() {
  if (!els.batchUnpackCandidatesList) return;
  const q = (state.batchUnpackFilterQuery || '').toLowerCase().trim();
  const filtered = state.batchUnpackCandidates.filter(c =>
    c.name.toLowerCase().includes(q) ||
    (c.parent_name && c.parent_name.toLowerCase().includes(q)) ||
    c.path.toLowerCase().includes(q)
  );

  if (filtered.length === 0) {
    els.batchUnpackCandidatesList.innerHTML = `
      <div class="text-center py-8 text-xs text-slate-500">
        ${state.batchUnpackCandidates.length === 0 ? 'No ZIP archives found in library.' : 'No candidates match your search filter.'}
      </div>
    `;
    if (els.batchUnpackSelectedBadge) els.batchUnpackSelectedBadge.textContent = `0 of ${state.batchUnpackCandidates.length} selected`;
    if (els.confirmBatchUnpackBtn) els.confirmBatchUnpackBtn.disabled = true;
    if (els.confirmBatchUnpackBtnText) els.confirmBatchUnpackBtnText.textContent = 'Extract Selected (0)';
    return;
  }

  const selectedCount = state.batchUnpackSelectedPaths.size;
  const totalCount = state.batchUnpackCandidates.length;

  if (els.batchUnpackSelectedBadge) {
    els.batchUnpackSelectedBadge.textContent = `${selectedCount} of ${totalCount} selected`;
  }
  if (els.confirmBatchUnpackBtn) {
    els.confirmBatchUnpackBtn.disabled = selectedCount === 0;
  }
  if (els.confirmBatchUnpackBtnText) {
    els.confirmBatchUnpackBtnText.textContent = `Extract Selected (${selectedCount})`;
  }
  if (els.batchUnpackSelectAllToggle) {
    els.batchUnpackSelectAllToggle.checked = selectedCount === totalCount && totalCount > 0;
    els.batchUnpackSelectAllToggle.indeterminate = selectedCount > 0 && selectedCount < totalCount;
  }

  els.batchUnpackCandidatesList.innerHTML = filtered.map(c => {
    const isChecked = state.batchUnpackSelectedPaths.has(c.path);
    return `
      <div class="flex items-center justify-between gap-3 p-2 rounded-xl hover:bg-slate-800/50 transition cursor-pointer group" data-path="${escapeHtml(c.path)}">
        <div class="flex items-center gap-2.5 min-w-0 flex-1">
          <input
            type="checkbox"
            class="batch-unpack-item-checkbox rounded bg-slate-800 border-slate-700 text-emerald-600 focus:ring-emerald-500 cursor-pointer"
            data-path="${escapeHtml(c.path)}"
            ${isChecked ? 'checked' : ''}
          />
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-1.5 text-xs font-semibold text-white truncate">
              <span>🗜️</span>
              <span class="truncate">${escapeHtml(c.name)}</span>
            </div>
            <div class="text-[10px] text-slate-400 font-mono truncate">
              ${c.parent_name ? `${escapeHtml(c.parent_name)} / ` : ''}${escapeHtml(c.path)}
            </div>
          </div>
        </div>
        <div class="flex items-center gap-2 shrink-0 text-right">
          <span class="text-[11px] px-2 py-0.5 rounded-full bg-slate-800 text-emerald-300 font-mono text-[10px]">
            ${c.total_files} files
          </span>
          <span class="text-[11px] text-slate-400 font-mono w-16">
            ${c.total_size_formatted}
          </span>
        </div>
      </div>
    `;
  }).join('');

  // Attach checkbox toggle events
  els.batchUnpackCandidatesList.querySelectorAll('.batch-unpack-item-checkbox').forEach(cb => {
    cb.addEventListener('change', () => {
      const p = cb.dataset.path;
      if (cb.checked) {
        state.batchUnpackSelectedPaths.add(p);
      } else {
        state.batchUnpackSelectedPaths.delete(p);
      }
      renderBatchUnpackCandidates();
    });
  });

  // Clicking a row toggles its checkbox
  els.batchUnpackCandidatesList.querySelectorAll('div[data-path]').forEach(row => {
    row.addEventListener('click', (e) => {
      if (e.target.closest('input')) return;
      const p = row.dataset.path;
      const cb = row.querySelector('.batch-unpack-item-checkbox');
      if (!cb) return;
      cb.checked = !cb.checked;
      if (cb.checked) {
        state.batchUnpackSelectedPaths.add(p);
      } else {
        state.batchUnpackSelectedPaths.delete(p);
      }
      renderBatchUnpackCandidates();
    });
  });
}
// Submit Batch Unpack
async function submitBatchUnpack() {
  const selected = Array.from(state.batchUnpackSelectedPaths);
  if (selected.length === 0) return;
  try {
    if (els.confirmBatchUnpackBtn) els.confirmBatchUnpackBtn.disabled = true;
    const res = await fetch('/api/backup/zip/batch-unpack/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ zip_paths: selected }),
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || 'Failed to start batch extraction');
    }
    const data = await res.json();
    closeBatchUnpackModal();
    showToast(data.message || `Extracting ${selected.length} archives...`, 'info');
    pollBatchUnpackStatus();
  } catch (err) {
    console.error('Error starting batch unpack:', err);
    showToast(err.message || 'Error starting batch extraction', 'error');
    if (els.confirmBatchUnpackBtn) els.confirmBatchUnpackBtn.disabled = false;
  }
}
// Toggle Batch Unpack Pause / Resume
async function toggleBatchUnpackPause() {
  try {
    const res = await fetch('/api/backup/zip/batch-unpack/pause', { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      showToast(data.message || (data.status === 'paused' ? 'Batch extraction paused' : 'Batch extraction resumed'), 'info');
      pollBatchUnpackStatus();
    }
  } catch (err) {
    console.error(err);
    showToast('Error toggling batch extraction pause', 'error');
  }
}
// Cancel Batch Unpack
async function cancelBatchUnpack() {
  if (!confirm('Are you sure you want to cancel the remaining batch extraction? The currently running archive will complete.')) return;
  try {
    const res = await fetch('/api/backup/zip/batch-unpack/cancel', { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      showToast(data.message || 'Batch extraction canceled', 'warning');
      pollBatchUnpackStatus();
    }
  } catch (err) {
    console.error(err);
    showToast('Error canceling batch extraction', 'error');
  }
}
async function openEmptyFoldersModal() {
  if (els.transferDropdown) {
    els.transferDropdown.classList.add('hidden');
  }
  if (els.transferBackdrop) {
    els.transferBackdrop.classList.add('hidden');
  }
  if (!els.emptyFoldersModal) return;
  els.emptyFoldersModal.classList.remove('hidden');
  if (els.emptyFoldersCandidatesList) {
    els.emptyFoldersCandidatesList.innerHTML = `
      <div class="col-span-full py-12 flex flex-col items-center justify-center gap-2 text-slate-400">
        <span class="w-6 h-6 border-2 border-rose-400 border-t-transparent rounded-full animate-spin"></span>
        <span class="text-xs">Scanning library for empty folders...</span>
      </div>
    `;
  }
  if (els.confirmEmptyFoldersBtn) els.confirmEmptyFoldersBtn.disabled = true;

  try {
    const res = await fetch('/api/backup/folders/empty/candidates');
    if (!res.ok) throw new Error('Failed to fetch candidate empty folders');
    const data = await res.json();

    state.emptyFoldersCandidates = data.candidates || [];
    state.emptyFoldersSelectedPaths = new Set(state.emptyFoldersCandidates.map(c => c.path));
    state.emptyFoldersFilterQuery = '';
    if (els.emptyFoldersFilterInput) els.emptyFoldersFilterInput.value = '';

    if (els.emptyFoldersCandidateCount) els.emptyFoldersCandidateCount.textContent = data.total_candidates;
    const leafCount = state.emptyFoldersCandidates.filter(c => c.is_leaf).length;
    if (els.emptyFoldersLeafCount) els.emptyFoldersLeafCount.textContent = leafCount;

    renderEmptyFoldersCandidates();
  } catch (err) {
    console.error('Error opening empty folders modal:', err);
    showToast('Could not load empty folders', 'error');
    if (els.emptyFoldersCandidatesList) {
      els.emptyFoldersCandidatesList.innerHTML = `<div class="text-center py-8 text-xs text-rose-400">Error loading empty folders: ${escapeHtml(err.message)}</div>`;
    }
  }
}
function closeEmptyFoldersModal() {
  if (els.emptyFoldersModal) els.emptyFoldersModal.classList.add('hidden');
}
function renderEmptyFoldersCandidates() {
  if (!els.emptyFoldersCandidatesList) return;
  const q = (state.emptyFoldersFilterQuery || '').toLowerCase().trim();
  const filtered = state.emptyFoldersCandidates.filter(c =>
    c.name.toLowerCase().includes(q) ||
    (c.parent_name && c.parent_name.toLowerCase().includes(q)) ||
    (c.relative_path && c.relative_path.toLowerCase().includes(q)) ||
    c.path.toLowerCase().includes(q)
  );

  if (filtered.length === 0) {
    els.emptyFoldersCandidatesList.innerHTML = `
      <div class="text-center py-8 text-xs text-slate-500">
        ${state.emptyFoldersCandidates.length === 0 ? 'No empty folders found in library.' : 'No candidates match your search filter.'}
      </div>
    `;
    if (els.emptyFoldersSelectedBadge) els.emptyFoldersSelectedBadge.textContent = `0 of ${state.emptyFoldersCandidates.length} selected`;
    if (els.confirmEmptyFoldersBtn) els.confirmEmptyFoldersBtn.disabled = true;
    if (els.confirmEmptyFoldersBtnText) els.confirmEmptyFoldersBtnText.textContent = 'Delete Selected (0)';
    return;
  }

  const selectedCount = state.emptyFoldersSelectedPaths.size;
  const totalCount = state.emptyFoldersCandidates.length;

  if (els.emptyFoldersSelectedBadge) {
    els.emptyFoldersSelectedBadge.textContent = `${selectedCount} of ${totalCount} selected`;
  }
  if (els.confirmEmptyFoldersBtn) {
    els.confirmEmptyFoldersBtn.disabled = selectedCount === 0;
  }
  if (els.confirmEmptyFoldersBtnText) {
    els.confirmEmptyFoldersBtnText.textContent = `Delete Selected (${selectedCount})`;
  }
  if (els.emptyFoldersSelectAllToggle) {
    els.emptyFoldersSelectAllToggle.checked = selectedCount === totalCount && totalCount > 0;
    els.emptyFoldersSelectAllToggle.indeterminate = selectedCount > 0 && selectedCount < totalCount;
  }

  els.emptyFoldersCandidatesList.innerHTML = filtered.map(c => {
    const isChecked = state.emptyFoldersSelectedPaths.has(c.path);
    return `
      <div class="flex items-center justify-between gap-3 p-2 rounded-xl hover:bg-slate-800/50 transition cursor-pointer group" data-path="${escapeHtml(c.path)}">
        <div class="flex items-center gap-2.5 min-w-0 flex-1">
          <input 
            type="checkbox" 
            class="empty-folder-item-checkbox rounded bg-slate-800 border-slate-700 text-rose-600 focus:ring-rose-500 cursor-pointer"
            data-path="${escapeHtml(c.path)}"
            ${isChecked ? 'checked' : ''}
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
  }).join('');

  // Attach click events on checkboxes
  els.emptyFoldersCandidatesList.querySelectorAll('.empty-folder-item-checkbox').forEach(cb => {
    cb.addEventListener('change', (e) => {
      const path = e.target.dataset.path;
      if (e.target.checked) {
        state.emptyFoldersSelectedPaths.add(path);
      } else {
        state.emptyFoldersSelectedPaths.delete(path);
      }
      renderEmptyFoldersCandidates();
    });
  });

  // Row click toggle
  els.emptyFoldersCandidatesList.querySelectorAll('[data-path]').forEach(row => {
    row.addEventListener('click', (e) => {
      if (e.target.classList.contains('empty-folder-item-checkbox')) return;
      const cb = row.querySelector('.empty-folder-item-checkbox');
      if (cb) {
        cb.checked = !cb.checked;
        cb.dispatchEvent(new Event('change'));
      }
    });
  });
}
async function submitDeleteEmptyFolders() {
  const selected = Array.from(state.emptyFoldersSelectedPaths);
  if (selected.length === 0) return;

  try {
    if (els.confirmEmptyFoldersBtn) {
      els.confirmEmptyFoldersBtn.disabled = true;
    }
    if (els.confirmEmptyFoldersBtnText) {
      els.confirmEmptyFoldersBtnText.textContent = 'Deleting...';
    }

    const res = await fetch('/api/backup/folders/empty/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_paths: selected }),
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || 'Failed to delete empty folders');
    }

    const data = await res.json();
    closeEmptyFoldersModal();

    if (data.deleted_count > 0) {
      showToast(`Successfully deleted ${data.deleted_count} empty folder${data.deleted_count === 1 ? '' : 's'}!`, 'success');
    } else {
      showToast('No empty folders were deleted', 'info');
    }

    // Refresh current folder view & folder tree
    loadFolder(state.currentPath, false, true);
    renderSidebarTree();
  } catch (err) {
    console.error('Error deleting empty folders:', err);
    showToast(err.message || 'Error deleting empty folders', 'error');
  } finally {
    if (els.confirmEmptyFoldersBtn) {
      els.confirmEmptyFoldersBtn.disabled = false;
    }
    if (els.confirmEmptyFoldersBtnText) {
      els.confirmEmptyFoldersBtnText.textContent = `Delete Selected (${state.emptyFoldersSelectedPaths.size})`;
    }
  }
}

export { cancelBatchUnpack, cancelBatchZip, closeBatchUnpackModal, closeBatchZipModal, closeCompressZipModal, closeEmptyFoldersModal, closeUnpackZipModal, handleZipStatusUpdate, openBatchUnpackModal, openBatchZipModal, openCompressZipModal, openEmptyFoldersModal, openUnpackZipModal, renderBatchUnpackCandidates, renderBatchZipCandidates, renderEmptyFoldersCandidates, submitBatchUnpack, submitBatchZip, submitCompressZip, submitDeleteEmptyFolders, submitUnpackZip, toggleBatchUnpackPause, toggleBatchZipPause };
