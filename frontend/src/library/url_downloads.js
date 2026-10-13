// Shared rendering helpers for URL/playlist download jobs.
//
// The same job payload (`/api/system/status#url_downloads`) is rendered in two
// places: the desktop Background Tasks modal section and the mobile Controls &
// Settings "Downloads" tab. Keeping the row markup and the summary/badge math
// here means both surfaces stay visually and behaviourally in sync.
import { escapeHtml } from './utils.js';

const BADGE_CLASSES = {
  downloading: 'bg-emerald-900/60 text-emerald-300',
  queued: 'bg-slate-800 text-slate-300',
  completed: 'bg-emerald-900/60 text-emerald-400',
  failed: 'bg-red-900/50 text-red-400',
  cancelled: 'bg-slate-800 text-slate-500',
};

/** Number of jobs that are actively downloading or waiting in the queue. */
export function urlDownloadsActivityCount(data) {
  if (!data) return 0;
  const active = Array.isArray(data.active) ? data.active.length : 0;
  const queued = Array.isArray(data.queued) ? data.queued.length : 0;
  return active + queued;
}

/** Short one-line status used as the summary label on both surfaces. */
export function urlDownloadsSummaryText(data) {
  const active = (data && data.active) || [];
  const queued = (data && data.queued) || [];
  if (active.length > 1) return `DL ${active.length} active`;
  if (active.length === 1) return `DL ${Math.round(active[0].percent || 0)}%`;
  if (queued.length) return `${queued.length} queued`;
  return 'Idle';
}

/** Best available human label for a job: live title, then URL, then job id. */
export function urlDownloadTitle(item) {
  if (!item) return '';
  const title = item.current_title
    || String(item.url || '').replace(/^https?:\/\//, '')
    || item.id
    || '';
  return String(title);
}

/**
 * One job row, shared by the Background Tasks modal, the mobile tab, and the
 * /downloads page. `data-urldl-cancel` marks the cancel control so callers can
 * bind it; when `retryable` is true, failed/cancelled rows also render a
 * `data-urldl-retry` control (the /downloads page uses it).
 */
export function urlDownloadRowHtml(item, { retryable = false } = {}) {
  if (!item) return '';
  const isActive = item.status === 'downloading';
  const isQueued = item.status === 'queued';
  const isCompleted = item.status === 'completed';
  const isFailed = item.status === 'failed';
  const isCancelled = item.status === 'cancelled';
  const cancellable = isActive || isQueued;
  const retryableRow = retryable && (isFailed || isCancelled);
  const title = urlDownloadTitle(item);
  const pct = Math.round(item.percent || 0);
  const dl = item.downloader ? String(item.downloader) : '';
  const pos = item.playlist_index && item.playlist_count
    ? `${item.playlist_index}/${item.playlist_count}`
    : '';
  const activeLabel = pct > 0 ? pct + '%' : (item.playlist_index ? item.playlist_index + ' done' : '…');

  const detailParts = [];
  if (isActive) {
    if (pos) detailParts.push(pos);
    else if (item.playlist_index) detailParts.push(`${item.playlist_index} done`);
    if (item.speed) detailParts.push(item.speed);
    if (item.eta) detailParts.push(`ETA ${item.eta}`);
    if (dl) detailParts.push(dl);
  } else if (isCompleted && item.imported_count) {
    detailParts.push(`${item.imported_count} file${item.imported_count > 1 ? 's' : ''}`);
    if (dl) detailParts.push(dl);
  } else if (isFailed && item.error) {
    detailParts.push(String(item.error).slice(0, 80));
    if (dl) detailParts.push(dl);
  } else if (isCancelled) {
    if (dl) detailParts.push(dl);
  }

  const badgeCls = BADGE_CLASSES[item.status] || 'bg-slate-800 text-slate-400';
  const cancelBtn = cancellable
    ? `<button data-urldl-cancel="${escapeHtml(item.id)}" class="text-[10px] px-2 py-0.5 bg-red-900/30 text-red-400 hover:bg-red-900/50 hover:text-red-300 rounded border border-red-900/50 transition shrink-0" title="Cancel">✕</button>`
    : '';
  const retryBtn = retryableRow
    ? `<button data-urldl-retry="${escapeHtml(item.id)}" class="text-[10px] px-2 py-0.5 bg-emerald-900/30 text-emerald-300 hover:bg-emerald-900/50 hover:text-emerald-200 rounded border border-emerald-800/50 transition shrink-0" title="Retry download">↻</button>`
    : '';

  return `
        <div class="bg-slate-900 rounded-lg border border-slate-800/80 p-2 flex flex-col gap-1.5">
          <div class="flex items-center gap-2 min-w-0">
            <span class="text-[9px] uppercase px-1.5 py-0.5 rounded ${badgeCls} font-mono shrink-0">${escapeHtml(item.status)}</span>
            <span class="truncate text-[11px] text-slate-300 flex-1 min-w-0" title="${escapeHtml(title)}">${escapeHtml(title)}</span>
            ${retryBtn}
            ${cancelBtn}
          </div>
          ${(isActive || isQueued) ? `
          <div class="flex items-center gap-2">
            <div class="flex-1 h-1 bg-slate-800 rounded-full overflow-hidden">
              <div class="h-full bg-emerald-500 transition-all duration-300${isActive && pct <= 0 ? ' animate-pulse opacity-60' : ''}" style="width:${isActive ? (pct > 0 ? pct : 100) : 0}%"></div>
            </div>
            <span class="text-[10px] text-emerald-400 font-mono shrink-0">${isActive ? activeLabel : 'queued'}</span>
          </div>` : ''}
          ${detailParts.length ? `<div class="text-[10px] text-slate-500 font-mono truncate">${escapeHtml(detailParts.join(' · '))}</div>` : ''}
        </div>`;
}

/**
 * Ordered rows for a container. Active jobs first, then queued, then the most
 * recent finished jobs (newest first). `historyLimit` caps only the history
 * tail, so active/queued jobs are always shown in full. `retryable` adds the
 * retry control to failed/cancelled rows.
 */
export function urlDownloadRowsHtml(data, { historyLimit = 20, retryable = false } = {}) {
  const active = (data && data.active) || [];
  const queued = (data && data.queued) || [];
  const history = (data && data.history) || [];
  const recent = historyLimit > 0 ? history.slice(-historyLimit) : history;
  return [...active, ...queued, ...recent.slice().reverse()]
    .map(item => urlDownloadRowHtml(item, { retryable }))
    .join('');
}
