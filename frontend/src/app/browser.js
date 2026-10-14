import { loadQuickNav, updateUI } from './config.js';
import { browserFolderList, browserModal, browserPathInput, browserUpBtn, browserVideoCountIndicator } from './elements.js';
import { escapeHtml, showToast, state } from './state.js';

// Source folder browser modal and quick navigation.
// Split out of the former monolithic sorter index.js.

// Source Directory Browser Modal
let browserCurrentPath = "";
async function openBrowser(path = null) {
browserModal.classList.remove("hidden");
loadQuickNav();
await browseDirectory(path || state.sourceDir || state.destDir || "/");
}
function closeBrowser() {
browserModal.classList.add("hidden");
}
async function browseDirectory(path) {
browserFolderList.innerHTML = `<div class="p-4 text-center text-xs text-slate-500">Loading...</div>`;
try {
  const res = await fetch(`/api/browse?path=${encodeURIComponent(path)}`, { cache: 'no-store' });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || "Cannot browse directory");
  }

  browserCurrentPath = data.current_path;
  browserPathInput.value = data.current_path;
  browserUpBtn.disabled = !data.parent_path;
  browserUpBtn.dataset.parent = data.parent_path || "";
  browserVideoCountIndicator.textContent = `${data.video_count_here} video(s) found in this folder`;

  browserFolderList.innerHTML = "";
  if (data.folders.length === 0) {
    browserFolderList.innerHTML = `<div class="p-4 text-center text-xs text-slate-500">No subdirectories here</div>`;
  } else {
    data.folders.forEach((f) => {
      const row = document.createElement("div");
      row.className = "flex items-center justify-between p-2.5 hover:bg-slate-800 active:bg-slate-700 rounded-xl cursor-pointer text-xs text-slate-300 transition min-h-[44px]";
      const hasEdgeSpace = f.name !== f.name.trim();
      const marker = hasEdgeSpace
        ? `<span class="ml-1 px-1 rounded bg-amber-500/20 text-amber-300 text-[9px] font-mono" title="Folder name contains leading/trailing spaces">␠</span>`
        : "";
      row.innerHTML = `
        <div class="flex items-center gap-2 truncate min-w-0">
          <span class="text-emerald-400 text-sm">📁</span>
          <span class="truncate font-medium text-xs sm:text-sm">${escapeHtml(f.name)}</span>${marker}
        </div>
        <span class="text-[10px] text-slate-500 font-mono">Open ➔</span>
      `;
      row.addEventListener("click", () => {
        browseDirectory(f.path);
      });
      browserFolderList.appendChild(row);
    });
  }
} catch (err) {
  browserFolderList.innerHTML = `<div class="p-4 text-center text-xs text-red-400">Error: ${escapeHtml(err.message)}</div>`;
}
}
async function setSourceDirectory(path, notify = true) {
try {
  const res = await fetch("/api/source/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_dir: path }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || "Failed to open directory");
  }

  state.sourceDir = data.source_dir;
  state.queue = data.videos || [];
  state.currentIndex = 0;
  localStorage.setItem("video_sorter_source", state.sourceDir);

  if (notify) {
    showToast(`Loaded ${data.count} videos`, "success");
  }
  updateUI();
} catch (err) {
  if (notify) {
    showToast(`Error: ${err.message}`, "error");
  }
}
}

export { browseDirectory, browserCurrentPath, closeBrowser, openBrowser, setSourceDirectory };
