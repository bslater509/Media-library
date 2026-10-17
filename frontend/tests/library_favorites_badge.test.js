import { describe, it, expect, vi, afterEach } from 'vitest';

// The sidebar favorites badge refreshes on page load and after every star
// toggle. It used to fetch the full favorites list (every favorite shaped into a
// browse entry, megabytes on a large collection) and read only `counts.all` from
// it. These assertions pin the cheap contract: the badge asks the COUNT-only
// endpoint, and a toggle updates it straight from the toggle response with no
// second request.

describe('favorites badge', () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('reads the count endpoint and the toggle response counts', async () => {
    const badge = document.createElement('span');
    badge.id = 'sidebarFavoritesBadge';
    document.body.appendChild(badge);

    const calls = [];
    globalThis.fetch = vi.fn(async (url, opts = {}) => {
      calls.push(`${opts.method || 'GET'} ${url}`);
      const body = String(url).startsWith('/api/backup/favorites/count')
        ? { counts: { all: 4812, videos: 4000, images: 812 } }
        : {
            status: 'success',
            path: '/lib/a.mp4',
            is_favorite: true,
            counts: { all: 7, videos: 5, images: 2 },
          };
      return { ok: true, json: async () => body };
    });

    // state.js caches its element lookups at import time, so the badge has to be
    // in the document before the module is first loaded.
    const { toggleFavorite, updateFavoritesBadgeCount } = await import(
      '../src/library/gallery.js'
    );
    const { els } = await import('../src/library/state.js');
    expect(els.sidebarFavoritesBadge).toBe(badge);

    await updateFavoritesBadgeCount();
    expect(badge.textContent).toBe('4812');
    expect(calls).toEqual(['GET /api/backup/favorites/count']);

    // A stand-in tile: passing it skips the CSS.escape card lookup.
    const card = document.createElement('div');
    await toggleFavorite('/lib/a.mp4', card);
    expect(badge.textContent).toBe('7');
    expect(calls).toEqual([
      'GET /api/backup/favorites/count',
      'POST /api/backup/favorites/toggle',
    ]);
  });
});
