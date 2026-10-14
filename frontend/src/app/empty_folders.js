import { cancelEmptyFoldersModalBtn, closeEmptyFoldersModalBtn, confirmEmptyFoldersBtn, confirmEmptyFoldersBtnText, deleteEmptyFoldersBtn, emptyFoldersCandidateCount, emptyFoldersCandidatesList, emptyFoldersFilterInput, emptyFoldersLeafCount, emptyFoldersModal, emptyFoldersSelectAllToggle, emptyFoldersSelectedBadge, transferDropdown } from './elements.js';
import { escapeHtml, showToast } from './state.js';

// Empty-folder cleanup modal and selection.
// Split out of the former monolithic sorter index.js.

let emptyFoldersCandidates = [];
let emptyFoldersSelectedPaths = new Set();
let emptyFoldersFilterQuery = "";
async function openEmptyFoldersModal() {
if (transferDropdown) {
  transferDropdown.classList.add("hidden");
}
const backdrop = document.getElementById("transferBackdrop");
if (backdrop) {
  backdrop.classList.add("hidden");
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

export { closeEmptyFoldersModal, emptyFoldersCandidates, emptyFoldersFilterQuery, emptyFoldersSelectedPaths, openEmptyFoldersModal, renderEmptyFoldersCandidates, submitDeleteEmptyFolders };
