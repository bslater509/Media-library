// /analytics — index-only library dashboard.
//
// Renders storage, folder, quality and housekeeping rollups computed by
// GET /api/analytics/overview (SQLite index only — no disk walking, no remote
// stats). Charts come from a tree-shaken ECharts build (treemap / donut / bars)
// and follow the live theme accent, rebuilt on the `themechange` event. The page
// is read-only: every card links into the Library panel that does the work.

import '../../../static/styles.css';
import * as echarts from 'echarts/core';
import { BarChart, PieChart, TreemapChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { LegacyGridContainLabel } from 'echarts/features';
import { CanvasRenderer } from 'echarts/renderers';
import { escapeHtml, formatBytes, formatDuration, getResolutionLabel, showToast } from '../library/utils.js';

echarts.use([
  BarChart,
  PieChart,
  TreemapChart,
  GridComponent,
  TooltipComponent,
  // ECharts 6 moved `grid.containLabel` behind this feature; without it the
  // option is ignored and the category labels get clipped.
  LegacyGridContainLabel,
  CanvasRenderer,
]);

const els = {};
let charts = {};
let currentData = null;

function byId(id) {
  return document.getElementById(id);
}

function fmtInt(n) {
  return Number(n || 0).toLocaleString();
}

function cacheEls() {
  els.mobileSubtitle = byId('anMobileSubtitle');
  els.loading = byId('anLoading');
  els.error = byId('anError');
  els.body = byId('anBody');
  els.kpis = byId('anKpis');
  els.treemap = byId('anTreemap');
  els.treemapTotal = byId('anTreemapTotal');
  els.treemapZoomIn = byId('anTreemapZoomIn');
  els.treemapZoomOut = byId('anTreemapZoomOut');
  els.treemapReset = byId('anTreemapReset');
  els.typeChart = byId('anTypeChart');
  els.typeLegend = byId('anTypeLegend');
  els.extChart = byId('anExtChart');
  els.resChart = byId('anResChart');
  els.qualityNotes = byId('anQualityNotes');
  els.folderRows = byId('anFolderRows');
  els.fileRows = byId('anFileRows');
  els.filesCount = byId('anFilesCount');
  els.reclaim = byId('anReclaim');
  els.healthPills = byId('anHealthPills');
  els.generatedAt = byId('anGeneratedAt');
  els.refreshBtn = byId('anRefreshBtn');
  els.refreshSpinner = byId('anRefreshSpinner');
  els.previewModal = byId('anPreviewModal');
  els.previewName = byId('anPreviewName');
  els.previewPath = byId('anPreviewPath');
  els.previewMeta = byId('anPreviewMeta');
  els.previewOpenBtn = byId('anPreviewOpenBtn');
  els.previewVideo = byId('anPreviewVideo');
  els.previewImage = byId('anPreviewImage');
  els.previewCloseBtn = byId('anPreviewCloseBtn');
}

// ------------------------------------------------------------------ theme

// Chart chrome (axis/label colors, block palette) derives from the live theme
// tokens in theme_head.html, so the dashboard matches the accent + surface mode
// the user picked — including OLED black. Rebuilt on `themechange`.
function themeColors() {
  const manager = window.THEME_MANAGER;
  const cur = (manager && manager.getCurrent && manager.getCurrent()) || { accent: 'amber', base: 'stone' };
  const tokens = ((manager && manager.ACCENTS && manager.ACCENTS[cur.accent]) || {}).tokens || {};
  const surfaces = ((manager && manager.BASES && manager.BASES[cur.base]) || {}).surfaces || {};
  const pick = (map, key, fallback) => map[key] || fallback;
  return {
    text: pick(surfaces, 200, '#e7e5e4'),
    muted: pick(surfaces, 400, '#a8a29e'),
    border: pick(surfaces, 800, '#292524'),
    surface: pick(surfaces, 950, '#0c0a09'),
    accent: pick(tokens, 500, '#f59e0b'),
    palette: [
      pick(tokens, 500, '#f59e0b'),
      pick(tokens, 300, '#fcd34d'),
      pick(tokens, 700, '#b45309'),
      pick(tokens, 400, '#fbbf24'),
      pick(tokens, 600, '#d97706'),
      pick(tokens, 200, '#fde68a'),
    ],
  };
}

function initThemeCustomizer() {
  const modal = byId('themeCustomizerModal');
  const triggers = Array.from(document.querySelectorAll('[data-theme-modal-btn]'));
  const closeBtn = byId('closeThemeModalBtn');
  const applyBtn = byId('applyThemeModalCloseBtn');
  const resetBtn = byId('resetThemeDefaultsBtn');
  const accentPicker = byId('themeAccentPicker');
  const basePicker = byId('themeBasePicker');
  const seekToggle = byId('mobileDoubleTapSeekToggle');
  if (!modal || !window.THEME_MANAGER) return;

  function renderPickers() {
    const cur = window.THEME_MANAGER.getCurrent();
    if (accentPicker) {
      accentPicker.innerHTML = window.THEME_MANAGER.getAccents().map((acc) => {
        const sel = acc.key === cur.accent;
        return `
          <button type="button" data-accent="${acc.key}" class="theme-accent-btn group flex flex-col items-center gap-1.5 p-2 rounded-xl border transition cursor-pointer select-none ${sel ? 'border-emerald-500 bg-emerald-500/10 shadow-sm' : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'}">
            <div class="relative w-7 h-7 sm:w-8 sm:h-8 rounded-full shadow-md flex items-center justify-center transition-transform group-hover:scale-110" style="background-color: ${acc.accent};">${sel ? '<span class="text-white text-xs font-bold drop-shadow">✓</span>' : ''}</div>
            <span class="text-[11px] font-medium ${sel ? 'text-white font-semibold' : 'text-slate-400 group-hover:text-slate-300'}">${acc.name}</span>
          </button>`;
      }).join('');
      accentPicker.querySelectorAll('.theme-accent-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          window.THEME_MANAGER.setTheme(btn.dataset.accent, window.THEME_MANAGER.getCurrent().base);
          renderPickers();
          showToast(`🎨 Accent changed to ${btn.dataset.accent}`, 'success');
        });
      });
    }
    if (basePicker) {
      basePicker.innerHTML = window.THEME_MANAGER.getBases().map((b) => {
        const sel = b.key === cur.base;
        return `
          <button type="button" data-base="${b.key}" class="theme-base-btn text-left p-3 rounded-xl border transition cursor-pointer select-none ${sel ? 'border-emerald-500 bg-emerald-500/10 shadow-sm' : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'}">
            <div class="flex items-center justify-between mb-1">
              <span class="text-xs font-bold text-white flex items-center gap-1.5"><span>${b.emoji}</span><span>${b.name}</span></span>
              ${sel ? '<span class="text-emerald-400 text-xs font-bold">✓</span>' : ''}
            </div>
            <p class="text-[11px] text-slate-400 leading-tight">${b.desc}</p>
          </button>`;
      }).join('');
      basePicker.querySelectorAll('.theme-base-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          window.THEME_MANAGER.setTheme(window.THEME_MANAGER.getCurrent().accent, btn.dataset.base);
          renderPickers();
          showToast(`🌌 Theme changed to ${btn.dataset.base}`, 'success');
        });
      });
    }
  }

  function openModal() {
    renderPickers();
    document.body.classList.add('modal-scroll-lock');
    modal.classList.remove('hidden');
    modal.classList.add('flex');
  }
  function closeModal() {
    document.body.classList.remove('modal-scroll-lock');
    modal.classList.add('hidden');
    modal.classList.remove('flex');
  }

  triggers.forEach((btn) => btn.addEventListener('click', openModal));
  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  if (applyBtn) applyBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      window.THEME_MANAGER.setTheme('amber', 'stone');
      localStorage.setItem('lib_touch_double_tap_seek', 'true');
      if (seekToggle) seekToggle.checked = true;
      renderPickers();
      showToast('🎨 Theme reset to default', 'info');
    });
  }
  if (seekToggle) {
    seekToggle.checked = localStorage.getItem('lib_touch_double_tap_seek') !== 'false';
    seekToggle.addEventListener('change', (e) => {
      localStorage.setItem('lib_touch_double_tap_seek', e.target.checked ? 'true' : 'false');
      showToast(e.target.checked ? 'Double-tap seek enabled (±5s)' : 'Double-tap seek disabled', 'info');
    });
  }
}

// ------------------------------------------------------------- kpis/tables

function renderKpis(data) {
  if (!els.kpis) return;
  const t = data.totals;
  const q = data.quality;
  const tiles = [
    { label: 'Total bytes', value: t.bytes_formatted, sub: `${fmtInt(t.files)} indexed files` },
    { label: 'Files', value: fmtInt(t.files), sub: `${fmtInt(t.videos)} video · ${fmtInt(t.images)} image` },
    { label: 'Videos', value: fmtInt(t.videos), sub: t.video_bytes_formatted },
    { label: 'Images', value: fmtInt(t.images), sub: t.image_bytes_formatted },
    { label: 'Folders', value: fmtInt(t.folders), sub: t.zip_folders ? `${fmtInt(t.zip_folders)} ZIP archive${t.zip_folders === 1 ? '' : 's'}` : 'no ZIP archives' },
    { label: 'Total runtime', value: q.total_duration_formatted, sub: `${fmtInt(q.high_fps_count)} clips at ≥${q.fps_threshold}fps` },
  ];
  els.kpis.innerHTML = tiles.map((k) => `
    <div class="bg-slate-900/50 backdrop-blur-md border border-slate-800/70 rounded-2xl px-3.5 py-3 min-w-0">
      <div class="text-lg sm:text-xl font-bold text-white tracking-tight truncate" title="${escapeHtml(k.value)}">${escapeHtml(k.value)}</div>
      <div class="text-[10px] uppercase tracking-wider text-slate-400 mt-0.5">${escapeHtml(k.label)}</div>
      <div class="text-[10px] text-slate-500 font-mono truncate mt-1" title="${escapeHtml(k.sub)}">${escapeHtml(k.sub)}</div>
    </div>
  `).join('');
}

function renderFolders(data) {
  if (!els.folderRows) return;
  const rows = data.folders.largest || [];
  if (!rows.length) {
    els.folderRows.innerHTML = '<tr><td colspan="5" class="py-6 text-center text-[11px] text-slate-500">No indexed folders yet.</td></tr>';
    return;
  }
  els.folderRows.innerHTML = rows.map((f) => {
    const pct = Math.max(1, Number(f.percent_bytes || 0));
    const link = `/?folder=${encodeURIComponent(f.path)}`;
    return `
      <tr class="border-b border-slate-800/60 hover:bg-slate-800/30 transition">
        <td class="py-2 pr-3 min-w-0">
          <div class="font-mono text-slate-200 truncate max-w-[22rem]" title="${escapeHtml(f.path)}">${escapeHtml(f.name)}${f.is_zip ? '<span class="ml-1.5 text-[9px] px-1 py-[1px] rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">ZIP</span>' : ''}</div>
          <div class="text-[10px] text-slate-500 font-mono truncate max-w-[22rem]" title="${escapeHtml(f.path)}">${escapeHtml(f.rel_path)}</div>
        </td>
        <td class="py-2 pr-3 text-right font-mono text-slate-200 whitespace-nowrap">${escapeHtml(f.bytes_formatted)}</td>
        <td class="py-2 pr-3 text-right font-mono text-slate-400 whitespace-nowrap">${fmtInt(f.files)}</td>
        <td class="py-2 pr-3 hidden sm:table-cell">
          <div class="h-1.5 rounded-full bg-slate-800 overflow-hidden"><div class="h-full rounded-full bg-emerald-500/70" style="width:${pct}%"></div></div>
          <div class="text-[10px] text-slate-500 font-mono mt-1">${f.percent_bytes}%</div>
        </td>
        <td class="py-2 text-right whitespace-nowrap"><a href="${link}" class="text-emerald-400 hover:text-emerald-300 text-[11px] font-semibold">Open →</a></td>
      </tr>`;
  }).join('');
}

function renderFiles(data) {
  if (!els.fileRows) return;
  const rows = data.largest_files || [];
  if (els.filesCount) els.filesCount.textContent = `${rows.length} shown`;
  if (!rows.length) {
    els.fileRows.innerHTML = '<tr><td colspan="4" class="py-6 text-center text-[11px] text-slate-500">No indexed media yet.</td></tr>';
    return;
  }
  els.fileRows.innerHTML = rows.map((f, idx) => {
    const res = getResolutionLabel(f.width, f.height);
    const folder = f.folder_path || f.path.slice(0, f.path.lastIndexOf('/')) || '/';
    const duration = f.type === 'video' && f.duration ? formatDuration(f.duration) : '';
    return `
      <tr class="border-b border-slate-800/60 hover:bg-slate-800/30 transition cursor-pointer" data-file-idx="${idx}" title="Click to preview">
        <td class="py-2 pr-3 min-w-0">
          <div class="text-slate-200 truncate max-w-[22rem]" title="${escapeHtml(f.path)}">${escapeHtml(f.name)}</div>
          <div class="text-[10px] text-slate-500 font-mono truncate max-w-[22rem]" title="${escapeHtml(folder)}">${escapeHtml(folder.split('/').slice(-2).join('/') || '/')}</div>
        </td>
        <td class="py-2 pr-3 text-right font-mono text-slate-200 whitespace-nowrap">${escapeHtml(f.size_formatted || formatBytes(f.size))}</td>
        <td class="py-2 pr-3 text-right whitespace-nowrap hidden sm:table-cell">
          ${res ? `<span class="text-[10px] px-1.5 py-[1px] rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">${escapeHtml(res)}</span>` : ''}
          ${duration ? `<span class="text-[10px] text-slate-500 font-mono ml-1.5">${escapeHtml(duration)}</span>` : ''}
        </td>
        <td class="py-2 text-right whitespace-nowrap">
          <a href="/?folder=${encodeURIComponent(folder)}" class="text-emerald-400 hover:text-emerald-300 text-[11px] font-semibold" onclick="event.stopPropagation()">Open →</a>
        </td>
      </tr>`;
  }).join('');
  els.fileRows.querySelectorAll('[data-file-idx]').forEach((tr) => {
    tr.addEventListener('click', () => openPreview(rows[Number(tr.dataset.fileIdx)]));
  });
}

function renderReclaim(data) {
  if (!els.reclaim) return;
  const r = data.reclaimable;
  const blocks = [
    {
      value: fmtInt(r.duplicate_groups),
      label: `size-match duplicate group${r.duplicate_groups === 1 ? '' : 's'} to verify`,
      note: `${fmtInt(r.duplicate_files)} files · up to ${r.duplicate_bytes_formatted}`,
      link: { href: '/', text: 'Review in Library →', title: 'Open the Library and use the Duplicates panel to verify and resolve groups' },
      warn: r.duplicate_groups > 0,
    },
    {
      value: r.webp_saved_formatted,
      label: `saved by WebP optimisation (${fmtInt(r.webp_converted_count)} converted)`,
      note: r.webp_pending_count
        ? `${fmtInt(r.webp_pending_count)} loose image${r.webp_pending_count === 1 ? '' : 's'} (${r.webp_pending_bytes_formatted}) still to convert`
        : 'no loose images left to convert',
      link: { href: '/', text: 'WebP status →', title: 'The WebP worker card lives in the Library Background Tasks modal' },
      warn: r.webp_pending_count > 0,
    },
    {
      value: fmtInt(r.zip_eligible_folders),
      label: `leaf image folders eligible for ZIP (${r.zip_eligible_bytes_formatted})`,
      note: 'compresses to one archive per folder',
      link: { href: '/', text: 'Batch ZIP →', title: 'Run Batch ZIP from the Library Background Tasks modal' },
      warn: r.zip_eligible_folders > 0,
    },
    {
      value: fmtInt(r.empty_folders),
      label: `empty leaf folder${r.empty_folders === 1 ? '' : 's'} (indexed)`,
      note: 'the Clean Empty Folders tool verifies on disk before deleting',
      link: { href: '/', text: 'Empty folders →', title: 'Clean Empty Folders lives in the Library Background Tasks modal' },
      warn: r.empty_folders > 0,
    },
  ];
  els.reclaim.innerHTML = blocks.map((b) => `
    <div class="border-l-2 ${b.warn ? 'border-amber-500/70' : 'border-emerald-600/60'} pl-3 min-w-0">
      <div class="text-base font-bold text-white truncate">${escapeHtml(b.value)}</div>
      <div class="text-[11px] text-slate-300 leading-snug">${escapeHtml(b.label)}</div>
      <div class="text-[10px] text-slate-500 font-mono leading-snug mt-0.5">${escapeHtml(b.note)}</div>
      <a href="${b.link.href}" title="${escapeHtml(b.link.title)}" class="inline-block mt-1 text-[11px] text-emerald-400 hover:text-emerald-300 font-semibold">${escapeHtml(b.link.text)}</a>
    </div>
  `).join('');
}

function renderHealth(data) {
  if (!els.healthPills) return;
  const h = data.health;
  const pills = [];

  if (h.is_crawling) {
    pills.push({ warn: true, text: 'Index crawling…', title: 'The background crawler is walking the library right now' });
  } else if (!h.is_complete) {
    pills.push({
      warn: true,
      text: `Index stale · ${fmtInt(h.stale_folders)} folders / ${fmtInt(h.stale_files)} files missing`,
      title: h.prune_reason || 'The crawler blocked a prune because the mount looked partially unavailable',
    });
  } else {
    pills.push({ warn: false, text: 'Index fresh', title: 'Every indexed folder was reachable at the last crawl' });
  }

  pills.push({
    warn: h.missing_thumbs > 0,
    text: `${fmtInt(h.missing_thumbs)} missing thumbnail${h.missing_thumbs === 1 ? '' : 's'}`,
    title: 'Files awaiting a poster frame from the Thumbnail Generator',
  });
  pills.push({
    warn: h.unprobed > 0,
    text: `${fmtInt(data.totals.files - h.unprobed)} of ${fmtInt(data.totals.files)} probed`,
    title: 'Dimensions, duration and codecs are filled in by the Media Metadata Probe',
  });
  pills.push({
    warn: h.unhashed_visual > 0,
    text: `${fmtInt(data.totals.files - h.unhashed_visual)} visual hashed`,
    title: 'Perceptual hashes power visual duplicate detection',
  });
  pills.push({
    warn: h.missing_animated > 0,
    text: `${fmtInt(h.missing_animated)} without animated preview`,
    title: 'Videos still missing a looping animated WebP preview',
  });
  pills.push({
    warn: h.needs_transcode > 0,
    text: `${fmtInt(h.needs_transcode)} need transcoding`,
    title: 'Non-web formats playable only after a remux/re-encode',
  });
  pills.push({
    warn: false,
    text: `${fmtInt(data.reclaimable.empty_folders)} empty folders`,
    title: 'Indexed leaf folders with no files',
  });
  if (data.reclaimable.duplicate_ignored_pairs > 0) {
    pills.push({
      warn: false,
      text: `${fmtInt(data.reclaimable.duplicate_ignored_pairs)} ignored duplicate pair${data.reclaimable.duplicate_ignored_pairs === 1 ? '' : 's'}`,
      title: 'Pairs you marked as false positives',
    });
  }

  els.healthPills.innerHTML = pills.map((p) => `
    <span title="${escapeHtml(p.title)}" class="inline-flex items-center gap-2 border border-slate-700/70 rounded-full px-3 py-1.5 text-[11px] text-slate-200 cursor-help">
      <span class="w-1.5 h-1.5 rounded-full ${p.warn ? 'bg-amber-400' : 'bg-emerald-500'} shrink-0"></span>
      <span>${escapeHtml(p.text)}</span>
    </span>
  `).join('');
}

// ---------------------------------------------------------------- charts

function disposeCharts() {
  Object.values(charts).forEach((c) => { try { c.dispose(); } catch (e) { /* already gone */ } });
  charts = {};
}

function mountChart(key, el, option) {
  if (!el) return;
  const chart = echarts.init(el, null, { renderer: 'canvas' });
  chart.setOption(option);
  charts[key] = chart;
}

// ------------------------------------------------------- treemap roam/zoom

// A phone can show the whole library at once but cannot read it at that size,
// so the treemap is *roamable*: pinch/wheel zooms, a drag pans, and the buttons
// drive the same view programmatically for mouse users. `visibleMin` /
// `childrenVisibleMin` keep the zoomed-out view free of unreadable specks —
// ECharts re-lays out on every zoom, so detail (and its labels) reappears as
// soon as a block is big enough to read.
const TREEMAP_ZOOM_MIN = 0.35;
const TREEMAP_ZOOM_MAX = 12;
const TREEMAP_ZOOM_STEP = 1.45;
// How far a finger/pointer may travel before a release stops counting as a tap
// on a block (a pan must not navigate to the folder under the pointer).
const TREEMAP_TAP_SLOP = 8;
let treemapDragDistance = 0;

function treemapSeries(chart) {
  try {
    return chart.getModel().getSeriesByIndex(0);
  } catch (e) {
    return null;
  }
}

// The live view is described by the root node's rect inside the chart box: at
// zoom 1 it is exactly the box, and a bigger rect means the treemap overflows
// (the canvas clips it) so panning can bring the rest into view.
function treemapView(chart) {
  const series = treemapSeries(chart);
  if (!series) return null;
  try {
    const layout = series.getData().tree.root.getLayout();
    const info = series.layoutInfo;
    if (!layout || !info || !layout.width || !layout.height) return null;
    return { series, layout, info };
  } catch (e) {
    return null;
  }
}

function dispatchTreemapView(chart, view, rootRect) {
  chart.dispatchAction({ type: 'treemapRender', seriesId: view.series.id, rootRect });
}

// Zoom about the centre of the visible box, keeping the point under the centre
// fixed so a zoom-in does not drift. The "zoom" is how much bigger the drawn
// root rect is than the chart box, so zooming in grows the rect (the canvas
// clips it and a drag brings the rest into view).
function zoomTreemap(factor) {
  const chart = charts.treemap;
  if (!chart) return;
  const view = treemapView(chart);
  if (!view) return;
  const { layout, info } = view;
  const current = layout.width / info.width;
  const target = Math.min(TREEMAP_ZOOM_MAX, Math.max(TREEMAP_ZOOM_MIN, current * factor));
  const step = target / current;
  if (Math.abs(step - 1) < 1e-4) return;
  const cx = info.width / 2;
  const cy = info.height / 2;
  dispatchTreemapView(chart, view, {
    x: cx + (layout.x - cx) * step,
    y: cy + (layout.y - cy) * step,
    width: layout.width * step,
    height: layout.height * step,
  });
}

function resetTreemap() {
  const chart = charts.treemap;
  if (!chart) return;
  const view = treemapView(chart);
  if (!view) return;
  dispatchTreemapView(chart, view, { x: 0, y: 0, width: view.info.width, height: view.info.height });
}

// Track how far the pointer moved between press and release so the click
// handler can tell a tap from a pan (roam drags end in a click as well).
function trackTreemapDrag() {
  if (!els.treemap) return;
  let down = null;
  els.treemap.addEventListener('pointerdown', (e) => {
    down = { x: e.clientX, y: e.clientY };
    treemapDragDistance = 0;
  });
  els.treemap.addEventListener('pointermove', (e) => {
    if (!down) return;
    treemapDragDistance = Math.max(
      treemapDragDistance,
      Math.hypot(e.clientX - down.x, e.clientY - down.y),
    );
  });
  const end = () => { down = null; };
  els.treemap.addEventListener('pointerup', end);
  els.treemap.addEventListener('pointercancel', end);
  els.treemap.addEventListener('pointerleave', end);
}

function buildCharts(data) {
  disposeCharts();
  const c = themeColors();
  const tooltip = {
    backgroundColor: c.surface,
    borderColor: c.border,
    textStyle: { color: c.text, fontSize: 11 },
  };

  // Treemap: every top-level folder, one level of children each. Nothing is
  // folded away — the whole library is in the chart and roam is how a phone
  // reads it.
  const treemapNodes = (data.folders.top || []).map((f, i) => ({
    name: f.name,
    value: f.bytes,
    path: f.path,
    is_group: Boolean(f.is_group),
    itemStyle: f.is_group ? { color: c.palette[(i + 2) % c.palette.length] } : { color: c.palette[i % c.palette.length] },
    children: (f.children || []).map((kid) => ({ name: kid.name, value: kid.bytes, path: kid.path })),
  }));
  if (els.treemapTotal) {
    els.treemapTotal.textContent = `${data.folders.root_bytes_formatted} · ${fmtInt(treemapNodes.length)} top-level`;
  }
  mountChart('treemap', els.treemap, {
    tooltip: {
      ...tooltip,
      confine: true,
      formatter: (p) => {
        if (p.value == null) return p.name;
        const pct = data.folders.root_bytes ? ((p.value / data.folders.root_bytes) * 100).toFixed(1) : '0.0';
        return `<b>${escapeHtml(p.name)}</b><br>${formatBytes(p.value)} · ${pct}% of library`;
      },
    },
    series: [{
      type: 'treemap',
      name: 'Storage',
      // Pinch / scroll zooms, a drag pans; the buttons drive the same view.
      roam: true,
      scaleLimit: { min: TREEMAP_ZOOM_MIN, max: TREEMAP_ZOOM_MAX },
      nodeClick: false,
      breadcrumb: { show: false },
      width: '100%',
      height: '100%',
      data: treemapNodes,
      // At zoom 1 the whole library is on screen, so hide what cannot be read
      // there; each zoom re-lays out and brings the next level back with its
      // labels once a block is big enough to hold them.
      visibleMin: 40,
      childrenVisibleMin: 12000,
      label: { fontSize: 11, color: '#ffffff', overflow: 'truncate' },
      upperLabel: { show: true, height: 20, fontSize: 10, color: '#ffffff', overflow: 'truncate' },
      itemStyle: { borderColor: c.surface, borderWidth: 2, gapWidth: 2 },
      levels: [
        { itemStyle: { borderColor: c.surface, borderWidth: 3, gapWidth: 3 }, upperLabel: { show: true, height: 22 } },
        {
          itemStyle: { borderColor: c.surface, borderWidth: 1, gapWidth: 1 },
          colorSaturation: [0.3, 0.55],
          label: { fontSize: 10 },
        },
      ],
    }],
  });
  if (charts.treemap) {
    charts.treemap.on('click', (p) => {
      // A pan also ends in a click, so only a tap that barely moved opens the
      // folder — otherwise dragging around the treemap would navigate away.
      if (treemapDragDistance > TREEMAP_TAP_SLOP) return;
      const path = p && p.data && p.data.path;
      if (path) window.location.href = `/?folder=${encodeURIComponent(path)}`;
    });
  }

  // Media mix donut (bytes) + legend with counts.
  const typeData = (data.by_type || []).map((t, i) => ({
    name: t.type === 'video' ? 'Videos' : t.type === 'image' ? 'Images' : 'Other',
    value: t.bytes,
    itemStyle: { color: i === 0 ? c.palette[0] : c.palette[2 + (i % 3)] },
  }));
  mountChart('type', els.typeChart, {
    tooltip: { ...tooltip, formatter: (p) => `${p.name}<br>${formatBytes(p.value)} · ${p.percent}%` },
    series: [{
      type: 'pie',
      radius: ['52%', '78%'],
      center: ['50%', '50%'],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: c.surface, borderWidth: 2 },
      label: { color: c.text, fontSize: 11, formatter: '{d}%' },
      labelLine: { lineStyle: { color: c.muted } },
      data: typeData,
    }],
  });
  if (els.typeLegend) {
    els.typeLegend.innerHTML = (data.by_type || []).map((t, i) => `
      <div class="flex items-center gap-2 text-[11px] min-w-0">
        <span class="w-2.5 h-2.5 rounded-sm shrink-0" style="background:${i === 0 ? c.palette[0] : c.palette[2 + (i % 3)]}"></span>
        <span class="text-slate-300 capitalize">${escapeHtml(t.type)}</span>
        <span class="ml-auto font-mono text-slate-400 shrink-0">${fmtInt(t.count)} · ${escapeHtml(t.bytes_formatted)}</span>
      </div>`).join('');
  }

  // Top extensions by bytes.
  const extItems = ((data.by_extension && data.by_extension.items) || []).slice().reverse();
  mountChart('ext', els.extChart, {
    grid: { left: 4, right: 12, top: 6, bottom: 4, containLabel: true },
    tooltip: { ...tooltip, formatter: (p) => `${p.name} · ${formatBytes(p.value)}` },
    xAxis: {
      type: 'value',
      axisLabel: { color: c.muted, fontSize: 10, formatter: (v) => formatBytes(v) },
      splitLine: { lineStyle: { color: c.border } },
      axisLine: { show: false },
    },
    yAxis: {
      type: 'category',
      data: extItems.map((e) => e.ext),
      axisLabel: { color: c.text, fontSize: 10, fontFamily: 'monospace' },
      axisLine: { lineStyle: { color: c.border } },
      axisTick: { show: false },
    },
    series: [{
      type: 'bar',
      data: extItems.map((e) => e.bytes),
      itemStyle: { color: c.palette[0], borderRadius: [0, 3, 3, 0] },
      barMaxWidth: 16,
    }],
  });

  // Resolution histogram (clip count per bucket).
  const buckets = data.quality.buckets || [];
  mountChart('res', els.resChart, {
    grid: { left: 4, right: 8, top: 12, bottom: 4, containLabel: true },
    tooltip: {
      ...tooltip,
      formatter: (p) => {
        const b = buckets[p.dataIndex];
        return `${b.label}<br>${fmtInt(b.count)} clips · ${b.bytes_formatted}`;
      },
    },
    xAxis: {
      type: 'category',
      data: buckets.map((b) => b.label),
      axisLabel: { color: c.text, fontSize: 10 },
      axisLine: { lineStyle: { color: c.border } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: c.muted, fontSize: 10 },
      splitLine: { lineStyle: { color: c.border } },
    },
    series: [{
      type: 'bar',
      data: buckets.map((b) => b.count),
      itemStyle: { color: c.palette[1], borderRadius: [3, 3, 0, 0] },
      barMaxWidth: 48,
    }],
  });
  if (els.qualityNotes) {
    const q = data.quality;
    els.qualityNotes.innerHTML = [
      `runtime ${q.total_duration_formatted}`,
      `${fmtInt(q.videos)} videos`,
      `probed ${fmtInt(q.probed_videos)}`,
      q.unprobed_videos ? `${fmtInt(q.unprobed_videos)} unprobed` : null,
      `≥${q.fps_threshold}fps ${fmtInt(q.high_fps_count)}`,
    ].filter(Boolean).map((n) => `<span class="font-mono">${escapeHtml(n)}</span>`).join('<span class="text-slate-600">·</span>');
  }
}

// --------------------------------------------------------------- preview

function closePreview() {
  if (!els.previewModal) return;
  if (els.previewVideo) {
    els.previewVideo.pause();
    els.previewVideo.removeAttribute('src');
    els.previewVideo.load();
    els.previewVideo.classList.add('hidden');
  }
  if (els.previewImage) {
    els.previewImage.removeAttribute('src');
    els.previewImage.classList.add('hidden');
  }
  els.previewModal.classList.add('hidden');
  els.previewModal.classList.remove('flex');
}

function openPreview(file) {
  if (!els.previewModal || !file) return;
  const folder = file.folder_path || file.path.slice(0, file.path.lastIndexOf('/')) || '/';
  if (els.previewName) els.previewName.textContent = file.name;
  if (els.previewPath) els.previewPath.textContent = file.path;
  if (els.previewOpenBtn) els.previewOpenBtn.href = `/?folder=${encodeURIComponent(folder)}`;
  if (els.previewMeta) {
    els.previewMeta.textContent = [
      file.size_formatted,
      getResolutionLabel(file.width, file.height),
      file.type === 'video' && file.duration ? formatDuration(file.duration) : '',
      file.video_codec ? String(file.video_codec).toUpperCase() : '',
    ].filter(Boolean).join(' · ');
  }
  if (file.type === 'video' && els.previewVideo) {
    els.previewVideo.src = file.media_url;
    els.previewVideo.classList.remove('hidden');
  } else if (els.previewImage) {
    els.previewImage.src = file.media_url;
    els.previewImage.classList.remove('hidden');
  }
  els.previewModal.classList.remove('hidden');
  els.previewModal.classList.add('flex');
}

// ------------------------------------------------------------------ load

function showError(message) {
  if (els.loading) els.loading.classList.add('hidden');
  if (els.body) els.body.classList.add('hidden');
  if (els.body) els.body.classList.remove('flex');
  if (els.error) {
    els.error.textContent = message;
    els.error.classList.remove('hidden');
  }
}

async function load({ refresh = false } = {}) {
  if (els.refreshBtn) els.refreshBtn.disabled = true;
  if (els.refreshSpinner) els.refreshSpinner.classList.remove('hidden');
  try {
    const res = await fetch(`/api/analytics/overview${refresh ? '?refresh=1' : ''}`, {
      headers: { Accept: 'application/json' },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    currentData = data;

    if (els.mobileSubtitle) els.mobileSubtitle.textContent = `${fmtInt(data.totals.files)} files · ${data.totals.bytes_formatted}`;
    if (els.generatedAt) {
      els.generatedAt.textContent = `computed ${new Date(data.generated_at * 1000).toLocaleTimeString()}`;
    }

    renderKpis(data);
    renderFolders(data);
    renderFiles(data);
    renderReclaim(data);
    renderHealth(data);

    if (els.loading) els.loading.classList.add('hidden');
    if (els.error) els.error.classList.add('hidden');
    // Reveal the stage *before* mounting charts: ECharts measures its container
    // on init, and a card inside a `hidden` parent has zero size.
    if (els.body) {
      els.body.classList.remove('hidden');
      els.body.classList.add('flex');
    }
    buildCharts(data);

    if (refresh) showToast('📊 Analytics reloaded', 'success');
  } catch (e) {
    showError(`Could not load analytics: ${e.message}. Check that the index is available, then press Refresh.`);
  } finally {
    if (els.refreshBtn) els.refreshBtn.disabled = false;
    if (els.refreshSpinner) els.refreshSpinner.classList.add('hidden');
  }
}

function init() {
  cacheEls();

  if (els.refreshBtn) els.refreshBtn.addEventListener('click', () => load({ refresh: true }));
  if (els.treemapZoomIn) els.treemapZoomIn.addEventListener('click', () => zoomTreemap(TREEMAP_ZOOM_STEP));
  if (els.treemapZoomOut) els.treemapZoomOut.addEventListener('click', () => zoomTreemap(1 / TREEMAP_ZOOM_STEP));
  if (els.treemapReset) els.treemapReset.addEventListener('click', resetTreemap);
  trackTreemapDrag();
  if (els.previewCloseBtn) els.previewCloseBtn.addEventListener('click', closePreview);
  if (els.previewModal) {
    els.previewModal.addEventListener('click', (e) => { if (e.target === els.previewModal) closePreview(); });
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (els.previewModal && !els.previewModal.classList.contains('hidden')) { closePreview(); return; }
      if (els.refreshBtn !== document.activeElement) {
        closeThemeCustomizerIfOpen();
      }
    }
    if (e.key === 't' && !e.ctrlKey && !e.metaKey) {
      const btn = Array.from(document.querySelectorAll('[data-theme-modal-btn]')).find((b) => b.offsetParent !== null);
      if (btn && document.activeElement && document.activeElement.tagName !== 'TEXTAREA') btn.click();
    }
  });

  let resizeTimer = null;
  const resizeCharts = () => {
    Object.values(charts).forEach((c) => { try { c.resize(); } catch (e) { /* disposed */ } });
  };
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(resizeCharts, 150);
  });
  // A tab that was hidden while the page loaded has no layout, so charts init
  // at zero size; re-measure when it becomes visible again.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) resizeCharts();
  });

  // Charts are colour-derived from the theme tokens; rebuild on any theme
  // change so accent and OLED/surface switches are reflected immediately.
  window.addEventListener('themechange', () => { if (currentData) buildCharts(currentData); });

  initThemeCustomizer();
  load();
}

function closeThemeCustomizerIfOpen() {
  const modal = byId('themeCustomizerModal');
  if (modal && !modal.classList.contains('hidden')) {
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    document.body.classList.remove('modal-scroll-lock');
  }
}

// Start the dashboard when the DOM is ready — a module script may already have
// run after DOMContentLoaded on a fast navigation.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
