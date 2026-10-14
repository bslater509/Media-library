import "../../../static/styles.css";
import { hydrateIcons } from '../shared/icons.js';
import { setSourceDirectory } from './browser.js';
import { loadConfig, loadDestinations, refreshQueue, updateUI } from './config.js';
import { setupEventListeners } from './events.js';
import { startBackgroundIndexPolling, startDuplicateHashPolling, startThumbnailPolling, startTransfersPolling, startWebpPolling } from './polling.js';
import { loadLocalRecents, state } from './state.js';
import { setupVideoControlsManager, setupVideoGestures } from './video.js';

// Initialize Application
document.addEventListener("DOMContentLoaded", async () => {
  // Static template markup carries icon placeholders; swap in the shared
  // inline SVG set before anything renders.
  hydrateIcons(document);
  setupEventListeners();
  setupVideoGestures();
  setupVideoControlsManager();
  loadLocalRecents();
  await loadConfig();
  await loadDestinations();

  // If source directory is not set, try saved preference
  if (!state.sourceDir) {
    const savedSource = localStorage.getItem("video_sorter_source");
    if (savedSource) {
      await setSourceDirectory(savedSource, false);
    }
  }

  if (state.sourceDir) {
    await refreshQueue();
  } else {
    updateUI();
  }

  // Start background transfers, thumbnails, & duplicate hash polling
  startTransfersPolling();
  startThumbnailPolling();
  startDuplicateHashPolling();
  startWebpPolling();
  startBackgroundIndexPolling();
});

