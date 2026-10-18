/**
 * A drill-down folder chooser over the library index.
 *
 * Self-contained: it renders its own modal — the same full-height sheet on
 * phones that the other sheets use — so a page only needs a trigger button and
 * a callback. Folders come from `GET /api/backup/folders`, which is index-only,
 * so opening it never walks the (possibly FUSE) mount, and ZIP archives are
 * filtered out: an archive is not a place to put files or scan.
 *
 * Usage:
 *   const picker = createFolderPicker({
 *     title: 'Scan a folder',
 *     confirmLabel: 'Scan this folder',
 *     rowIcon: icon('folder', 'w-3.5 h-3.5 shrink-0 text-emerald-400'),
 *     onSelect: (path) => scan(path),
 *   });
 *   button.addEventListener('click', () => picker.open(currentValue));
 *
 * ``rowIcon`` is markup the caller supplies (the shared icon registry), so this
 * module does not drag the whole icon set into a bundle that needs no others.
 */

import { escapeHtml } from '../library/utils.js';

const ZIP_RE = /\.zip$/i;

export function createFolderPicker({
  title = 'Choose a folder',
  subtitle = '',
  confirmLabel = 'Use this folder',
  rowIcon = '',
  onSelect = null,
} = {}) {
  let modal = null;
  let pathEl = null;
  let listEl = null;
  let upBtn = null;
  let confirmBtn = null;
  let current = '';
  let isRoot = true;
  let loading = false;

  function render() {
    if (!pathEl) return;
    pathEl.textContent = current || '/';
    pathEl.title = current || '/';
    upBtn.disabled = isRoot;
    upBtn.className = isRoot
      ? 'px-2 py-1 rounded-lg bg-slate-900 text-slate-600 border border-slate-800 text-[11px] font-semibold shrink-0 cursor-not-allowed'
      : 'px-2 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-[11px] font-semibold shrink-0 transition';
    confirmBtn.disabled = loading || !current;
  }

  function renderFolders(subfolders) {
    const dirs = (subfolders || []).filter(
      (s) => s && !(s.is_zip || ZIP_RE.test(s.path || ''))
    );
    if (!dirs.length) {
      listEl.innerHTML =
        '<div class="px-2 py-6 text-center text-xs text-slate-500">No subfolders here — use this one, or go up.</div>';
      return;
    }
    listEl.innerHTML = dirs
      .map(
        (s) => `
      <button type="button" data-picker-dir="${escapeHtml(s.path)}" class="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs text-slate-200 hover:bg-slate-800 text-left transition min-w-0">
        ${rowIcon}
        <span class="truncate flex-1">${escapeHtml(s.name || s.path)}</span>
        <span class="text-slate-500 shrink-0">›</span>
      </button>`
      )
      .join('');
    listEl.querySelectorAll('[data-picker-dir]').forEach((btn) => {
      btn.addEventListener('click', () => load(btn.dataset.pickerDir));
    });
  }

  async function load(path) {
    loading = true;
    render();
    listEl.innerHTML =
      '<div class="px-2 py-6 text-center text-xs text-slate-500">Loading…</div>';
    try {
      const res = await fetch(
        `/api/backup/folders?path=${encodeURIComponent(path || '')}`
      );
      const data = await res.json();
      current = data.current_path || path || '';
      isRoot = Boolean(data.is_root);
      loading = false;
      render();
      renderFolders(data.subfolders);
    } catch (err) {
      loading = false;
      render();
      listEl.innerHTML =
        '<div class="px-2 py-6 text-center text-xs text-amber-300">Could not read the folder list.</div>';
    }
  }

  function close() {
    if (!modal) return;
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    document.removeEventListener('keydown', onKeydown);
  }

  function onKeydown(e) {
    if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) {
      e.stopPropagation();
      close();
    }
  }

  function ensure() {
    if (modal) return;
    modal = document.createElement('div');
    modal.className =
      'ml-sheet fixed inset-0 bg-slate-950/85 backdrop-blur-sm z-[96] hidden items-center justify-center p-3 sm:p-6';
    modal.innerHTML = `
      <div class="ml-sheet-card bg-slate-900 border border-slate-700/80 rounded-2xl w-full max-w-lg max-h-[88vh] flex flex-col shadow-2xl overflow-hidden">
        <div class="ml-sheet-grabber" data-sheet-grabber></div>
        <div class="px-4 py-3 bg-slate-950/80 border-b border-slate-800 flex items-center justify-between gap-3 shrink-0">
          <div class="min-w-0">
            <h3 class="text-xs sm:text-sm font-bold text-white truncate">${escapeHtml(title)}</h3>
            ${
              subtitle
                ? `<p class="text-[10px] text-slate-400 font-mono truncate">${escapeHtml(subtitle)}</p>`
                : ''
            }
          </div>
          <button type="button" data-picker-close class="w-8 h-8 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white flex items-center justify-center text-sm transition shrink-0" title="Close">✕</button>
        </div>
        <div class="px-3 pt-3 flex items-center gap-1.5 shrink-0">
          <button type="button" data-picker-up class="px-2 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-[11px] font-semibold shrink-0 transition">⬆ Up</button>
          <span data-picker-path class="flex-1 truncate text-[11px] font-mono text-slate-300 bg-slate-950 px-2 py-1 rounded-lg border border-slate-800">/</span>
        </div>
        <div data-picker-list class="flex-1 min-h-0 overflow-y-auto px-3 py-3 flex flex-col gap-1"></div>
        <div class="px-4 py-3 bg-slate-950/80 border-t border-slate-800 flex items-center justify-end gap-2 shrink-0" style="padding-bottom: max(0.75rem, var(--sab, env(safe-area-inset-bottom, 0px)));">
          <button type="button" data-picker-cancel class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-[11px] font-semibold transition">Cancel</button>
          <button type="button" data-picker-confirm class="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] font-semibold transition disabled:opacity-40 disabled:pointer-events-none">${escapeHtml(confirmLabel)}</button>
        </div>
      </div>`;
    document.body.appendChild(modal);

    pathEl = modal.querySelector('[data-picker-path]');
    listEl = modal.querySelector('[data-picker-list]');
    upBtn = modal.querySelector('[data-picker-up]');
    confirmBtn = modal.querySelector('[data-picker-confirm]');

    modal.querySelector('[data-picker-close]').addEventListener('click', close);
    modal.querySelector('[data-picker-cancel]').addEventListener('click', close);
    modal.addEventListener('click', (e) => {
      if (e.target === modal) close();
    });
    upBtn.addEventListener('click', () => {
      if (isRoot || !current) return;
      load(current.substring(0, current.lastIndexOf('/')) || '');
    });
    confirmBtn.addEventListener('click', () => {
      const chosen = current;
      close();
      if (chosen && onSelect) onSelect(chosen);
    });
  }

  return {
    open(startPath = '') {
      ensure();
      modal.classList.remove('hidden');
      modal.classList.add('flex');
      document.addEventListener('keydown', onKeydown);
      load(startPath || '');
    },
    close,
  };
}
