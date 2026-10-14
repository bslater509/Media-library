import { currentVideo } from './config.js';
import { clearSearchBtn, folderListBox, folderMatchCount, folderSearchInput, primaryMoveBtn, primaryMoveBtnText, recentFoldersContainer } from './elements.js';
import { createAndMoveFolder, executeMove } from './moves.js';
import { escapeHtml, state } from './state.js';

// Destination folder list, recents and filtering.
// Split out of the former monolithic sorter index.js.

// Render Recent Destinations Carousel (from local storage)
function renderRecentFolders() {
recentFoldersContainer.innerHTML = "";
if (!state.recentDestinations || state.recentDestinations.length === 0) {
  recentFoldersContainer.innerHTML = `<span class="text-xs text-slate-500 italic py-1 whitespace-nowrap">No recent folders yet</span>`;
  return;
}

state.recentDestinations.slice(0, 6).forEach((folder, idx) => {
  const btn = document.createElement("button");
  const shortcutNum = idx + 1;
  btn.className =
    "min-h-[34px] px-2.5 py-1 bg-slate-800 hover:bg-slate-700 active:scale-95 border border-slate-700 hover:border-emerald-500/50 text-slate-200 rounded-lg text-xs font-medium flex items-center gap-1.5 transition whitespace-nowrap group shrink-0";
  btn.title = `Move to ${folder} (Key: ${shortcutNum})`;
  btn.innerHTML = `
    <span class="text-slate-400 group-hover:text-emerald-400 text-xs">📁</span>
    <span class="max-w-[120px] truncate">${escapeHtml(folder)}</span>
    <kbd class="px-1 py-0.2 rounded bg-slate-900 border border-slate-700 text-[10px] font-mono text-emerald-300">${shortcutNum}</kbd>
  `;
  btn.addEventListener("click", () => {
    executeMove(folder);
  });
  recentFoldersContainer.appendChild(btn);
});
}
// Filter Destinations List
function filterFolders(query = "") {
const q = query.trim().toLowerCase();
if (clearSearchBtn) {
  clearSearchBtn.classList.toggle("hidden", !query);
}

if (!q) {
  state.filteredFolders = [...state.destinations];
} else {
  state.filteredFolders = state.destinations.filter((f) =>
    f.toLowerCase().includes(q)
  );
}

folderMatchCount.textContent = state.filteredFolders.length;
state.highlightedIndex = state.filteredFolders.length > 0 ? 0 : -1;
renderFolderList();
updatePrimaryMoveBtn();
}
// Render Folder List (clean without stars)
function renderFolderList() {
folderListBox.innerHTML = "";

if (state.filteredFolders.length === 0) {
  const q = folderSearchInput.value.trim();
  if (q) {
    folderListBox.innerHTML = `
      <div class="p-4 text-center text-xs text-slate-400 flex flex-col items-center gap-2">
        <p>No matching folder for "<strong>${escapeHtml(q)}</strong>"</p>
        <button id="createInlineFolderBtn" class="min-h-[42px] px-4 py-2 bg-emerald-600 hover:bg-emerald-500 active:scale-95 text-white rounded-xl text-xs font-semibold shadow">
          + Create "${escapeHtml(q)}" and Move
        </button>
      </div>
    `;
    const btn = document.getElementById("createInlineFolderBtn");
    if (btn) {
      btn.addEventListener("click", () => {
        createAndMoveFolder(q);
      });
    }
  } else {
    folderListBox.innerHTML = `<div class="p-4 text-center text-xs text-slate-500">No destination folders found.</div>`;
  }
  return;
}

state.filteredFolders.forEach((folder, idx) => {
  const isSelected = idx === state.highlightedIndex;
  const item = document.createElement("div");
  item.className = `folder-item px-3.5 py-2.5 text-xs cursor-pointer flex items-center justify-between select-none ${
    isSelected ? "selected" : "text-slate-300"
  }`;
  item.dataset.index = idx;

  item.innerHTML = `
    <div class="flex items-center gap-2 truncate min-w-0">
      <span class="text-slate-400 text-sm">📁</span>
      <span class="truncate font-medium text-xs sm:text-sm">${escapeHtml(folder)}</span>
    </div>
    <span class="text-[10px] text-slate-500 opacity-60 font-mono hidden sm:inline">Enter ↵</span>
  `;

  // Row Click -> Move
  item.addEventListener("click", () => {
    state.highlightedIndex = idx;
    updateHighlight();
    executeMove(folder);
  });

  item.addEventListener("mouseenter", () => {
    state.highlightedIndex = idx;
    updateHighlight();
  });

  folderListBox.appendChild(item);
});

scrollHighlightedIntoView();
}
function updateHighlight() {
const items = folderListBox.querySelectorAll(".folder-item");
items.forEach((item, idx) => {
  if (idx === state.highlightedIndex) {
    item.classList.add("selected");
  } else {
    item.classList.remove("selected");
  }
});
updatePrimaryMoveBtn();
}
function scrollHighlightedIntoView() {
const selectedEl = folderListBox.querySelector(".folder-item.selected");
if (selectedEl) {
  selectedEl.scrollIntoView({ block: "nearest" });
}
}
function updatePrimaryMoveBtn() {
if (state.highlightedIndex >= 0 && state.highlightedIndex < state.filteredFolders.length) {
  const target = state.filteredFolders[state.highlightedIndex];
  primaryMoveBtnText.textContent = `Move to "${target}"`;
  primaryMoveBtn.disabled = !currentVideo();
} else {
  const q = folderSearchInput.value.trim();
  if (q) {
    primaryMoveBtnText.textContent = `Create "${q}" & Move`;
    primaryMoveBtn.disabled = !currentVideo();
  } else {
    primaryMoveBtnText.textContent = "Select a subfolder above";
    primaryMoveBtn.disabled = true;
  }
}
}

export { filterFolders, renderFolderList, renderRecentFolders, scrollHighlightedIntoView, updateHighlight, updatePrimaryMoveBtn };
