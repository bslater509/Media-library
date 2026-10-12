import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  ANIM_CACHE_NAME,
  initServiceWorker,
  getBrowserAnimCacheStats,
  clearBrowserAnimCache,
  precacheFolderAnimatedPreviews,
} from '../src/library/service_worker.js';

describe('service_worker.js', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    delete window.caches;
  });

  it('exports correct cache name constant', () => {
    expect(ANIM_CACHE_NAME).toBe('media-library-anim-v1');
  });

  it('initServiceWorker handles environment without navigator.serviceWorker', async () => {
    const originalNavigator = globalThis.navigator;
    try {
      globalThis.navigator = {};
      const res = await initServiceWorker();
      expect(res).toBeNull();
    } finally {
      globalThis.navigator = originalNavigator;
    }
  });

  it('initServiceWorker registers /sw.js with root scope', async () => {
    const mockRegister = vi.fn().mockResolvedValue({ scope: '/' });
    const mockPersist = vi.fn().mockResolvedValue(true);
    const mockPersisted = vi.fn().mockResolvedValue(false);

    const originalNavigator = globalThis.navigator;
    try {
      globalThis.navigator = {
        serviceWorker: { register: mockRegister },
        storage: { persist: mockPersist, persisted: mockPersisted },
      };

      const res = await initServiceWorker();
      expect(res).toEqual({ scope: '/' });
      expect(mockRegister).toHaveBeenCalledWith('/sw.js', { scope: '/' });
      expect(mockPersisted).toHaveBeenCalled();
      expect(mockPersist).toHaveBeenCalled();
    } finally {
      globalThis.navigator = originalNavigator;
    }
  });

  it('getBrowserAnimCacheStats returns unsupported when caches is absent', async () => {
    delete window.caches;
    const stats = await getBrowserAnimCacheStats();
    expect(stats.supported).toBe(false);
    expect(stats.count).toBe(0);
  });

  it('getBrowserAnimCacheStats queries CacheStorage and storage estimate', async () => {
    const mockKeys = vi.fn().mockResolvedValue(['req1', 'req2', 'req3']);
    const mockOpen = vi.fn().mockResolvedValue({ keys: mockKeys });
    window.caches = { open: mockOpen };

    const originalStorage = navigator.storage;
    navigator.storage = {
      estimate: vi.fn().mockResolvedValue({ usage: 1048576, quota: 1073741824 }),
    };

    try {
      const stats = await getBrowserAnimCacheStats();
      expect(stats.supported).toBe(true);
      expect(stats.count).toBe(3);
      expect(stats.usage).toBe(1048576);
      expect(stats.quota).toBe(1073741824);
      expect(mockOpen).toHaveBeenCalledWith(ANIM_CACHE_NAME);
    } finally {
      navigator.storage = originalStorage;
    }
  });

  it('clearBrowserAnimCache deletes cache and notifies service worker', async () => {
    const mockDelete = vi.fn().mockResolvedValue(true);
    window.caches = { delete: mockDelete };

    const mockPostMessage = vi.fn();
    const originalSW = navigator.serviceWorker;
    navigator.serviceWorker = {
      controller: { postMessage: mockPostMessage },
    };

    try {
      const res = await clearBrowserAnimCache();
      expect(res.success).toBe(true);
      expect(mockDelete).toHaveBeenCalledWith(ANIM_CACHE_NAME);
      expect(mockPostMessage).toHaveBeenCalledWith({ type: 'CLEAR_CACHE' });
    } finally {
      navigator.serviceWorker = originalSW;
    }
  });

  it('precacheFolderAnimatedPreviews filters video files and caches responses', async () => {
    const mockMatch = vi.fn().mockResolvedValue(null);
    const mockPut = vi.fn().mockResolvedValue(undefined);
    const mockOpen = vi.fn().mockResolvedValue({ match: mockMatch, put: mockPut });
    window.caches = { open: mockOpen };

    const mockFetch = vi.fn().mockResolvedValue({
      status: 200,
      headers: {
        get: (h) => {
          if (h.toLowerCase() === 'content-type') return 'image/webp';
          return null;
        },
      },
    });
    globalThis.fetch = mockFetch;

    const files = [
      { path: '/videos/sample.mp4', type: 'video', animated_url: '/api/backup/thumbnails/animated?path=sample.mp4' },
      { path: '/photos/pic.jpg', type: 'image' },
      { path: '/videos/clip.mov', type: 'video' },
    ];

    const progressUpdates = [];
    const res = await precacheFolderAnimatedPreviews(files, (p) => progressUpdates.push(p));

    expect(res.total).toBe(2);
    expect(res.added).toBe(2);
    expect(progressUpdates.length).toBe(2);
    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(mockPut).toHaveBeenCalledTimes(2);
  });
});
