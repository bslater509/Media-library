import { icon } from '../shared/icons.js';
import { els, state } from './state.js';
import { escapeHtml, formatBytes, safeAttrEscape, showToast } from './utils.js';
import { loadFolder } from './folders.js';
import { pollSystemStatus, updateHeaderTaskBadge } from './tasks.js';

// Chunked browser uploads and mobile URL download form.
// Extracted from the former monolithic library index.js.

// ==========================================
// BROWSER UPLOAD FEATURE
// ==========================================
const uploadState = {
  queue: [], // { file, relativePath, id, status, size, uploadedBytes, mtime }
  activeUploads: 0,
  maxConcurrent: 3,
  chunkSize: 5 * 1024 * 1024, // 5MB
  totalBytes: 0,
  uploadedBytes: 0,
  isCancelled: false,
  startTime: null,
  targetFolder: null
};
// Non-cryptographic fallback for the upload id. `crypto.subtle` only exists in a
// secure context, and a phone browsing the library over plain http://192.168.x.x
// is not one, so this must never be the only path.
function hashToHex(input) {
  let h1 = 0x811c9dc5;
  let h2 = 0x1000193;
  for (let i = 0; i < input.length; i++) {
    const c = input.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 0x01000193) >>> 0;
    h2 = Math.imul(h2 + c, 0x85ebca6b) >>> 0;
  }
  return (
    h1.toString(16).padStart(8, '0') + h2.toString(16).padStart(8, '0')
  );
}

// A stable upload id per (path, size, mtime): the server stages chunks under it,
// so re-opening the page and re-picking the same file resumes the transfer
// instead of starting the multi-gigabyte upload over.
async function makeUploadId(fileObj) {
  const key = `${fileObj.relativePath || (fileObj.file && fileObj.file.name) || ''}|${fileObj.size || 0}|${fileObj.mtime || 0}`;
  try {
    if (window.crypto && window.crypto.subtle && typeof TextEncoder !== 'undefined') {
      const digest = await window.crypto.subtle.digest('SHA-256', new TextEncoder().encode(key));
      const hex = Array.from(new Uint8Array(digest))
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('');
      return 'u_' + hex.slice(0, 32);
    }
  } catch (err) {
    // Insecure context / digest refused: fall through to the plain hash.
  }
  return 'u_' + hashToHex(key);
}

function initUploadEvents() {
  const dropzone = document.getElementById('globalDropzone');
  const uploadDropdownBtn = document.getElementById('uploadDropdownBtn');
  const uploadDropdownMenu = document.getElementById('uploadDropdownMenu');
  const uploadFilesBtn = document.getElementById('uploadFilesBtn');
  const uploadFolderBtn = document.getElementById('uploadFolderBtn');
  const hiddenFileInput = document.getElementById('hiddenUploadFileInput');
  const hiddenFolderInput = document.getElementById('hiddenUploadFolderInput');
  const startUploadBtn = document.getElementById('startUploadBtn');
  const cancelUploadReviewBtn = document.getElementById('cancelUploadReviewBtn');
  const cancelUploadReviewBtnTop = document.getElementById('cancelUploadReviewBtnTop');
  const cancelAllUploadsBtn = document.getElementById('cancelAllUploadsBtn');

  // Toggle dropdown
  if (uploadDropdownBtn) {
    uploadDropdownBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      uploadDropdownMenu.classList.toggle('hidden');
    });
  }
  // Outside click/touch listener to close dropdown
  const closeDropdownHandler = (e) => {
    if (uploadDropdownMenu && !uploadDropdownMenu.contains(e.target) && !uploadDropdownBtn.contains(e.target)) {
      uploadDropdownMenu.classList.add('hidden');
    }
  };
  document.addEventListener('click', closeDropdownHandler);
  document.addEventListener('touchstart', closeDropdownHandler, { passive: true });

  if (hiddenFileInput) {
    hiddenFileInput.addEventListener('change', (e) => {
      if (uploadDropdownMenu) uploadDropdownMenu.classList.add('hidden');
      handleFileInput(e);
    });
  }
  if (hiddenFolderInput) {
    hiddenFolderInput.addEventListener('change', (e) => {
      if (uploadDropdownMenu) uploadDropdownMenu.classList.add('hidden');
      handleFileInput(e);
    });
  }

  // Drag and Drop
  let dragCounter = 0;
  document.body.addEventListener('dragenter', (e) => {
    e.preventDefault();
    if (!state.currentPath || state.currentPath === state.basePath) return; // Disallow root upload for safety if preferred, or allow. We allow if state.currentPath exists.
    dragCounter++;
    if (dropzone) dropzone.classList.remove('hidden');
    const textEl = document.getElementById('dropzoneTargetText');
    if (textEl) textEl.textContent = 'Target: ' + state.currentPath;
  });
  
  document.body.addEventListener('dragleave', (e) => {
    e.preventDefault();
    dragCounter--;
    if (dragCounter <= 0) {
      dragCounter = 0;
      if (dropzone) dropzone.classList.add('hidden');
    }
  });
  
  document.body.addEventListener('dragover', (e) => {
    e.preventDefault();
  });
  
  document.body.addEventListener('drop', async (e) => {
    e.preventDefault();
    dragCounter = 0;
    if (dropzone) dropzone.classList.add('hidden');
    if (!state.currentPath || state.currentPath === state.basePath) return;
    
    const items = e.dataTransfer.items;
    if (!items) return;
    
    const filesToUpload = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i].webkitGetAsEntry();
      if (item) {
        await traverseFileTree(item, item.name, filesToUpload);
      }
    }
    if (filesToUpload.length > 0) {
      showUploadReviewModal(filesToUpload);
    }
  });

  if (startUploadBtn) startUploadBtn.addEventListener('click', startUploadBatch);
  if (cancelUploadReviewBtn) cancelUploadReviewBtn.addEventListener('click', closeUploadReviewModal);
  if (cancelUploadReviewBtnTop) cancelUploadReviewBtnTop.addEventListener('click', closeUploadReviewModal);
  if (cancelAllUploadsBtn) cancelAllUploadsBtn.addEventListener('click', cancelAllUploads);

  // Download from URL (desktop Upload dropdown) now opens the dedicated
  // /downloads page, prefilled with the folder currently being browsed.
  const urlDownloadBtn = document.getElementById('urlDownloadBtn');
  if (urlDownloadBtn) {
    urlDownloadBtn.addEventListener('click', () => {
      uploadDropdownMenu.classList.add('hidden');
      const folder = state.currentPath || state.basePath || '';
      window.location.href = folder
        ? `/downloads?folder=${encodeURIComponent(folder)}`
        : '/downloads';
    });
  }
}
function handleFileInput(e) {
  const rawFiles = e.target.files;
  if (!rawFiles || rawFiles.length === 0) {
    console.warn('handleFileInput: no files selected');
    return;
  }
  const files = Array.from(rawFiles);
  
  const filesToUpload = files.map(f => ({
    file: f,
    relativePath: f.webkitRelativePath || f.name,
    size: f.size || 0
  }));
  
  showUploadReviewModal(filesToUpload);
}
async function traverseFileTree(item, path, fileList) {
  if (item.isFile) {
    const file = await new Promise((resolve) => item.file(resolve));
    fileList.push({ file, relativePath: path, size: file.size });
  } else if (item.isDirectory) {
    const dirReader = item.createReader();
    const entries = await new Promise((resolve) => {
      const results = [];
      const readEntries = () => {
        dirReader.readEntries((entriesBatch) => {
          if (entriesBatch.length === 0) resolve(results);
          else {
            results.push(...entriesBatch);
            readEntries();
          }
        });
      };
      readEntries();
    });
    for (let entry of entries) {
      await traverseFileTree(entry, path + "/" + entry.name, fileList);
    }
  }
}
async function showUploadReviewModal(files) {
  try {
    const target = state.currentPath || state.basePath || '/';
    const items = files.map((item, idx) => ({
      ...item,
      id: 'upl_' + Date.now() + '_' + idx + '_' + Math.random().toString(36).slice(2, 7),
      status: 'pending',
      uploadedBytes: 0,
      mtime: (item.file && item.file.lastModified) ? item.file.lastModified : Date.now()
    }));

    // Stable, resumable upload ids (see makeUploadId).
    const stableIds = await Promise.all(items.map((item) => makeUploadId(item)));
    items.forEach((item, idx) => { item.id = stableIds[idx]; });

    uploadState.queue = items;
    
    uploadState.totalBytes = uploadState.queue.reduce((sum, f) => sum + (f.size || 0), 0);
    uploadState.targetFolder = target;
    
    const targetLabel = document.getElementById('uploadTargetFolderLabel');
    if (targetLabel) targetLabel.textContent = target;
    const totalFilesLabel = document.getElementById('uploadTotalFilesLabel');
    if (totalFilesLabel) totalFilesLabel.textContent = files.length.toLocaleString();
    const totalSizeLabel = document.getElementById('uploadTotalSizeLabel');
    if (totalSizeLabel) totalSizeLabel.textContent = formatBytes(uploadState.totalBytes);
    
    const warningEl = document.getElementById('uploadWarningText');
    const startBtn = document.getElementById('startUploadBtn');
    if (state.isZip) {
      if (warningEl) {
        warningEl.textContent = 'Cannot upload files directly into a virtual ZIP archive. Navigate to a folder first.';
        warningEl.classList.remove('hidden');
      }
      if (startBtn) startBtn.disabled = true;
    } else {
      if (warningEl) warningEl.classList.add('hidden');
      if (startBtn) startBtn.disabled = false;
    }

    const modal = document.getElementById('uploadReviewModal');
    if (modal) {
      modal.classList.remove('hidden');
      modal.classList.add('flex');
    }
  } catch (err) {
    console.error('Error in showUploadReviewModal:', err);
    showToast('Error displaying upload review: ' + (err.message || 'Unknown'), 'error');
  }
}
function closeUploadReviewModal() {
  const modal = document.getElementById('uploadReviewModal');
  if (modal) {
    modal.classList.add('hidden');
    modal.classList.remove('flex');
  }
  uploadState.queue = [];
  const fi = document.getElementById('hiddenUploadFileInput');
  if (fi) fi.value = '';
  const fo = document.getElementById('hiddenUploadFolderInput');
  if (fo) fo.value = '';
}
// Mobile Downloads tab: mirror the target-folder warning state and
// disable Downloads when the current folder is a virtual ZIP archive.
function syncMobileDownloadsUI() {
  const label = els.mobileDlTargetLabel;
  if (label) label.textContent = state.currentPath || state.basePath || '/';
  const warningEl = els.mobileDlWarningText;
  const startBtn = els.mobileDlStartBtn;
  if (state.isZip) {
    if (warningEl) {
      warningEl.textContent = 'Cannot download into a virtual ZIP archive. Navigate to a folder first.';
      warningEl.classList.remove('hidden');
    }
    if (startBtn) startBtn.disabled = true;
  } else {
    if (warningEl) warningEl.classList.add('hidden');
    if (startBtn) startBtn.disabled = false;
  }
}
// Same endpoint as the desktop dialog; the sheet stays open so the queued job
// shows up in the list below with live progress.
async function submitMobileUrlDownload() {
  const input = els.mobileDlUrlInput;
  const url = input ? input.value.trim() : '';
  if (!url) {
    showToast('Enter a video or playlist URL', 'error');
    return;
  }
  if (state.isZip) {
    showToast('Navigate to a real folder first', 'error');
    return;
  }
  const target = state.currentPath || state.basePath || '/';
  const startBtn = els.mobileDlStartBtn;
  if (startBtn) startBtn.disabled = true;
  try {
    const res = await fetch('/api/backup/upload/url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, destination_dir: target })
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast('Could not queue download: ' + (j.detail || res.status), 'error');
      return;
    }
    if (input) input.value = '';
    showToast('Download queued in background', 'success');
    pollSystemStatus(true);
  } catch (err) {
    showToast('Failed to queue download: ' + (err.message || 'Error'), 'error');
  } finally {
    if (startBtn) startBtn.disabled = false;
  }
}
async function startUploadBatch() {
  const collisionPolicy = document.getElementById('uploadCollisionPolicy').value;
  uploadState.collisionPolicy = collisionPolicy;
  
  const modal = document.getElementById('uploadReviewModal');
  if (modal) {
    modal.classList.add('hidden');
    modal.classList.remove('flex');
  }
  
  const count = uploadState.queue.length;
  showToast(`Starting upload for ${count} file${count === 1 ? '' : 's'}...`, 'info');

  const section = document.getElementById('browserUploadsSection');
  if (section) section.classList.remove('hidden');
  
  uploadState.isCancelled = false;
  uploadState.activeUploads = 0;
  uploadState.uploadedBytes = 0;
  uploadState.startTime = Date.now();
  
  updateUploadsUI();
  
  // Reflect the active upload in the header tasks badge.
  updateHeaderTaskBadge();
  
  processUploadQueue();
}
async function processUploadQueue() {
  if (uploadState.isCancelled) return;
  
  const pending = uploadState.queue.filter(f => f.status === 'pending');
  if (pending.length === 0 && uploadState.activeUploads === 0) {
    finishUploadBatch();
    return;
  }
  
  while (uploadState.activeUploads < uploadState.maxConcurrent && pending.length > 0) {
    const fileObj = pending.shift();
    uploadState.activeUploads++;
    fileObj.status = 'uploading';
    updateUploadsUI();
    uploadFile(fileObj).finally(() => {
      uploadState.activeUploads--;
      processUploadQueue();
    });
  }
}
async function uploadFile(fileObj) {
  const { file, id, relativePath, size, mtime } = fileObj;
  const totalChunks = Math.ceil(size / uploadState.chunkSize) || 1;

  // Resume probe: the server stages this upload's chunks under a stable id, so
  // ask how many bytes it already holds. An interrupted transfer (dropped
  // connection, closed tab, phone asleep) continues from there instead of
  // re-sending gigabytes; the server acks an already-held chunk as a duplicate.
  let startChunk = 0;
  try {
    const stRes = await fetch(
      `/api/backup/upload/status?upload_id=${encodeURIComponent(id)}`,
      { cache: 'no-store' }
    );
    if (stRes.ok) {
      const st = await stRes.json().catch(() => ({}));
      const received = Number(st.received_bytes || 0);
      if (received >= size && size > 0) {
        startChunk = totalChunks; // every chunk is already staged; just finalize
      } else if (received > 0 && received < size) {
        startChunk = Math.floor(received / uploadState.chunkSize);
        fileObj.uploadedBytes = startChunk * uploadState.chunkSize;
        showToast(
          `Resuming ${file.name} at ${Math.floor((fileObj.uploadedBytes / size) * 100)}%`,
          'info',
          2500
        );
        updateUploadsUI();
      }
    }
  } catch (err) {
    // Status unavailable: start from 0. Duplicate chunks are acked, and the
    // offset check makes an out-of-order write impossible either way.
  }

  try {
    for (let chunkIndex = startChunk; chunkIndex < totalChunks; chunkIndex++) {
      if (uploadState.isCancelled || fileObj.status === 'cancelled') {
        await cancelBackendUpload(id);
        return;
      }
      
      const start = chunkIndex * uploadState.chunkSize;
      const end = Math.min(start + uploadState.chunkSize, size);
      const chunk = file.slice(start, end);
      
      let attempts = 0;
      let success = false;
      while (attempts < 3 && !success) {
        if (uploadState.isCancelled || fileObj.status === 'cancelled') return;
        try {
          const formData = new FormData();
          formData.append('upload_id', id);
          formData.append('chunk_index', chunkIndex);
          formData.append('total_chunks', totalChunks);
          formData.append('offset', String(start));
          formData.append('chunk', chunk, file.name);
          
          const res = await fetch('/api/backup/upload/chunk', { method: 'POST', body: formData });
          if (!res.ok) {
              const text = await res.text();
              let detail = text;
              try {
                const j = JSON.parse(text);
                if (j.detail) detail = j.detail;
              } catch (_) {}
              throw new Error(detail);
          }
          success = true;
        } catch (err) {
          attempts++;
          if (attempts >= 3) throw err;
          await new Promise(r => setTimeout(r, 1000 * attempts)); // backoff
        }
      }
      
      fileObj.uploadedBytes = end;
      updateUploadBatchStats();
      updateUploadsUI();
    }
    
    if (uploadState.isCancelled || fileObj.status === 'cancelled') {
      await cancelBackendUpload(id);
      return;
    }
    
    // Finalize
    const finalizeRes = await fetch('/api/backup/upload/finalize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        upload_id: id,
        original_filename: file.name,
        relative_path: relativePath,
        destination_dir: uploadState.targetFolder,
        collision_policy: uploadState.collisionPolicy,
        total_size: size,
        mtime: mtime
      })
    });
    
    if (!finalizeRes.ok) {
        const text = await finalizeRes.text();
        let detail = text;
        try {
          const j = JSON.parse(text);
          if (j.detail) detail = j.detail;
        } catch (_) {}
        throw new Error(detail);
    }
    
    fileObj.status = 'done';
  } catch (err) {
    console.error('Upload failed for', file.name, err);
    fileObj.status = 'error';
    showToast(`Failed to upload ${file.name}: ${err.message || 'Error'}`, 'error');
  }
  updateUploadsUI();
}
async function cancelBackendUpload(id) {
  try {
    await fetch('/api/backup/upload/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ upload_id: id })
    });
  } catch (err) { }
}
function cancelAllUploads() {
  uploadState.isCancelled = true;
  uploadState.queue.forEach(f => {
    if (f.status === 'uploading' || f.status === 'pending') {
      f.status = 'cancelled';
      if (f.status === 'uploading') cancelBackendUpload(f.id);
    }
  });
  updateUploadsUI();
  finishUploadBatch();
}
window.cancelSingleUpload = function(id) {
  const f = uploadState.queue.find(item => item.id === id);
  if (f) {
    if (f.status === 'pending') {
      f.status = 'cancelled';
      updateUploadsUI();
    } else if (f.status === 'uploading') {
      f.status = 'cancelled';
    }
  }
};
function updateUploadBatchStats() {
  let totalUploaded = 0;
  uploadState.queue.forEach(f => {
    if (f.status === 'done') totalUploaded += f.size;
    else if (f.status === 'uploading') totalUploaded += f.uploadedBytes;
  });
  uploadState.uploadedBytes = totalUploaded;
}
function updateUploadsUI() {
  const progressEl = document.getElementById('uploadBatchProgress');
  const barEl = document.getElementById('uploadBatchProgressBar');
  const speedEl = document.getElementById('uploadBatchSpeed');
  const statusEl = document.getElementById('uploadBatchStatus');
  const listEl = document.getElementById('activeUploadsList');
  
  if (!listEl) return;

  let totalUploaded = uploadState.uploadedBytes;
  const total = uploadState.totalBytes || 1;
  const percent = Math.floor((totalUploaded / total) * 100);
  
  if (progressEl) progressEl.textContent = percent + '%';
  if (barEl) barEl.style.width = percent + '%';
  
  const elapsedSecs = (Date.now() - uploadState.startTime) / 1000;
  const speed = elapsedSecs > 1 ? (totalUploaded / elapsedSecs) : 0;
  if (speedEl) speedEl.textContent = formatBytes(speed) + '/s';
  
  const doneCount = uploadState.queue.filter(f => f.status === 'done').length;
  if (statusEl) statusEl.textContent = `${doneCount} of ${uploadState.queue.length} files`;
  
  // Update header badge dynamically
  updateHeaderTaskBadge();

  // Render list
  listEl.innerHTML = uploadState.queue.slice().sort((a,b) => {
    const order = { 'uploading': 1, 'pending': 2, 'error': 3, 'done': 4, 'cancelled': 5 };
    return order[a.status] - order[b.status];
  }).map(f => {
    let statusIcon = icon('loader-circle', 'w-3.5 h-3.5');
    let rowClass = 'bg-slate-900 border-slate-700/50';
    if (f.status === 'uploading') { statusIcon = icon('upload', 'w-3.5 h-3.5'); rowClass = 'bg-emerald-950/20 border-emerald-900/30'; }
    if (f.status === 'done') { statusIcon = icon('circle-check', 'w-3.5 h-3.5'); rowClass = 'bg-emerald-950/40 border-emerald-800/40 opacity-70'; }
    if (f.status === 'error') { statusIcon = icon('circle-x', 'w-3.5 h-3.5'); rowClass = 'bg-red-950/30 border-red-900/40'; }
    if (f.status === 'cancelled') { statusIcon = icon('ban', 'w-3.5 h-3.5'); rowClass = 'bg-slate-900/50 border-slate-800/50 opacity-50'; }
    
    const filePercent = f.size > 0 ? Math.floor((f.uploadedBytes / f.size) * 100) : 100;
    
    return `
      <div class="flex items-center gap-2 p-2 rounded-lg border text-xs ${rowClass}">
        <span class="shrink-0 w-4 flex items-center justify-center">${statusIcon}</span>
        <div class="flex-1 min-w-0 flex flex-col">
          <span class="text-slate-300 truncate font-medium">${escapeHtml(f.relativePath)}</span>
          ${f.status === 'uploading' ? `<div class="h-1 w-full bg-slate-800 rounded-full mt-1 overflow-hidden"><div class="h-full bg-emerald-500" style="width: ${filePercent}%"></div></div>` : ''}
        </div>
        <span class="shrink-0 text-slate-500 text-[10px] w-12 text-right">${formatBytes(f.size)}</span>
        ${(f.status === 'pending' || f.status === 'uploading') ? `
          <button onclick="window.cancelSingleUpload('${safeAttrEscape(f.id)}')" class="shrink-0 text-red-400 hover:text-red-300 p-0.5 rounded hover:bg-slate-800 transition">${icon('x', 'w-3.5 h-3.5')}</button>
        ` : ''}
      </div>
    `;
  }).join('');
}
function finishUploadBatch() {
  setTimeout(() => {
    const section = document.getElementById('browserUploadsSection');
    if (section) section.classList.add('hidden');
    
    updateHeaderTaskBadge();
    
    const successCount = uploadState.queue.filter(f => f.status === 'done').length;
    if (successCount > 0) {
      showToast(`Successfully uploaded ${successCount} files`, 'success');
      if (state.currentPath === uploadState.targetFolder) {
        loadFolder(state.currentPath, false, false, true); // silent refresh
      }
    }
    
    uploadState.queue = [];
  }, 2000);
}

export { cancelAllUploads, cancelBackendUpload, closeUploadReviewModal, finishUploadBatch, handleFileInput, hashToHex, initUploadEvents, makeUploadId, processUploadQueue, showUploadReviewModal, startUploadBatch, submitMobileUrlDownload, syncMobileDownloadsUI, traverseFileTree, updateUploadBatchStats, updateUploadsUI, uploadFile, uploadState };
