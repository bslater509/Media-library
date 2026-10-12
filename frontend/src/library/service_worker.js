/**
 * Service Worker & Browser Cache Manager for Animated Video Previews
 * Manages CacheStorage registration, storage quota inspection, pre-caching, and pruning.
 */

export const ANIM_CACHE_NAME = 'media-library-anim-v1';

/**
 * Initializes the Service Worker registration and requests persistent storage.
 * @returns {Promise<ServiceWorkerRegistration|null>}
 */
export async function initServiceWorker() {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) {
    return null;
  }

  try {
    const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });

    // Request persistent storage if the browser supports it
    if (typeof navigator.storage !== 'undefined' && typeof navigator.storage.persist === 'function') {
      try {
        const isPersisted = await navigator.storage.persisted();
        if (!isPersisted) {
          await navigator.storage.persist();
        }
      } catch (_) {
        // Storage persistence request failure is non-fatal
      }
    }

    return reg;
  } catch (err) {
    console.warn('[ServiceWorker] Registration failed:', err);
    return null;
  }
}

/**
 * Retrieves the count of animated thumbnails stored in browser CacheStorage
 * and estimates origin storage quota.
 * @returns {Promise<{supported: boolean, count: number, usage: number|null, quota: number|null}>}
 */
export async function getBrowserAnimCacheStats() {
  if (typeof window === 'undefined' || !('caches' in window)) {
    return { supported: false, count: 0, usage: null, quota: null };
  }

  try {
    const cache = await caches.open(ANIM_CACHE_NAME);
    const keys = await cache.keys();
    const count = keys.length;

    let usage = null;
    let quota = null;
    if (typeof navigator.storage !== 'undefined' && typeof navigator.storage.estimate === 'function') {
      try {
        const est = await navigator.storage.estimate();
        usage = est.usage || null;
        quota = est.quota || null;
      } catch (_) {}
    }

    return { supported: true, count, usage, quota };
  } catch (err) {
    console.warn('[CacheStorage] Failed to inspect animated cache:', err);
    return { supported: true, count: 0, usage: null, quota: null };
  }
}

/**
 * Empties the animated preview CacheStorage partition.
 * @returns {Promise<{success: boolean, count: number}>}
 */
export async function clearBrowserAnimCache() {
  if (typeof window === 'undefined' || !('caches' in window)) {
    return { success: false, count: 0 };
  }

  try {
    await caches.delete(ANIM_CACHE_NAME);

    // Also notify active service worker controller if present
    if (navigator.serviceWorker && navigator.serviceWorker.controller) {
      navigator.serviceWorker.controller.postMessage({ type: 'CLEAR_CACHE' });
    }

    return { success: true, count: 0 };
  } catch (err) {
    console.warn('[CacheStorage] Failed to clear animated cache:', err);
    return { success: false, count: 0 };
  }
}

/**
 * Pre-downloads animated previews for a list of video files into browser CacheStorage.
 * Runs with concurrency limit to prevent network congestion.
 * @param {Array<Object>} files Array of file objects
 * @param {Function} [onProgress] Callback receiving ({ current, total, added })
 * @returns {Promise<{total: number, added: number}>}
 */
export async function precacheFolderAnimatedPreviews(files = [], onProgress = null) {
  if (typeof window === 'undefined' || !('caches' in window)) {
    return { total: 0, added: 0 };
  }

  // Filter video files
  const videoFiles = files.filter((f) => {
    if (!f || !f.path) return false;
    const isVid = f.type === 'video' || /\.(mp4|m4v|mov|mkv|webm|avi|wmv|flv)$/i.test(f.path);
    return isVid;
  });

  if (videoFiles.length === 0) {
    return { total: 0, added: 0 };
  }

  const cache = await caches.open(ANIM_CACHE_NAME);
  let added = 0;
  let processed = 0;
  const total = videoFiles.length;

  // Process with concurrency pool of 3 requests
  const concurrency = 3;
  let index = 0;

  async function worker() {
    while (index < videoFiles.length) {
      const file = videoFiles[index++];
      const url =
        file.animated_url ||
        `/api/backup/thumbnails/animated?path=${encodeURIComponent(file.path)}${file.mtime ? `&v=${file.mtime}` : ''}`;

      try {
        const existing = await cache.match(url);
        if (!existing) {
          const res = await fetch(url);
          if (res && res.status === 200) {
            const contentType = res.headers.get('content-type') || '';
            const isGenerating = res.headers.get('x-thumbnail-status') === 'generating';
            if (contentType.includes('image/webp') && !isGenerating) {
              await cache.put(url, res);
              added++;
            }
          }
        }
      } catch (_) {
        // Individual fetch failures (e.g. offline or interrupted) are ignored
      }

      processed++;
      if (typeof onProgress === 'function') {
        onProgress({ current: processed, total, added });
      }
    }
  }

  const pool = [];
  for (let i = 0; i < Math.min(concurrency, videoFiles.length); i++) {
    pool.push(worker());
  }
  await Promise.all(pool);

  return { total, added };
}
