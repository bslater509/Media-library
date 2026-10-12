/**
 * Media Library & Video Sorter — Service Worker
 * Intercepts animated thumbnail requests and caches them in browser CacheStorage.
 */

const CACHE_NAME = 'media-library-anim-v1';

// Install event: skip waiting to activate immediately
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

// Activate event: purge obsolete caches and claim existing clients
self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      if ('caches' in self) {
        const cacheNames = await caches.keys();
        await Promise.all(
          cacheNames
            .filter((name) => name.startsWith('media-library-anim-') && name !== CACHE_NAME)
            .map((name) => caches.delete(name))
        );
      }
      if (self.clients && self.clients.claim) {
        await self.clients.claim();
      }
    })()
  );
});

// Fetch event: Cache-First strategy for animated video previews
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Only intercept GET requests to /api/backup/thumbnails/animated on our origin
  if (
    event.request.method === 'GET' &&
    url.origin === self.location.origin &&
    url.pathname === '/api/backup/thumbnails/animated'
  ) {
    event.respondWith(handleAnimatedThumbnailFetch(event.request));
  }
});

/**
 * Handles fetching an animated WebP thumbnail:
 * 1. Checks CacheStorage first.
 * 2. If missed, fetches from network.
 * 3. Only stores if the response is 200 OK, WebP, and not a temporary generating placeholder.
 */
async function handleAnimatedThumbnailFetch(request) {
  const cache = await caches.open(CACHE_NAME);
  const cachedResponse = await cache.match(request);
  if (cachedResponse) {
    return cachedResponse;
  }

  try {
    const networkResponse = await fetch(request);

    // Only cache completed WebP files (not temporary JPEG fallbacks or 202 Generating status)
    if (networkResponse && networkResponse.status === 200) {
      const contentType = networkResponse.headers.get('content-type') || '';
      const isGenerating = networkResponse.headers.get('x-thumbnail-status') === 'generating';

      if (contentType.includes('image/webp') && !isGenerating) {
        // Clone response before saving to cache
        cache.put(request, networkResponse.clone());
      }
    }
    return networkResponse;
  } catch (error) {
    // If network fails (e.g. offline) and we happen to have cached, return it
    if (cachedResponse) {
      return cachedResponse;
    }
    throw error;
  }
}

// Client message interface for cache management
self.addEventListener('message', async (event) => {
  const data = event.data;
  if (!data || !data.type) return;

  if (data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  } else if (data.type === 'GET_STATS') {
    try {
      const cache = await caches.open(CACHE_NAME);
      const keys = await cache.keys();
      event.ports[0]?.postMessage({ success: true, count: keys.length });
    } catch (err) {
      event.ports[0]?.postMessage({ success: false, error: String(err) });
    }
  } else if (data.type === 'CLEAR_CACHE') {
    try {
      await caches.delete(CACHE_NAME);
      event.ports[0]?.postMessage({ success: true, count: 0 });
    } catch (err) {
      event.ports[0]?.postMessage({ success: false, error: String(err) });
    }
  }
});
