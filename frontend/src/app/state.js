import { mobileUndoBtn, toastContainer, undoBtn } from './elements.js';
import { renderRecentFolders } from './folders.js';

// Sorter application state and small shared helpers.
// Split out of the former monolithic sorter index.js.

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
function escapeHtml(text) {
if (!text) return "";
return text
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;")
  .replace(/'/g, "&#039;");
}

export { addRecentFolder, escapeHtml, loadLocalRecents, saveLocalRecents, showToast, state, updateUndoState };
