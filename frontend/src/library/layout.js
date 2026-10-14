import { TILE_SCALES, TILE_SCALE_KEY, TILE_SCALE_LABELS, els, state } from './state.js';
import { renderSubfolders } from './folders.js';
import { renderCards, updateCardsTranscodeStatus } from './gallery.js';

// Justified grid layout math and thumbnail scale presets.
// Extracted from the former monolithic library index.js.

const MIN_TILE_RATIO = 2 / 3;
const MAX_TILE_RATIO = 3 / 2;
function tileRatio(width, height) {
  const w = Number(width);
  const h = Number(height);
  if (!w || !h || w <= 0 || h <= 0) return 1;
  let ratio = w / h;
  if (ratio < MIN_TILE_RATIO) ratio = MIN_TILE_RATIO;
  if (ratio > MAX_TILE_RATIO) ratio = MAX_TILE_RATIO;
  return ratio;
}
// Responsive density used only to derive the justified target row height.
function mediaColumnCount() {
  const w = window.innerWidth;
  if (w < 640) return 2;
  if (w < 768) return 3;
  if (w < 1024) return 4;
  if (w < 1280) return 5;
  return 6;
}
function subfolderColumnCount() {
  const w = window.innerWidth;
  if (w < 640) return 2;
  if (w < 768) return 3;
  if (w < 1024) return 4;
  return 5;
}
function gridGapPx() {
  return window.innerWidth >= 640 ? 16 : 10;
}
function gridWidth() {
  const w = els.mediaGrid ? els.mediaGrid.clientWidth : 0;
  return w > 0 ? w : Math.max(320, window.innerWidth - 32);
}
const MIN_ROW_HEIGHT = 96;
const MAX_ROW_HEIGHT = 640;
function targetRowHeight(width, gap, columns, scale) {
  const base = (width - gap * (columns - 1)) / columns;
  const h = base * (scale || 1);
  return Math.min(MAX_ROW_HEIGHT, Math.max(MIN_ROW_HEIGHT, h));
}
// Row-ordered "justified rows": tiles keep their natural (clamped) aspect
// ratio, but every tile in a row shares one row height so the row fills the
// container width exactly. Reading order therefore runs strictly left to
// right, then down to the next row. A trailing partial row keeps the target
// height and is left-aligned until more items arrive.
// `measurer(item)` returns the tile's aspect ratio.
function layoutJustifiedRows(items, width, gap, targetHeight, measurer) {
  const rows = [];
  if (!items.length) return rows;
  let current = [];
  let ratios = [];
  let sumRatio = 0;
  const flush = (height, partial) => {
    rows.push({ items: current, ratios, height, partial });
    current = [];
    ratios = [];
    sumRatio = 0;
  };
  for (const item of items) {
    const ratio = measurer(item) || 1;
    current.push(item);
    ratios.push(ratio);
    sumRatio += ratio;
    if (width > 0) {
      const rowHeight = (width - gap * (current.length - 1)) / sumRatio;
      if (rowHeight <= targetHeight) flush(rowHeight, false);
    }
  }
  if (current.length) flush(targetHeight, true);
  return rows;
}
let _lastMediaColumnCount = mediaColumnCount();
let _lastMediaGridWidth = gridWidth();
function relayoutMediaGrid() {
  const n = mediaColumnCount();
  const width = gridWidth();
  if (n === _lastMediaColumnCount && width === _lastMediaGridWidth) return;
  _lastMediaColumnCount = n;
  _lastMediaGridWidth = width;
  if (!state.filteredFiles || state.filteredFiles.length === 0) return;
  const count = Math.max(state.displayedCount, Math.min(state.filteredFiles.length, 50));
  state._gridPendingRowEl = null;
  state._gridPendingStart = 0;
  renderCards(0, count);
  if (state.latestTranscodeData) updateCardsTranscodeStatus(state.latestTranscodeData);
}
function relayoutSubfolders() {
  if (state.subfolders && state.subfolders.length > 0) renderSubfolders();
}
// Reflect the current thumbnail-size preset on the +/- control and mobile sheet.
function syncTileScaleUI() {
  const idx = Math.max(0, TILE_SCALES.indexOf(state.tileScale));
  if (els.tileSizeLabel) els.tileSizeLabel.textContent = TILE_SCALE_LABELS[idx] || 'M';
  if (els.tileSizeDownBtn) {
    els.tileSizeDownBtn.disabled = idx === 0;
    els.tileSizeDownBtn.classList.toggle('opacity-30', idx === 0);
    els.tileSizeDownBtn.classList.toggle('pointer-events-none', idx === 0);
  }
  if (els.tileSizeUpBtn) {
    els.tileSizeUpBtn.disabled = idx === TILE_SCALES.length - 1;
    els.tileSizeUpBtn.classList.toggle('opacity-30', idx === TILE_SCALES.length - 1);
    els.tileSizeUpBtn.classList.toggle('pointer-events-none', idx === TILE_SCALES.length - 1);
  }
  if (els.mobileTileSizeValue) {
    els.mobileTileSizeValue.textContent = TILE_SCALE_LABELS[idx] || 'M';
  }
  const mobileScaleBtns = [
    els.mobileTileSizeBtnXS,
    els.mobileTileSizeBtnS,
    els.mobileTileSizeBtnM,
    els.mobileTileSizeBtnL,
    els.mobileTileSizeBtnXL
  ];
  mobileScaleBtns.forEach((btn, bIdx) => {
    if (!btn) return;
    if (bIdx === idx) {
      btn.className = 'py-2 rounded-lg font-mono font-semibold transition bg-emerald-600 text-white shadow-sm';
    } else {
      btn.className = 'py-2 rounded-lg font-mono font-semibold transition text-slate-400 hover:text-white';
    }
  });
}
// Apply a new thumbnail-size preset: persist it and re-flow the current view.
function applyTileScale() {
  try { localStorage.setItem(TILE_SCALE_KEY, String(state.tileScale)); } catch (e) { /* ignore */ }
  syncTileScaleUI();
  if (state.filteredFiles && state.filteredFiles.length > 0) {
    state._gridPendingRowEl = null;
    state._gridPendingStart = 0;
    renderCards(0, state.displayedCount);
    if (state.latestTranscodeData) updateCardsTranscodeStatus(state.latestTranscodeData);
  }
  relayoutSubfolders();
}
function stepTileScale(dir) {
  const idx = TILE_SCALES.indexOf(state.tileScale);
  const next = Math.min(TILE_SCALES.length - 1, Math.max(0, (idx < 0 ? 2 : idx) + dir));
  if (TILE_SCALES[next] === state.tileScale) return;
  state.tileScale = TILE_SCALES[next];
  applyTileScale();
}

export { MAX_ROW_HEIGHT, MAX_TILE_RATIO, MIN_ROW_HEIGHT, MIN_TILE_RATIO, _lastMediaColumnCount, _lastMediaGridWidth, applyTileScale, gridGapPx, gridWidth, layoutJustifiedRows, mediaColumnCount, relayoutMediaGrid, relayoutSubfolders, stepTileScale, subfolderColumnCount, syncTileScaleUI, targetRowHeight, tileRatio };
