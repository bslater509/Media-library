import { currentVideo, updateUI } from './config.js';
import { compareExistingName, compareExistingSize, compareExistingVideo, compareIncomingName, compareIncomingSize, duplicateBadgeType, duplicateBanner, duplicateCompareContainer, duplicateCompareText, duplicateMatchInfo } from './elements.js';
import { escapeHtml, showToast, state } from './state.js';

// Duplicate detection banner and compare/trash actions.
// Split out of the former monolithic sorter index.js.

let duplicateCheckAbortCtrl = null;
async function checkCurrentVideoDuplicate(video) {
if (duplicateCheckAbortCtrl) {
  duplicateCheckAbortCtrl.abort();
  duplicateCheckAbortCtrl = null;
}
if (!video || !duplicateBanner) return;
const videoId = video.id;

duplicateCheckAbortCtrl = new AbortController();
try {
  const res = await fetch(`/api/sorter/check-duplicate?file_id=${encodeURIComponent(videoId)}`, {
    signal: duplicateCheckAbortCtrl.signal
  });
  if (!res.ok) return;
  const data = await res.json();

  // Ensure we are still showing the same video
  const curr = currentVideo();
  if (!curr || curr.id !== videoId) return;

  if (data.is_duplicate && data.matches && data.matches.length > 0) {
    state.currentDuplicate = data;
    showDuplicateBanner(data, curr);
  } else {
    state.currentDuplicate = null;
    hideDuplicateBanner();
  }
} catch (err) {
  if (err.name === "AbortError") return;
  console.error("Error checking duplicate:", err);
} finally {
  if (duplicateCheckAbortCtrl && duplicateCheckAbortCtrl.signal.aborted) {
    duplicateCheckAbortCtrl = null;
  }
}
}
function showDuplicateBanner(data, incomingVideo) {
if (!duplicateBanner) return;
const match = data.matches[0];
if (!match) return;

// Auto-trash incoming video if autoTrashDuplicate is enabled and content is exact 100% match
if (state.autoTrashDuplicate && data.match_type === "content") {
  showToast(`⚡ 100% duplicate detected in Backup v2 — auto-trashing incoming video`, "warning", 3000);
  trashCurrentDuplicate();
  return;
}

if (duplicateBadgeType) {
  if (data.match_type === "content") {
    duplicateBadgeType.textContent = "Exact Content Match";
    duplicateBadgeType.className = "text-[10px] font-mono px-1.5 py-0.2 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30";
  } else {
    duplicateBadgeType.textContent = "Same Filename";
    duplicateBadgeType.className = "text-[10px] font-mono px-1.5 py-0.2 rounded bg-sky-500/20 text-sky-300 border border-sky-500/30";
  }
}

if (duplicateMatchInfo) {
  duplicateMatchInfo.textContent = `Existing in: 📁 ${match.folder_display} / ${match.name} (${match.size_formatted})`;
  duplicateMatchInfo.title = match.path;
}

// Populate compare previews
if (compareIncomingName) compareIncomingName.textContent = incomingVideo.filename;
if (compareIncomingSize) compareIncomingSize.textContent = incomingVideo.size_formatted;
if (compareExistingName) compareExistingName.textContent = match.name;
if (compareExistingSize) compareExistingSize.textContent = match.size_formatted;
if (compareExistingVideo) {
  compareExistingVideo.src = match.media_url;
  if (match.thumb_url) compareExistingVideo.poster = match.thumb_url;
}

duplicateBanner.classList.remove("hidden");
}
function hideDuplicateBanner() {
if (!duplicateBanner) return;
duplicateBanner.classList.add("hidden");
if (duplicateCompareContainer) duplicateCompareContainer.classList.add("hidden");
if (compareExistingVideo) {
  try { compareExistingVideo.pause(); } catch (e) {}
  compareExistingVideo.removeAttribute("src");
  compareExistingVideo.load();
}
state.isComparingDuplicate = false;
if (duplicateCompareText) duplicateCompareText.textContent = "Compare";
}
function toggleDuplicateCompare() {
if (!duplicateCompareContainer) return;
state.isComparingDuplicate = !state.isComparingDuplicate;
if (state.isComparingDuplicate) {
  duplicateCompareContainer.classList.remove("hidden");
  if (duplicateCompareText) duplicateCompareText.textContent = "Hide Compare";
  if (compareExistingVideo && compareExistingVideo.src) {
    compareExistingVideo.load();
    compareExistingVideo.play().catch(() => {});
  }
} else {
  duplicateCompareContainer.classList.add("hidden");
  if (duplicateCompareText) duplicateCompareText.textContent = "Compare";
  if (compareExistingVideo) {
    try { compareExistingVideo.pause(); } catch (e) {}
  }
}
}
async function trashCurrentDuplicate() {
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

  showToast(`Deleted duplicate "${escapeHtml(video.filename)}"`, "warning");
  hideDuplicateBanner();

  state.queue = state.queue.filter((v) => v.id !== video.id);
  if (state.currentIndex >= state.queue.length) {
    state.currentIndex = Math.max(0, state.queue.length - 1);
  }
  updateUI();
} catch (err) {
  showToast(`Delete error: ${err.message}`, "error");
}
}

export { checkCurrentVideoDuplicate, duplicateCheckAbortCtrl, hideDuplicateBanner, showDuplicateBanner, toggleDuplicateCompare, trashCurrentDuplicate };
