import { els } from './state.js';
import { icon } from '../shared/icons.js';

// The library binds a couple of dozen keyboard shortcuts but never told anyone
// about them. This module is the single source of truth for that reference: the
// `?` overlay renders from SHORTCUT_GROUPS, so a new binding only has to be
// described here to become discoverable.
export const SHORTCUT_GROUPS = [
  {
    title: 'Library',
    iconHtml: icon('layout-grid', 'w-3.5 h-3.5'),
    entries: [
      { keys: ['Ctrl', 'A'], description: 'Select / deselect every item in view' },
      { keys: ['Esc'], description: 'Close the open dialog, drawer or viewer' },
      { keys: ['T'], description: 'Open the theme & appearance customizer' },
      { keys: ['?'], description: 'Show this shortcut reference' },
    ],
  },
  {
    title: 'Viewer (lightbox)',
    iconHtml: icon('image', 'w-3.5 h-3.5'),
    entries: [
      { keys: ['←', '→'], description: 'Previous / next item' },
      { keys: ['Space'], description: 'Play or pause a video' },
      { keys: [','], description: 'Step back (5s playing, 0.05s paused)' },
      { keys: ['.'], description: 'Step forward (5s playing, 0.05s paused)' },
      { keys: ['[', ']'], description: 'Slower / faster playback' },
      { keys: ['M'], description: 'Mute or unmute' },
      { keys: ['L'], description: 'Toggle loop' },
      { keys: ['P'], description: 'Set the current frame as the poster' },
      { keys: ['S'], description: 'Convert this video to x265 / HEVC' },
      { keys: ['F'], description: 'Add or remove from favorites' },
      { keys: ['I'], description: 'Media info & EXIF details' },
      { keys: ['R'], description: 'Rename the file' },
      { keys: ['D'], description: 'Delete the file' },
      { keys: ['Esc'], description: 'Close the viewer' },
    ],
  },
  {
    title: 'Touch',
    iconHtml: icon('smartphone', 'w-3.5 h-3.5'),
    entries: [
      { keys: ['Double-tap', 'left / right'], description: 'Seek back / forward 5 seconds' },
      { keys: ['Swipe'], description: 'Move to the previous / next item' },
    ],
  },
];

function renderKeyCaps(keys) {
  return keys
    .map(
      (k) =>
        `<kbd class="px-1.5 py-0.5 rounded-md bg-slate-800 border border-slate-700 text-[10px] font-mono font-semibold text-slate-200 whitespace-nowrap">${k}</kbd>`
    )
    .join('<span class="text-slate-600 text-[10px]">+</span>');
}

export function renderShortcutsHelp() {
  const container = els.shortcutsGroups;
  if (!container) return;
  container.innerHTML = SHORTCUT_GROUPS.map(
    (group) => `
    <section class="flex flex-col gap-1.5">
      <h4 class="text-[11px] font-bold uppercase tracking-wide text-slate-400 flex items-center gap-1.5">
        ${group.iconHtml}<span>${group.title}</span>
      </h4>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-1">
        ${group.entries
          .map(
            (e) => `
          <div class="flex items-center justify-between gap-2 px-2 py-1.5 rounded-lg bg-slate-950/60 border border-slate-800/80">
            <span class="text-[11px] text-slate-300 min-w-0">${e.description}</span>
            <span class="flex items-center gap-0.5 shrink-0">${renderKeyCaps(e.keys)}</span>
          </div>`
          )
          .join('')}
      </div>
    </section>`
  ).join('');
}

export function openShortcutsHelp() {
  const modal = els.shortcutsModal;
  if (!modal) return;
  renderShortcutsHelp();
  modal.classList.remove('hidden');
  modal.classList.add('flex');
}

export function closeShortcutsHelp() {
  const modal = els.shortcutsModal;
  if (!modal) return;
  modal.classList.add('hidden');
  modal.classList.remove('flex');
}

export function isShortcutsHelpOpen() {
  const modal = els.shortcutsModal;
  return Boolean(modal && !modal.classList.contains('hidden'));
}

export function toggleShortcutsHelp() {
  if (isShortcutsHelpOpen()) closeShortcutsHelp();
  else openShortcutsHelp();
}

export function initShortcutsHelp() {
  const modal = els.shortcutsModal;
  if (!modal) return;

  if (els.closeShortcutsBtn) els.closeShortcutsBtn.addEventListener('click', closeShortcutsHelp);
  if (els.closeShortcutsFooterBtn) els.closeShortcutsFooterBtn.addEventListener('click', closeShortcutsHelp);
  if (els.shortcutsHelpBtn) els.shortcutsHelpBtn.addEventListener('click', openShortcutsHelp);
  if (els.mobileShortcutsBtn) els.mobileShortcutsBtn.addEventListener('click', openShortcutsHelp);

  // Click the dimmed backdrop (not the card) to dismiss.
  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeShortcutsHelp();
  });

  renderShortcutsHelp();
}
