import { currentVideo, loadDestinations, refreshQueue, updateUI } from './config.js';
import { folderSearchInput } from './elements.js';
import { filterFolders } from './folders.js';
import { pollTransfers } from './polling.js';
import { addRecentFolder, escapeHtml, showToast, state, updateUndoState } from './state.js';

// Moving the current video, creating folders and undo.
// Split out of the former monolithic sorter index.js.

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
      is_favorite: Boolean(video.is_favorite),
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

export { createAndMoveFolder, executeMove, prevVideo, skipVideo, undoLastMove };
