import { els, state } from './state.js';
import { escapeHtml, showToast } from './utils.js';
import { generateAnimatedPreviewForTarget } from './animations.js';
import { loadFolder } from './folders.js';
import { applyFiltersAndSort } from './gallery.js';
import { downloadMediaFile, openFancybox, openMediaInfoModal } from './lightbox.js';

// File/folder management: rename, delete, new folder, move, selection and bulk operations.
// Extracted from the former monolithic library index.js.

// Rename Flow
function openRenameModal(path, name, kind = 'file') {
  state.itemToRename = {
    path: decodeURIComponent(path),
    name: decodeURIComponent(name),
    kind,
  };
  els.newFileNameInput.value = state.itemToRename.name;
  els.renameModal.classList.remove('hidden');
  setTimeout(() => {
    els.newFileNameInput.focus();
    const lastDot = state.itemToRename.name.lastIndexOf('.');
    if (lastDot > 0 && kind === 'file') {
      els.newFileNameInput.setSelectionRange(0, lastDot);
    } else {
      els.newFileNameInput.select();
    }
  }, 50);
}
function closeRenameModal() {
  els.renameModal.classList.add('hidden');
  state.itemToRename = null;
}
async function submitRename() {
  if (!state.itemToRename) return;
  const newName = els.newFileNameInput.value.trim();
  if (!newName) {
    showToast('Filename cannot be empty', 'error');
    return;
  }
  if (newName === state.itemToRename.name) {
    closeRenameModal();
    return;
  }

  const isFile = state.itemToRename.kind === 'file';
  const endpoint = isFile ? '/api/backup/rename' : '/api/backup/folders/rename';
  const itemPath = state.itemToRename.path;
  const itemKind = state.itemToRename.kind;

  try {
    els.confirmRenameBtn.disabled = true;
    els.confirmRenameBtn.textContent = 'Saving...';

    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: itemPath,
        new_name: newName,
      }),
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Failed to rename item');
    }

    showToast(`Renamed to "${newName}"`, 'success');
    closeRenameModal();

    if (isFile) {
      // Update in local arrays
      const file = state.allFiles.find(f => f.path === itemPath);
      if (file) {
        file.name = newName;
        file.path = data.new_path;
        file.thumb_url = data.thumb_url;
        file.media_url = data.media_url;
      }
      if (state.fancyboxInstance) {
        Fancybox.close();
      }
      applyFiltersAndSort();
    } else {
      // Folder / ZIP rename: re-browse to reflect the changed subtree.
      loadFolder(state.currentPath, false);
    }
  } catch (err) {
    console.error(err);
    showToast(err.message || 'Error renaming item', 'error');
  } finally {
    els.confirmRenameBtn.disabled = false;
    els.confirmRenameBtn.textContent = 'Save Name';
  }
}
// Delete Flow
function resetDeleteConfirmation() {
  if (els.deleteConfirmAckInput) els.deleteConfirmAckInput.checked = false;
  if (els.deleteConfirmAck) {
    els.deleteConfirmAck.classList.remove('hidden');
    els.deleteConfirmAck.classList.add('flex');
  }
  els.confirmDeleteBtn.disabled = true;
}
function openDeleteModal(path, name, sizeFormatted, kind = 'file') {
  state.itemToDelete = {
    path: decodeURIComponent(path),
    name: decodeURIComponent(name),
    size: sizeFormatted,
    kind,
  };
  els.deleteFileName.textContent = state.itemToDelete.name;
  els.deleteFileSize.textContent = state.itemToDelete.size || '';

  if (kind === 'folder') {
    els.deleteModalTitle.textContent = 'Permanently Delete Folder?';
    els.deleteModalSubtitle.textContent = 'This folder and everything inside it will be permanently deleted.';
    els.confirmDeleteBtn.textContent = 'Delete Folder';
  } else if (kind === 'zip') {
    els.deleteModalTitle.textContent = 'Delete ZIP Archive?';
    els.deleteModalSubtitle.textContent = 'This archive and its cached contents will be deleted.';
    els.confirmDeleteBtn.textContent = 'Delete Archive';
  } else {
    els.deleteModalTitle.textContent = 'Permanently Delete File?';
    els.deleteModalSubtitle.textContent = 'This action cannot be undone.';
    els.confirmDeleteBtn.textContent = 'Delete File';
  }

  resetDeleteConfirmation();
  els.deleteModal.classList.remove('hidden');
}
function closeDeleteModal() {
  els.deleteModal.classList.add('hidden');
  state.itemToDelete = null;
}
async function submitDelete() {
  if (!state.itemToDelete) return;

  if (els.deleteConfirmAckInput && !els.deleteConfirmAckInput.checked) {
    showToast('Tick the confirmation box to proceed', 'error');
    return;
  }

  if (state.itemToDelete.bulk) {
    try {
      els.confirmDeleteBtn.disabled = true;
      els.confirmDeleteBtn.textContent = 'Deleting...';
      const res = await fetch('/api/backup/bulk/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths: state.itemToDelete.paths }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to delete');
      showToast(`Queued ${data.queued} deletion(s)`, 'success');
      closeDeleteModal();
      clearSelection();
      awaitLibraryOpsAndRefresh((data.tasks || []).map(t => t.task_id), state.currentPath);
    } catch (err) {
      showToast(err.message || 'Error deleting', 'error');
    } finally {
      els.confirmDeleteBtn.disabled = !(els.deleteConfirmAckInput && els.deleteConfirmAckInput.checked);
      els.confirmDeleteBtn.textContent = 'Delete';
    }
    return;
  }

  const kind = state.itemToDelete.kind;
  const endpoint = kind === 'file' ? '/api/backup/delete' : '/api/backup/folders/delete';
  const deletedPath = state.itemToDelete.path;

  try {
    els.confirmDeleteBtn.disabled = true;
    els.confirmDeleteBtn.textContent = 'Deleting...';

    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: deletedPath }),
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Failed to delete');
    }

    if (kind === 'file') {
      showToast(`Deleted "${state.itemToDelete.name}"`, 'success');
      closeDeleteModal();
      state.allFiles = state.allFiles.filter(f => f.path !== deletedPath);
      if (state.fancyboxInstance) {
        Fancybox.close();
      }
      applyFiltersAndSort();
    } else {
      showToast(`Queued deletion of "${state.itemToDelete.name}"`, 'success');
      const delTaskId = data.task_id;
      closeDeleteModal();
      awaitLibraryOpsAndRefresh([delTaskId], state.currentPath);
    }
  } catch (err) {
    console.error(err);
    showToast(err.message || 'Error deleting', 'error');
  } finally {
    els.confirmDeleteBtn.disabled = !(els.deleteConfirmAckInput && els.deleteConfirmAckInput.checked);
    els.confirmDeleteBtn.textContent = 'Delete';
  }
}
// --- New Folder ---
function openNewFolderModal() {
  els.newFolderNameInput.value = '';
  els.newFolderModal.classList.remove('hidden');
  setTimeout(() => els.newFolderNameInput.focus(), 50);
}
function closeNewFolderModal() {
  els.newFolderModal.classList.add('hidden');
}
async function submitNewFolder() {
  const name = els.newFolderNameInput.value.trim();
  if (!name) {
    showToast('Folder name cannot be empty', 'error');
    return;
  }
  try {
    els.confirmNewFolderBtn.disabled = true;
    const res = await fetch('/api/backup/folders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parent_path: state.currentPath, name }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to create folder');
    showToast(`Created folder "${name}"`, 'success');
    closeNewFolderModal();
    loadFolder(state.currentPath, false);
  } catch (err) {
    showToast(err.message || 'Error creating folder', 'error');
  } finally {
    els.confirmNewFolderBtn.disabled = false;
  }
}
// --- Context Menu ---
function openContextMenu(x, y, target) {
  state.contextMenuTarget = target;
  const isVid = target && (target.type === 'video' || (target.kind === 'file' && target.ext && ['.mp4', '.mkv', '.webm', '.avi', '.mov', '.wmv', '.flv', '.m4v', '.ts'].includes(target.ext.toLowerCase())));
  const posterBtn = els.contextMenu ? els.contextMenu.querySelector('button[data-cm="set-poster"]') : null;
  if (posterBtn) {
    posterBtn.classList.toggle('hidden', !isVid);
    posterBtn.classList.toggle('flex', isVid);
  }
  const animBtn = els.contextMenu ? els.contextMenu.querySelector('button[data-cm="gen-animated"]') : null;
  if (animBtn) {
    animBtn.classList.toggle('hidden', !isVid);
    animBtn.classList.toggle('flex', isVid);
  }
  els.contextMenu.style.left = `${x}px`;
  els.contextMenu.style.top = `${y}px`;
  els.contextMenu.classList.remove('hidden');
  // Keep within viewport
  const rect = els.contextMenu.getBoundingClientRect();
  if (rect.right > window.innerWidth) els.contextMenu.style.left = `${window.innerWidth - rect.width - 8}px`;
  if (rect.bottom > window.innerHeight) els.contextMenu.style.top = `${window.innerHeight - rect.height - 8}px`;
}
function closeContextMenu() {
  els.contextMenu.classList.add('hidden');
  state.contextMenuTarget = null;
}
function runContextAction(action) {
  const target = state.contextMenuTarget;
  closeContextMenu();
  if (!target) return;
  if (action === 'move') openMoveModal(target);
  else if (action === 'rename') openRenameModal(target.path, target.name, target.kind);
  else if (action === 'delete') openDeleteModal(target.path, target.name, target.size_formatted || '', target.kind);
  else if (action === 'info') openMediaInfoModal(target);
  else if (action === 'download') downloadMediaFile(target);
  else if (action === 'set-poster') {
    const idx = state.filteredFiles ? state.filteredFiles.findIndex(f => f.path === target.path) : -1;
    if (idx >= 0) {
      openFancybox(idx);
      showToast("Scrub to desired frame and click 'Set Poster' (or press P)", 'info');
    }
  } else if (action === 'gen-animated') {
    generateAnimatedPreviewForTarget(target);
  }
}
// Tap-and-hold (touch) support: opens the same context menu as right-click.
function attachLongPress(el, targetFn) {
  let timer = null;
  let startX = 0;
  let startY = 0;

  const cancel = () => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  };

  el.addEventListener('touchstart', (e) => {
    if (e.touches.length !== 1) return;
    const t = e.touches[0];
    startX = t.clientX;
    startY = t.clientY;
    cancel();
    timer = setTimeout(() => {
      timer = null;
      // Suppress the synthetic click that follows the touch so the menu
      // isn't closed instantly and the lightbox doesn't open.
      state.suppressClickUntil = Date.now() + 800;
      openContextMenu(t.clientX, t.clientY, targetFn());
      if (navigator.vibrate) {
        try { navigator.vibrate(10); } catch (err) { /* ignore */ }
      }
    }, 500);
  }, { passive: true });

  el.addEventListener('touchmove', (e) => {
    if (!timer) return;
    const t = e.touches[0];
    if (!t || Math.abs(t.clientX - startX) > 12 || Math.abs(t.clientY - startY) > 12) {
      cancel();
    }
  }, { passive: true });

  el.addEventListener('touchend', cancel);
  el.addEventListener('touchcancel', cancel);
}
// --- Move To... ---
function openMoveModal(target) {
  state.moveItems = [target];
  els.moveItemName.textContent = target.name;
  state.movePickerPath = state.basePath || state.currentPath;
  els.moveModal.classList.remove('hidden');
  loadMoveFolders(state.movePickerPath);
}
function openBulkMove(items) {
  state.moveItems = items;
  els.moveItemName.textContent = `${items.length} items`;
  state.movePickerPath = state.basePath || state.currentPath;
  els.moveModal.classList.remove('hidden');
  loadMoveFolders(state.movePickerPath);
}
function closeMoveModal() {
  els.moveModal.classList.add('hidden');
  state.moveItems = [];
}
async function loadMoveFolders(path) {
  try {
    const res = await fetch(`/api/backup/folders?path=${encodeURIComponent(path)}`);
    const data = await res.json();
    state.movePickerPath = data.current_path || path;
    state.movePickerFolders = data.subfolders || [];
    renderMoveFolders();
  } catch (err) {
    showToast('Error loading folders', 'error');
  }
}
function renderMoveFolders() {
  els.moveCurrentPath.textContent = state.movePickerPath;
  els.moveCurrentPath.title = state.movePickerPath;
  els.moveFolderList.innerHTML = '';

  const dirs = (state.movePickerFolders || []).filter(s =>
    !(s.is_zip || (s.path && s.path.toLowerCase().endsWith('.zip')))
  );

  if (dirs.length === 0) {
    els.moveFolderList.innerHTML = `<div class="text-xs text-slate-500 px-2 py-3 text-center">No subfolders — choose this location</div>`;
    return;
  }

  dirs.forEach(s => {
    const btn = document.createElement('button');
    btn.className = 'flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs text-slate-200 hover:bg-slate-800 text-left transition';
    btn.innerHTML = `<span>📁</span><span class="truncate flex-1">${escapeHtml(s.name)}</span><span class="text-slate-500 shrink-0">›</span>`;
    btn.addEventListener('click', () => loadMoveFolders(s.path));
    els.moveFolderList.appendChild(btn);
  });
}
function movePickerUp() {
  const base = state.basePath;
  const cur = state.movePickerPath;
  if (!cur || cur === base) return;
  const parent = cur.substring(0, cur.lastIndexOf('/')) || base;
  loadMoveFolders(parent || base);
}
async function submitMove() {
  if (!state.moveItems.length) return;
  const paths = state.moveItems.map(t => t.path);
  const dest = state.movePickerPath;
  try {
    const res = await fetch('/api/backup/move/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths, destination_folder: dest }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Move failed');
    if (data.collision_count > 0) {
      state.pendingMove = { paths, destination_folder: dest };
      els.moveConflictCount.textContent = `${data.collision_count} item(s) already exist at the destination.`;
      els.moveConflictModal.classList.remove('hidden');
      return;
    }
    await doMove(paths, dest, 'auto_rename');
  } catch (err) {
    showToast(err.message || 'Error moving', 'error');
  }
}
async function doMove(paths, dest, policy) {
  try {
    const res = await fetch('/api/backup/move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths, destination_folder: dest, conflict_policy: policy }),
    });
    const data = await res.json();
    if (!res.ok) {
      const msg = (data.detail && data.detail.message) ? data.detail.message : (data.detail || 'Move failed');
      throw new Error(msg);
    }
    showToast(`Queued ${data.queued} move(s)`, 'success');
    els.moveConflictModal.classList.add('hidden');
    closeMoveModal();
    if (state.fancyboxInstance) {
      Fancybox.close();
    }
    awaitLibraryOpsAndRefresh((data.tasks || []).map(t => t.task_id), state.currentPath);
  } catch (err) {
    showToast(err.message || 'Error moving', 'error');
  }
}
// Wait for queued library operations to finish, then reload the current
// folder so the UI reflects the move/delete/rename without a manual refresh.
async function awaitLibraryOpsAndRefresh(taskIds, expectedPath) {
  const pending = new Set((taskIds || []).filter(Boolean));
  const deadline = Date.now() + 10 * 60 * 1000;
  while (pending.size && Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 800));
    try {
      const res = await fetch('/api/backup/library-ops/status');
      if (!res.ok) break;
      const data = await res.json();
      const activeId = data.active ? data.active.id : null;
      const queuedIds = new Set((data.queued || []).map(q => q.id));
      Array.from(pending).forEach(id => {
        if (id !== activeId && !queuedIds.has(id)) pending.delete(id);
      });
    } catch (e) {
      break;
    }
  }
  if (!expectedPath || state.currentPath === expectedPath) {
    loadFolder(state.currentPath, false);
  }
}
// --- Multi-select ---
function selectedItems() {
  return state.filteredFiles.filter(f => state.selectedPaths.has(f.path));
}
function updateSelectionToolbar() {
  const n = state.selectedPaths.size;
  els.selectionCount.textContent = n;
  if (n > 0) els.selectionToolbar.classList.remove('hidden');
  else els.selectionToolbar.classList.add('hidden');
  updateSelectAllBtn();
}
function allFilteredSelected() {
  return state.filteredFiles.length > 0
    && state.filteredFiles.every(f => state.selectedPaths.has(f.path));
}
function updateSelectAllBtn() {
  if (!els.selectAllBtn) return;
  const hasFiles = state.filteredFiles.length > 0;
  els.selectAllBtn.classList.toggle('hidden', !hasFiles);
  els.selectAllBtn.textContent = allFilteredSelected() ? '☐ Deselect All' : '☑️ Select All';
  els.selectAllBtn.title = allFilteredSelected()
    ? 'Deselect every item in the current view'
    : 'Select every item in the current view';
}
// Select every item in the current filtered view, or clear when all are
// already selected.
function toggleSelectAll() {
  if (state.filteredFiles.length === 0) return;
  if (allFilteredSelected()) {
    state.selectedPaths.clear();
    state.lastSelectedIndex = null;
  } else {
    state.filteredFiles.forEach(f => state.selectedPaths.add(f.path));
    state.lastSelectedIndex = state.filteredFiles.length - 1;
  }
  updateSelectionToolbar();
  updateCardSelectStates();
}
function updateCardSelectStates() {
  document.querySelectorAll('.card-select').forEach(cb => {
    const idx = parseInt(cb.dataset.selectIdx, 10);
    const f = state.filteredFiles[idx];
    cb.checked = f ? state.selectedPaths.has(f.path) : false;
  });
}
function toggleSelect(path, index) {
  if (state.selectedPaths.has(path)) state.selectedPaths.delete(path);
  else state.selectedPaths.add(path);
  state.lastSelectedIndex = index;
  updateSelectionToolbar();
  updateCardSelectStates();
}
function rangeSelect(startIdx, endIdx) {
  const a = Math.min(startIdx, endIdx);
  const b = Math.max(startIdx, endIdx);
  for (let i = a; i <= b; i++) {
    const f = state.filteredFiles[i];
    if (f) state.selectedPaths.add(f.path);
  }
  updateSelectionToolbar();
  updateCardSelectStates();
}
function clearSelection() {
  state.selectedPaths.clear();
  state.lastSelectedIndex = null;
  updateSelectionToolbar();
  updateCardSelectStates();
}
// --- Bulk delete ---
function openBulkDeleteModal() {
  const paths = Array.from(state.selectedPaths);
  state.itemToDelete = {
    path: null,
    name: `${paths.length} items`,
    size: '',
    kind: 'file',
    bulk: true,
    paths,
  };
  els.deleteFileName.textContent = `${paths.length} item${paths.length === 1 ? '' : 's'} selected`;
  els.deleteFileSize.textContent = '';
  els.deleteModalTitle.textContent = `Delete ${paths.length} item${paths.length === 1 ? '' : 's'}?`;
  els.deleteModalSubtitle.textContent = 'This will permanently delete the selected files and folders.';
  els.confirmDeleteBtn.textContent = `Delete ${paths.length} item${paths.length === 1 ? '' : 's'}`;
  resetDeleteConfirmation();
  els.deleteModal.classList.remove('hidden');
}
// --- Bulk rename ---
function openBulkRenameModal() {
  state.bulkRenamePreview = [];
  els.bulkRenameCount.textContent = `${state.selectedPaths.size} file${state.selectedPaths.size === 1 ? '' : 's'}`;
  els.bulkRenamePreviewList.innerHTML = `<div class="text-xs text-slate-500 px-2 py-3 text-center">Configure a mode, then preview</div>`;
  els.bulkRenameModal.classList.remove('hidden');
  renderBulkRenameParams();
}
function closeBulkRenameModal() {
  els.bulkRenameModal.classList.add('hidden');
}
function renderBulkRenameParams() {
  const mode = els.bulkRenameMode.value;
  document.querySelectorAll('.bulk-param').forEach(el => el.classList.add('hidden'));
  const map = {
    prefix: 'bulkPrefixParams',
    numbering: 'bulkNumberingParams',
    base_counter: 'bulkBaseCounterParams',
    find_replace: 'bulkFindReplaceParams',
    case: 'bulkCaseParams',
  };
  const el = document.getElementById(map[mode]);
  if (el) {
    el.classList.remove('hidden');
    if (mode === 'numbering' || mode === 'base_counter') el.classList.add('flex');
  }
}
function collectBulkRenameParams() {
  const mode = els.bulkRenameMode.value;
  if (mode === 'prefix') return { prefix: document.getElementById('bulkPrefixInput').value.trim(), suffix: document.getElementById('bulkSuffixInput').value.trim() };
  if (mode === 'numbering') return {
    start: parseInt(document.getElementById('bulkNumStart').value, 10) || 1,
    step: parseInt(document.getElementById('bulkNumStep').value, 10) || 1,
    padding: parseInt(document.getElementById('bulkNumPadding').value, 10) || 0,
    position: document.getElementById('bulkNumPosition').value,
  };
  if (mode === 'base_counter') return {
    base: document.getElementById('bulkBaseName').value.trim(),
    start: parseInt(document.getElementById('bulkBaseStart').value, 10) || 1,
    padding: parseInt(document.getElementById('bulkBasePadding').value, 10) || 0,
  };
  if (mode === 'find_replace') return { find: document.getElementById('bulkFindInput').value, replace: document.getElementById('bulkReplaceInput').value };
  if (mode === 'case') return { case: document.getElementById('bulkCaseSelect').value };
  return {};
}
async function previewBulkRename() {
  const paths = Array.from(state.selectedPaths);
  if (!paths.length) return;
  try {
    const res = await fetch('/api/backup/bulk/rename/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths, mode: els.bulkRenameMode.value, params: collectBulkRenameParams() }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to preview');
    state.bulkRenamePreview = data.items || [];
    if (!state.bulkRenamePreview.length) {
      els.bulkRenamePreviewList.innerHTML = `<div class="text-xs text-slate-500 px-2 py-3 text-center">Nothing to rename</div>`;
      return;
    }
    els.bulkRenamePreviewList.innerHTML = state.bulkRenamePreview.map(item => {
      const changed = item.new_name !== item.name;
      const conflictCls = item.conflict ? 'text-red-300' : (changed ? 'text-emerald-300' : 'text-slate-500');
      return `
        <div class="flex items-center gap-2 text-xs">
          <span class="truncate text-slate-400">${escapeHtml(item.name)}</span>
          <span class="shrink-0 text-slate-500">→</span>
          <span class="truncate ${conflictCls}">${escapeHtml(item.new_name)}</span>
        </div>
      `;
    }).join('');
  } catch (err) {
    showToast(err.message || 'Error previewing rename', 'error');
  }
}
async function applyBulkRename() {
  const paths = Array.from(state.selectedPaths);
  if (!paths.length) return;
  try {
    const res = await fetch('/api/backup/bulk/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths, mode: els.bulkRenameMode.value, params: collectBulkRenameParams() }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to rename');
    showToast(`Queued ${data.queued} rename(s)`, 'success');
    closeBulkRenameModal();
    clearSelection();
    awaitLibraryOpsAndRefresh((data.tasks || []).map(t => t.task_id), state.currentPath);
  } catch (err) {
    showToast(err.message || 'Error renaming', 'error');
  }
}
async function bulkWebpOptimize() {
  const paths = Array.from(state.selectedPaths);
  if (!paths.length) return;
  try {
    const res = await fetch('/api/backup/bulk/webp-optimize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to queue WebP optimization');
    let msg = `Queued ${data.images_queued} image(s) for WebP`;
    if (data.folders_queued) msg += `, ${data.folders_queued} folder(s)`;
    if (data.skipped) msg += ` (${data.skipped} skipped)`;
    showToast(msg, 'success');
    clearSelection();
  } catch (err) {
    showToast(err.message || 'Error queueing WebP optimization', 'error');
  }
}
function formatDate(mtime) {
  if (!mtime) return '--';
  try {
    const d = new Date(mtime * 1000);
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  } catch (e) {
    return '--';
  }
}

export { allFilteredSelected, applyBulkRename, attachLongPress, awaitLibraryOpsAndRefresh, bulkWebpOptimize, clearSelection, closeBulkRenameModal, closeContextMenu, closeDeleteModal, closeMoveModal, closeNewFolderModal, closeRenameModal, collectBulkRenameParams, doMove, formatDate, loadMoveFolders, movePickerUp, openBulkDeleteModal, openBulkMove, openBulkRenameModal, openContextMenu, openDeleteModal, openMoveModal, openNewFolderModal, openRenameModal, previewBulkRename, rangeSelect, renderBulkRenameParams, renderMoveFolders, resetDeleteConfirmation, runContextAction, selectedItems, submitDelete, submitMove, submitNewFolder, submitRename, toggleSelect, toggleSelectAll, updateCardSelectStates, updateSelectAllBtn, updateSelectionToolbar };
