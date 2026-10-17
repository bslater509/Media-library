// Concatenated raw sources of the Library and Sorter bundles.
//
// Both entrypoints were split into feature modules, so tests that assert on
// bundle behaviour read the whole bundle as one string. The entrypoint comes
// first so same-file proximity checks keep working.

import libraryIndex from '../src/library/index.js?raw';
import libraryLayout from '../src/library/layout.js?raw';
import libraryAnimations from '../src/library/animations.js?raw';
import libraryFolders from '../src/library/folders.js?raw';
import librarySearch from '../src/library/search.js?raw';
import libraryGallery from '../src/library/gallery.js?raw';
import libraryLightbox from '../src/library/lightbox.js?raw';
import libraryThumbnailChooser from '../src/library/thumbnail_chooser.js?raw';
import libraryManagement from '../src/library/management.js?raw';
import libraryDuplicates from '../src/library/duplicates.js?raw';
import libraryTasks from '../src/library/tasks.js?raw';
import libraryWorkers from '../src/library/workers.js?raw';
import libraryZip from '../src/library/zip.js?raw';
import libraryTheme from '../src/library/theme.js?raw';
import librarySheet from '../src/library/sheet.js?raw';
import libraryShortcuts from '../src/library/shortcuts.js?raw';
import libraryUploads from '../src/library/uploads.js?raw';

import sorterIndex from '../src/app/index.js?raw';
import sorterElements from '../src/app/elements.js?raw';
import sorterState from '../src/app/state.js?raw';
import sorterConfig from '../src/app/config.js?raw';
import sorterFolders from '../src/app/folders.js?raw';
import sorterVideo from '../src/app/video.js?raw';
import sorterDuplicates from '../src/app/duplicates.js?raw';
import sorterMoves from '../src/app/moves.js?raw';
import sorterPolling from '../src/app/polling.js?raw';
import sorterEmptyFolders from '../src/app/empty_folders.js?raw';
import sorterBrowser from '../src/app/browser.js?raw';
import sorterModals from '../src/app/modals.js?raw';
import sorterEvents from '../src/app/events.js?raw';

export const librarySource = [
  libraryIndex, libraryLayout, libraryAnimations, libraryFolders, librarySearch,
  libraryGallery, libraryLightbox, libraryThumbnailChooser, libraryManagement,
  libraryDuplicates, libraryTasks, libraryWorkers, libraryZip, libraryTheme,
  librarySheet, libraryShortcuts, libraryUploads,
].join('\n');

export const sorterSource = [
  sorterIndex, sorterElements, sorterState, sorterConfig, sorterFolders,
  sorterVideo, sorterDuplicates, sorterMoves, sorterPolling, sorterEmptyFolders,
  sorterBrowser, sorterModals, sorterEvents,
].join('\n');
