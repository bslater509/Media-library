import { currentVideo, refreshQueue, updateUI } from './config.js';
import { confirmDeleteBtn, deleteFilenameDisplay, deleteModal, renameInput, renameModal } from './elements.js';
import { escapeHtml, showToast, state } from './state.js';

// Rename and delete confirmation modals.
// Split out of the former monolithic sorter index.js.

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

export { closeDeleteModal, closeRenameModal, openDeleteModal, openRenameModal, submitDelete, submitRename };
