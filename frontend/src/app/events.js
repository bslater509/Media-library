import { browseDirectory, browserCurrentPath, closeBrowser, openBrowser, setSourceDirectory } from './browser.js';
import { currentVideo, loadDestinations } from './config.js';
import { hideDuplicateBanner, toggleDuplicateCompare, trashCurrentDuplicate } from './duplicates.js';
import { autoTrashDuplicateToggle, browserGoBtn, browserPathInput, browserUpBtn, cancelBrowserBtn, cancelDeleteBtn, cancelRenameBtn, clearSearchBtn, closeBrowserModalBtn, closeShortcutsBtn, confirmDeleteBtn, confirmRenameBtn, createAndMoveBtn, currentVideoTitle, deleteBtn, dismissDuplicateBtn, duplicateBanner, emptyStateBrowseBtn, folderSearchInput, mobileDeleteBtn, mobilePrevBtn, mobileRenameBtn, mobileSkipBtn, mobileUndoBtn, newFolderInput, openFolderBrowserBtn, prevBtn, primaryMoveBtn, refreshDestsBtn, renameBtn, renameHeaderBtn, renameInput, selectCurrentFolderBtn, shortcutsBtn, shortcutsModal, skipBtn, toggleDuplicateCompareBtn, toggleHashPauseBtn, toggleStreamModeBtn, toggleThumbPauseBtn, toggleTranscodePauseBtn, toggleWebpPauseBtn, transferBadgeBtn, transferDropdown, trashIncomingDuplicateBtn, undoBtn, videoPlayer } from './elements.js';
import { filterFolders, scrollHighlightedIntoView, updateHighlight } from './folders.js';
import { closeDeleteModal, closeRenameModal, openDeleteModal, openRenameModal, submitDelete, submitRename } from './modals.js';
import { createAndMoveFolder, executeMove, prevVideo, skipVideo, undoLastMove } from './moves.js';
import { pollTransfers, toggleDuplicateHashPause, toggleThumbnailPause, toggleTranscodePause, toggleWebpPause } from './polling.js';
import { showToast, state } from './state.js';
import { loadVideoSource, setupSorterTouchSeek } from './video.js';

// Event wiring and the theme customizer.
// Split out of the former monolithic sorter index.js.

// Event Listeners setup
function setupEventListeners() {
const transferBackdrop = document.getElementById("transferBackdrop");
const closeTransferDropdownBtn = document.getElementById("closeTransferDropdownBtn");
const openFolderBrowserMobileBtn = document.getElementById("openFolderBrowserMobileBtn");

if (openFolderBrowserMobileBtn) {
  openFolderBrowserMobileBtn.addEventListener("click", () => openBrowser());
}

if (closeTransferDropdownBtn) {
  closeTransferDropdownBtn.addEventListener("click", () => {
    transferDropdown.classList.add("hidden");
    if (transferBackdrop) transferBackdrop.classList.add("hidden");
  });
}

// Footer "Close" button uses the same close path as the header ✕ so the
// dimming backdrop overlay is dismissed too.
transferDropdown.querySelectorAll("[data-tasks-modal-close]").forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    transferDropdown.classList.add("hidden");
    if (transferBackdrop) transferBackdrop.classList.add("hidden");
  });
});

if (transferBackdrop) {
  transferBackdrop.addEventListener("click", () => {
    transferDropdown.classList.add("hidden");
    transferBackdrop.classList.add("hidden");
  });
}

// Tasks tab switcher
function switchTasksTab(tabName, persist = true) {
  const tabs = ['active', 'workers', 'tools'];
  const target = tabs.includes(tabName) ? tabName : 'active';
  // Only persist explicit user selections; the automatic "open on Active
  // because work is running" choice must not clobber the saved preference.
  if (persist) {
    try { localStorage.setItem('tasks_active_tab', target); } catch (e) {}
  }

  const btnActive = document.getElementById('tasksTabBtnActive');
  const btnWorkers = document.getElementById('tasksTabBtnWorkers');
  const btnTools = document.getElementById('tasksTabBtnTools');
  const paneActive = document.getElementById('tasksPaneActive');
  const paneWorkers = document.getElementById('tasksPaneWorkers');
  const paneTools = document.getElementById('tasksPaneTools');

  const setTabStyle = (btn, isActive) => {
    if (!btn) return;
    if (isActive) {
      btn.className = 'tasks-tab-btn py-2.5 px-3 border-b-2 font-medium text-xs sm:text-sm flex items-center gap-1.5 transition text-emerald-400 border-emerald-400 cursor-pointer';
    } else {
      btn.className = 'tasks-tab-btn py-2.5 px-3 border-b-2 font-medium text-xs sm:text-sm flex items-center gap-1.5 transition text-slate-400 border-transparent hover:text-slate-200 cursor-pointer';
    }
    btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
  };

  setTabStyle(btnActive, target === 'active');
  setTabStyle(btnWorkers, target === 'workers');
  setTabStyle(btnTools, target === 'tools');

  if (paneActive) paneActive.classList.toggle('hidden', target !== 'active');
  if (paneWorkers) paneWorkers.classList.toggle('hidden', target !== 'workers');
  if (paneTools) paneTools.classList.toggle('hidden', target !== 'tools');
}
window.switchTasksTab = switchTasksTab;

// Toggle Transfer Dropdown / Modal with smart tab selection
if (transferBadgeBtn && transferDropdown) {
  transferBadgeBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const isHidden = transferDropdown.classList.toggle("hidden");
    if (transferBackdrop) {
      if (!isHidden) {
        transferBackdrop.classList.remove("hidden");
      } else {
        transferBackdrop.classList.add("hidden");
      }
    }
    if (!isHidden) {
      const hasActiveQueues = (state.totalPendingTransfers || 0) > 0 || (state.totalPendingTranscodes || 0) > 0 || !!state.activeTransfer || !!state.activeTranscode;
      if (hasActiveQueues) {
        switchTasksTab('active', false);
      } else {
        let saved = 'workers';
        try { saved = localStorage.getItem('tasks_active_tab') || 'workers'; } catch (e) {}
        switchTasksTab(saved);
      }
      pollTransfers(true);
    }
  });

  if (transferDropdown) {
    transferDropdown.addEventListener("click", (e) => {
      if (e.target === transferDropdown) {
        transferDropdown.classList.add("hidden");
        if (transferBackdrop) transferBackdrop.classList.add("hidden");
      }
    });
  }

  const btnActive = document.getElementById('tasksTabBtnActive');
  const btnWorkers = document.getElementById('tasksTabBtnWorkers');
  const btnTools = document.getElementById('tasksTabBtnTools');
  const jumpToWorkers = document.getElementById('jumpToWorkersBtn');
  if (btnActive) btnActive.addEventListener('click', () => switchTasksTab('active'));
  if (btnWorkers) btnWorkers.addEventListener('click', () => switchTasksTab('workers'));
  if (btnTools) btnTools.addEventListener('click', () => switchTasksTab('tools'));
  if (jumpToWorkers) jumpToWorkers.addEventListener('click', () => switchTasksTab('workers'));

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !transferDropdown.classList.contains("hidden")) {
      transferDropdown.classList.add("hidden");
      if (transferBackdrop) transferBackdrop.classList.add("hidden");
    }
  });
}

// Pre-Transcoder Pause/Resume Toggle
if (toggleTranscodePauseBtn) {
  toggleTranscodePauseBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleTranscodePause();
  });
}

// Thumbnail Generator Pause/Resume Toggle
if (toggleThumbPauseBtn) {
  toggleThumbPauseBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleThumbnailPause();
  });
}

// Duplicate Hash Verifier Pause/Resume Toggle
if (toggleHashPauseBtn) {
  toggleHashPauseBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleDuplicateHashPause();
  });
}

// WebP Optimizer Pause/Resume Toggle
if (toggleWebpPauseBtn) {
  toggleWebpPauseBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleWebpPause();
  });
}

// Folder Search Input
folderSearchInput.addEventListener("input", (e) => {
  filterFolders(e.target.value);
});

if (clearSearchBtn) {
  clearSearchBtn.addEventListener("click", () => {
    folderSearchInput.value = "";
    filterFolders("");
    folderSearchInput.focus();
  });
}

folderSearchInput.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") {
    e.preventDefault();
    if (state.filteredFolders.length > 0) {
      state.highlightedIndex = (state.highlightedIndex + 1) % state.filteredFolders.length;
      updateHighlight();
      scrollHighlightedIntoView();
    }
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    if (state.filteredFolders.length > 0) {
      state.highlightedIndex = (state.highlightedIndex - 1 + state.filteredFolders.length) % state.filteredFolders.length;
      updateHighlight();
      scrollHighlightedIntoView();
    }
  } else if (e.key === "Enter") {
    e.preventDefault();
    if (state.highlightedIndex >= 0 && state.highlightedIndex < state.filteredFolders.length) {
      executeMove(state.filteredFolders[state.highlightedIndex]);
    } else {
      const q = folderSearchInput.value.trim();
      if (q) {
        createAndMoveFolder(q);
      }
    }
  } else if (e.key === "Escape") {
    folderSearchInput.blur();
  }
});

// Primary Move Button
primaryMoveBtn.addEventListener("click", () => {
  if (state.highlightedIndex >= 0 && state.highlightedIndex < state.filteredFolders.length) {
    executeMove(state.filteredFolders[state.highlightedIndex]);
  } else {
    const q = folderSearchInput.value.trim();
    if (q) {
      createAndMoveFolder(q);
    }
  }
});

// Inline New Folder Create
createAndMoveBtn.addEventListener("click", () => {
  const val = newFolderInput.value.trim();
  if (val) {
    createAndMoveFolder(val);
    newFolderInput.value = "";
  }
});
newFolderInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const val = newFolderInput.value.trim();
    if (val) {
      createAndMoveFolder(val);
      newFolderInput.value = "";
    }
  }
});

// Toolbar Actions (Desktop)
if (prevBtn) prevBtn.addEventListener("click", prevVideo);
if (skipBtn) skipBtn.addEventListener("click", skipVideo);
if (renameBtn) renameBtn.addEventListener("click", openRenameModal);
if (deleteBtn) deleteBtn.addEventListener("click", openDeleteModal);
if (undoBtn) undoBtn.addEventListener("click", undoLastMove);

// Mobile Bottom Bar Actions
if (mobilePrevBtn) mobilePrevBtn.addEventListener("click", prevVideo);
if (mobileSkipBtn) mobileSkipBtn.addEventListener("click", skipVideo);
if (mobileUndoBtn) mobileUndoBtn.addEventListener("click", undoLastMove);
if (mobileRenameBtn) mobileRenameBtn.addEventListener("click", openRenameModal);
if (mobileDeleteBtn) mobileDeleteBtn.addEventListener("click", openDeleteModal);

renameHeaderBtn.addEventListener("click", openRenameModal);
currentVideoTitle.addEventListener("click", openRenameModal);

refreshDestsBtn.addEventListener("click", () => {
  loadDestinations();
  showToast("Refreshed subfolders list", "info");
});

// Toggle Stream Mode
toggleStreamModeBtn.addEventListener("click", () => {
  const video = currentVideo();
  if (!video) return;
  loadVideoSource(video, state.streamMode !== "direct");
});

// Browser Modal Events
openFolderBrowserBtn.addEventListener("click", () => openBrowser());
emptyStateBrowseBtn.addEventListener("click", () => openBrowser());
closeBrowserModalBtn.addEventListener("click", closeBrowser);
cancelBrowserBtn.addEventListener("click", closeBrowser);

browserUpBtn.addEventListener("click", () => {
  const parent = browserUpBtn.dataset.parent;
  if (parent) browseDirectory(parent);
});
browserGoBtn.addEventListener("click", () => {
  browseDirectory(browserPathInput.value.trim());
});
browserPathInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") browseDirectory(browserPathInput.value.trim());
});
selectCurrentFolderBtn.addEventListener("click", async () => {
  await setSourceDirectory(browserCurrentPath);
  closeBrowser();
});

// Rename Modal Events
cancelRenameBtn.addEventListener("click", closeRenameModal);
confirmRenameBtn.addEventListener("click", submitRename);
renameInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") submitRename();
  if (e.key === "Escape") closeRenameModal();
});

// Delete Modal Events
cancelDeleteBtn.addEventListener("click", closeDeleteModal);
confirmDeleteBtn.addEventListener("click", submitDelete);

// Shortcuts Modal
shortcutsBtn.addEventListener("click", () => shortcutsModal.classList.remove("hidden"));
closeShortcutsBtn.addEventListener("click", () => shortcutsModal.classList.add("hidden"));

// Duplicate Alert Banner Events
if (toggleDuplicateCompareBtn) toggleDuplicateCompareBtn.addEventListener("click", toggleDuplicateCompare);
if (trashIncomingDuplicateBtn) trashIncomingDuplicateBtn.addEventListener("click", trashCurrentDuplicate);
if (dismissDuplicateBtn) dismissDuplicateBtn.addEventListener("click", hideDuplicateBanner);
if (autoTrashDuplicateToggle) {
  autoTrashDuplicateToggle.checked = !!state.autoTrashDuplicate;
  autoTrashDuplicateToggle.addEventListener("change", (e) => {
    state.autoTrashDuplicate = e.target.checked;
    localStorage.setItem("sorter_autotrash_duplicates", e.target.checked ? "true" : "false");
    if (state.autoTrashDuplicate && state.currentDuplicate && state.currentDuplicate.match_type === "content") {
      trashCurrentDuplicate();
    }
  });
}

// Global Keyboard Shortcuts
window.addEventListener("keydown", (e) => {
  const activeTag = document.activeElement ? document.activeElement.tagName : "";
  const isInput = activeTag === "INPUT" || activeTag === "TEXTAREA";

  if (e.key === "Escape") {
    closeBrowser();
    closeRenameModal();
    closeDeleteModal();
    shortcutsModal.classList.add("hidden");
    const themeModal = document.getElementById("themeCustomizerModal");
    if (themeModal && !themeModal.classList.contains("hidden")) {
      themeModal.classList.add("hidden");
      return;
    }
    if (transferDropdown) transferDropdown.classList.add("hidden");
    return;
  }

  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !isInput) {
    e.preventDefault();
    undoLastMove();
    return;
  }

  if (isInput) return;

  // Recent Destinations Shortcuts: 1..6 (No modifier keys)
  if (!e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
    if (e.key >= "1" && e.key <= "6") {
      const idx = parseInt(e.key, 10) - 1;
      if (idx >= 0 && idx < state.recentDestinations.length) {
        e.preventDefault();
        executeMove(state.recentDestinations[idx]);
        return;
      }
    }
  }

  switch (e.key.toLowerCase()) {
    case " ":
      e.preventDefault();
      if (videoPlayer.paused) {
        videoPlayer.play().catch(() => {});
      } else {
        videoPlayer.pause();
      }
      break;
    case "s":
    case "arrowright":
      e.preventDefault();
      skipVideo();
      break;
    case "p":
    case "arrowleft":
      e.preventDefault();
      prevVideo();
      break;
    case "u":
      e.preventDefault();
      undoLastMove();
      break;
    case "r":
      e.preventDefault();
      openRenameModal();
      break;
    case "d":
      e.preventDefault();
      openDeleteModal();
      break;
    case "x":
      if (state.currentDuplicate && duplicateBanner && !duplicateBanner.classList.contains("hidden")) {
        e.preventDefault();
        trashCurrentDuplicate();
      }
      break;
    case "/":
      e.preventDefault();
      folderSearchInput.focus();
      folderSearchInput.select();
      break;
    case "l":
      videoPlayer.loop = !videoPlayer.loop;
      showToast(videoPlayer.loop ? "Loop enabled" : "Loop disabled", "info", 1500);
      break;
    case "f":
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else {
        videoPlayer.requestFullscreen().catch(() => {});
      }
      break;
    case "t":
      e.preventDefault();
      const themeBtn = document.getElementById("themeModalBtn");
      if (themeBtn) themeBtn.click();
      break;
    case "?":
      shortcutsModal.classList.toggle("hidden");
      break;
  }
});

initThemeCustomizer();
setupSorterTouchSeek();
}
function initThemeCustomizer() {
const themeModalBtn = document.getElementById("themeModalBtn");
const themeModal = document.getElementById("themeCustomizerModal");
const closeThemeModalBtn = document.getElementById("closeThemeModalBtn");
const applyThemeModalCloseBtn = document.getElementById("applyThemeModalCloseBtn");
const resetThemeDefaultsBtn = document.getElementById("resetThemeDefaultsBtn");
const themeAccentPicker = document.getElementById("themeAccentPicker");
const themeBasePicker = document.getElementById("themeBasePicker");
const mobileDoubleTapSeekToggle = document.getElementById("mobileDoubleTapSeekToggle");

if (!themeModal) return;

function renderPickers() {
  if (!window.THEME_MANAGER) return;
  const cur = window.THEME_MANAGER.getCurrent();
  const accents = window.THEME_MANAGER.getAccents();
  const bases = window.THEME_MANAGER.getBases();

  if (themeAccentPicker) {
    themeAccentPicker.innerHTML = accents.map(acc => {
      const isSel = acc.key === cur.accent;
      return `
        <button type="button" data-accent="${acc.key}" class="theme-accent-btn group flex flex-col items-center gap-1.5 p-2 rounded-xl border transition cursor-pointer select-none ${isSel ? 'border-emerald-500 bg-emerald-500/10 shadow-sm' : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'}">
          <div class="relative w-7 h-7 sm:w-8 sm:h-8 rounded-full shadow-md flex items-center justify-center transition-transform group-hover:scale-110" style="background-color: ${acc.accent};">
            ${isSel ? '<span class="text-white text-xs font-bold drop-shadow">✓</span>' : ''}
          </div>
          <span class="text-[11px] font-medium ${isSel ? 'text-white font-semibold' : 'text-slate-400 group-hover:text-slate-300'}">${acc.name}</span>
        </button>
      `;
    }).join('');

    themeAccentPicker.querySelectorAll('.theme-accent-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const accKey = btn.dataset.accent;
        const currentBase = window.THEME_MANAGER.getCurrent().base;
        window.THEME_MANAGER.setTheme(accKey, currentBase);
        renderPickers();
        showToast(`🎨 Accent changed to ${accKey.charAt(0).toUpperCase() + accKey.slice(1)}`, 'success', 2000);
      });
    });
  }

  if (themeBasePicker) {
    themeBasePicker.innerHTML = bases.map(b => {
      const isSel = b.key === cur.base;
      return `
        <button type="button" data-base="${b.key}" class="theme-base-btn text-left p-3 rounded-xl border transition cursor-pointer select-none ${isSel ? 'border-emerald-500 bg-emerald-500/10 shadow-sm' : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'}">
          <div class="flex items-center justify-between mb-1">
            <span class="text-xs font-bold text-white flex items-center gap-1.5">
              <span>${b.emoji}</span>
              <span>${b.name}</span>
            </span>
            ${isSel ? '<span class="text-emerald-400 text-xs font-bold">✓</span>' : ''}
          </div>
          <p class="text-[11px] text-slate-400 leading-tight">${b.desc}</p>
        </button>
      `;
    }).join('');

    themeBasePicker.querySelectorAll('.theme-base-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const baseKey = btn.dataset.base;
        const currentAcc = window.THEME_MANAGER.getCurrent().accent;
        window.THEME_MANAGER.setTheme(currentAcc, baseKey);
        renderPickers();
        showToast(`🌌 Theme changed to ${btn.querySelector('span:last-child').textContent}`, 'success', 2000);
      });
    });
  }

  if (mobileDoubleTapSeekToggle) {
    mobileDoubleTapSeekToggle.checked = localStorage.getItem('lib_touch_double_tap_seek') !== 'false';
  }
}

function openModal() {
  renderPickers();
  document.body.classList.add("modal-scroll-lock");
  themeModal.classList.remove("hidden");
  themeModal.classList.add("flex");
}

function closeModal() {
  document.body.classList.remove("modal-scroll-lock");
  themeModal.classList.add("hidden");
  themeModal.classList.remove("flex");
}

if (themeModalBtn) themeModalBtn.addEventListener("click", openModal);
if (closeThemeModalBtn) closeThemeModalBtn.addEventListener("click", closeModal);
if (applyThemeModalCloseBtn) applyThemeModalCloseBtn.addEventListener("click", closeModal);
themeModal.addEventListener("click", (e) => {
  if (e.target === themeModal) closeModal();
});

if (resetThemeDefaultsBtn) {
  resetThemeDefaultsBtn.addEventListener("click", () => {
    if (window.THEME_MANAGER) window.THEME_MANAGER.setTheme('amber', 'stone');
    localStorage.setItem('lib_touch_double_tap_seek', 'true');
    if (mobileDoubleTapSeekToggle) mobileDoubleTapSeekToggle.checked = true;
    renderPickers();
    showToast('🎨 Theme reset to default', 'info', 2000);
  });
}

if (mobileDoubleTapSeekToggle) {
  mobileDoubleTapSeekToggle.addEventListener("change", (e) => {
    localStorage.setItem('lib_touch_double_tap_seek', e.target.checked ? 'true' : 'false');
    showToast(e.target.checked ? 'Double-tap seek enabled (±5s)' : 'Double-tap seek disabled', 'info', 2000);
  });
}
}

export { initThemeCustomizer, setupEventListeners };
