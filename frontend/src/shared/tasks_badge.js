// Shared badge math for the background-tasks entry points (desktop pill,
// mobile header button, sorter header). Kept DOM-free so both the Library and
// Sorter bundles can use it and Vitest can unit-test it directly.

export const TASK_BADGE_MAX = 99;

const LIBOP_LABELS = { move: 'Moving', delete: 'Deleting', rename: 'Renaming' };
const BATCH_ACTIVE_STATUSES = new Set(['running', 'processing']);

/** Renders a badge number, capped at TASK_BADGE_MAX ("99+"), empty when <= 0. */
export function formatTaskCount(count) {
  const n = Number(count) || 0;
  if (n <= 0) return '';
  return n > TASK_BADGE_MAX ? `${TASK_BADGE_MAX}+` : String(n);
}

function queueLength(data) {
  if (!data) return 0;
  if (Array.isArray(data.queued)) return data.queued.length;
  return data.queue_length || 0;
}

/**
 * Count active + queued background items across every subsystem.
 * Discrete queues (transfers, library ops, transcodes, animated previews,
 * URL downloads, client-side uploads) contribute their running + queued item
 * counts. Long bulk sweeps (index, backup, thumbnails, WebP, ZIP jobs) count
 * as 1 while they are running, so the badge never shows "2,000+ pending".
 */
export function computeTaskSummary(sources = {}) {
  const {
    transfers, libraryOps, transcode, animated, thumbnails, webp,
    index, backup, zip, batchZip, batchUnpack, urlDownloads, uploads,
    metadata, thumbHash,
  } = sources;

  const trans = transfers ? (transfers.active ? 1 : 0) + queueLength(transfers) : 0;
  const ops = libraryOps ? (libraryOps.active ? 1 : 0) + queueLength(libraryOps) : 0;
  const tx = transcode ? (transcode.active ? 1 : 0) + queueLength(transcode) : 0;
  const anim = animated
    ? (Number(animated.total_queued) || 0)
      || (animated.active ? 1 : 0) + queueLength(animated)
    : 0;
  const th = thumbnails && thumbnails.status === 'generating' && (thumbnails.pending_count || 0) > 0 ? 1 : 0;
  const wp = webp && webp.status === 'converting' && (webp.pending_count || 0) > 0 ? 1 : 0;
  const ix = index && index.status === 'crawling' ? 1 : 0;
  const bk = backup && (backup.status === 'backing_up' || backup.status === 'restoring') ? 1 : 0;
  const zp = zip && zip.is_active ? 1 : 0;
  const bz = batchZip && BATCH_ACTIVE_STATUSES.has(batchZip.status) ? 1 : 0;
  const bu = batchUnpack && BATCH_ACTIVE_STATUSES.has(batchUnpack.status) ? 1 : 0;
  const md = metadata && metadata.status === 'probing' ? 1 : 0;
  const thh = thumbHash && (thumbHash.status === 'hashing' || thumbHash.status === 'migrating') ? 1 : 0;
  const dl = urlDownloads
    ? (Array.isArray(urlDownloads.active) ? urlDownloads.active.length : 0)
      + (Array.isArray(urlDownloads.queued) ? urlDownloads.queued.length : 0)
    : 0;
  const up = uploads ? (uploads.active || 0) + (uploads.queued || 0) : 0;

  return {
    count: trans + ops + tx + anim + th + wp + ix + bk + zp + bz + bu + md + thh + dl + up,
    active: trans + ops + tx + anim + th + wp + ix + bk + zp + bz + bu + md + thh + dl + up > 0,
  };
}

/**
 * Short human label for the most prominent active subsystem, used for the
 * header button's tooltip so the desktop pill keeps its informative detail
 * even though the visible text is now just the count badge.
 */
export function describePrimaryTask(sources = {}) {
  const {
    transfers, libraryOps, transcode, animated, thumbnails, webp,
    index, backup, zip, batchZip, batchUnpack, urlDownloads, uploads,
    metadata, thumbHash,
  } = sources;

  const hasQueue = (d) => d && (d.active || queueLength(d) > 0);

  if (hasQueue(transfers)) {
    const active = transfers.active;
    return active
      ? `Moving ${Math.round(active.percent || 0)}%`
      : `${queueLength(transfers)} transfer${queueLength(transfers) > 1 ? 's' : ''}`;
  }
  if (hasQueue(libraryOps)) {
    const a = libraryOps.active;
    const q = queueLength(libraryOps);
    const verb = a ? (LIBOP_LABELS[a.op] || 'Working') : 'Queued';
    return a ? `${verb}${q ? ` +${q}` : ''}` : `${q} op${q > 1 ? 's' : ''}`;
  }
  if (index && index.status === 'crawling') {
    const pct = typeof index.percent === 'number' ? Math.round(index.percent) : 0;
    return `Indexing ${pct}%`;
  }
  if (backup && (backup.status === 'backing_up' || backup.status === 'restoring')) {
    const actionName = backup.status === 'backing_up' ? 'Backup' : 'Restore';
    return `${actionName} ${Math.round(backup.progress_percent || 0)}%`;
  }
  if (hasQueue(transcode)) {
    const active = transcode.active;
    const q = queueLength(transcode);
    return active
      ? `${Math.round(active.percent || 0)}% (${q + 1})`
      : `${q} queued`;
  }
  if (animated && (animated.active || (animated.total_queued || 0) > 0)) {
    const pct = Math.round(animated.percent || 0);
    return animated.active ? `Anim ${pct}%` : `${animated.total_queued || queueLength(animated)} anim`;
  }
  if (thumbnails && thumbnails.status === 'generating' && (thumbnails.pending_count || 0) > 0) {
    return `Thumbs ${Math.round(thumbnails.percent || 0)}%`;
  }
  if (webp && webp.status === 'converting' && (webp.pending_count || 0) > 0) {
    return `WebP ${Math.round(webp.percent || 0)}%`;
  }
  if (metadata && metadata.status === 'probing') {
    return `Probing ${Math.round(metadata.percent || 0)}%`;
  }
  if (thumbHash && (thumbHash.status === 'hashing' || thumbHash.status === 'migrating')) {
    return `Visual hash ${Math.round(thumbHash.percent || 0)}%`;
  }
  if (batchZip && BATCH_ACTIVE_STATUSES.has(batchZip.status)) {
    return `Batch Zip ${Math.round(batchZip.percent || 0)}%`;
  }
  if (batchUnpack && BATCH_ACTIVE_STATUSES.has(batchUnpack.status)) {
    return `Extract ${Math.round(batchUnpack.overall_percent || 0)}%`;
  }
  if (urlDownloads && (Array.isArray(urlDownloads.active) && urlDownloads.active.length > 0)) {
    const dlActive = urlDownloads.active;
    const qDl = Array.isArray(urlDownloads.queued) ? urlDownloads.queued.length : 0;
    const pct = Math.round(dlActive[0].percent || 0);
    return dlActive.length > 1
      ? `DL ${pct}% (${dlActive.length})`
      : `DL ${pct}%${qDl ? ` +${qDl}` : ''}`;
  }
  if (zip && zip.is_active) {
    const runningTask = (zip.tasks || []).find((t) => t.status === 'running');
    const actionName = runningTask
      ? (runningTask.type === 'compress' ? 'Zipping' : (runningTask.type === 'unpack' ? 'Unpack' : 'Archive'))
      : 'ZIP';
    return `${actionName} ${runningTask ? Math.round(runningTask.percent || 0) : 0}%`;
  }
  if (urlDownloads && (Array.isArray(urlDownloads.queued) && urlDownloads.queued.length > 0)) {
    return `${urlDownloads.queued.length} dl queued`;
  }
  if (uploads && (uploads.active || 0) > 0) {
    return `Uploading ${Math.round(uploads.percent || 0)}%`;
  }
  return '';
}