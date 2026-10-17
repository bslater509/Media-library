import { describe, it, expect, vi, beforeEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import reviewSource from '../src/people/review.js?raw';

// The /people/review page: the unnamed-cluster queue (one cluster at a time,
// biggest first) and its three ways out — match into a named person (from the
// card's name pills, or the full list behind "More…"), name it as a new person,
// or dismiss it as "not a person". The queue keeps no state of its own: every
// action removes the cluster from the unnamed set, so "next" is just the next
// `GET /api/people?named=false&limit=1`.

const here = path.dirname(fileURLToPath(import.meta.url));
const templatesDir = path.resolve(here, '../../templates');

const PILLS = 6;

function expandIncludes(html, dir) {
  return html.replace(/\{\{\s*include\s+(['"])(.*?)\1\s*\}\}/g, (_m, _q, file) => {
    const filePath = path.resolve(dir, file);
    return expandIncludes(fs.readFileSync(filePath, 'utf8'), path.dirname(filePath));
  });
}

function installPage() {
  const html = expandIncludes(
    fs.readFileSync(path.join(templatesDir, 'people_review.html'), 'utf8'),
    templatesDir
  );
  const bodyMatch = html.match(/<body[^>]*>([\s\S]*)<\/body>/i);
  let body = bodyMatch ? bodyMatch[1] : html;
  body = body.replace(/<script[\s\S]*?<\/script>/gi, '');
  document.body.innerHTML = body;
}

const STATUS = {
  status: 'idle',
  enabled: true,
  engine_available: true,
  engine_reason: '',
  model_dir: '/data/models',
  pending_count: 0,
  faces_total: 120,
  people_total: 5,
  unnamed_people: 3,
  dismissed_faces: 4,
  is_paused: false,
};

const crop = (n, v) => `/api/people/crop?face_id=${n}&v=${v}.jpg`;

const CLUSTER = {
  id: 12,
  name: null,
  face_count: 34,
  media_count: 9,
  is_named: false,
  cover_url: crop(1, 'a'),
  sample_urls: [crop(1, 'a'), crop(2, 'b'), crop(3, 'c')],
  folder_names: ['Iceland 2023/Day 4', 'Goa <script>/Day 1'],
};

const NEXT = {
  people: [CLUSTER],
  counts: { people: 5, named: 8, unnamed: 3, faces: 120, dismissed: 4, files: 40 },
  total: 3,
  limit: 1,
  offset: 0,
  status: STATUS,
};

// The named people the pickers rank: the first is filed in the same folder as the
// cluster being reviewed, so it is the one the ranking must surface.
const NAMED_NAMES = ['Anna <script>', 'Bob', 'Chloe', 'Dare', 'Emily', 'Felix', 'Grace', 'Hugo'];
const NAMED = {
  people: NAMED_NAMES.map((name, i) => ({
    id: i + 1,
    name,
    face_count: 30 - i,
    media_count: 9 - i,
    is_named: true,
    cover_url: crop(100 + i, `n${i}`),
    sample_urls: [crop(100 + i, `n${i}`)],
    shared_folder_names: i === 0 ? ['Iceland 2023/Day 4'] : [],
    shared_folders: i === 0 ? 1 : 0,
    shared_faces: i === 0 ? 9 : 0,
    affinity: i === 0 ? 42 : 0,
  })),
  counts: { people: 5, named: 8, unnamed: 3, faces: 120, dismissed: 4, files: 40 },
  total: NAMED_NAMES.length,
  limit: 30,
  offset: 0,
  status: STATUS,
};

function jsonResponse(payload) {
  return { ok: true, status: 200, json: () => Promise.resolve(payload) };
}

// `holder` is mutable so a test can simulate the queue draining, or the API
// failing, between one cluster and the next.
function stubFetch({ queue = NEXT, named = NAMED, picker = null, fail = false } = {}) {
  const calls = [];
  const holder = { queue, named, picker, fail };
  global.fetch = vi.fn((url) => {
    const target = String(url);
    calls.push(target);
    if (holder.fail) {
      return Promise.resolve({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ detail: 'boom' }),
      });
    }
    if (target.startsWith('/api/people?')) {
      const q = new URL(target, 'http://localhost').searchParams;
      if (q.get('named') === 'true') {
        // The card asks for a handful of pills, the sheet for a full page.
        const limit = Number(q.get('limit') || 30);
        const source = limit <= PILLS ? holder.named : (holder.picker || holder.named);
        return Promise.resolve(jsonResponse({
          ...source,
          people: (source.people || []).slice(0, limit),
          total: source.total,
        }));
      }
      return Promise.resolve(jsonResponse(holder.queue));
    }
    return Promise.resolve(jsonResponse({ success: true, moved: 34, dismissed: 34 }));
  });
  return { calls, holder };
}

async function flush(times = 10) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function boot(options) {
  installPage();
  const { calls, holder } = stubFetch(options);
  const mod = await import('../src/people/review.js');
  await flush();
  return {
    calls,
    holder,
    REVIEW_SHORTCUTS: mod.REVIEW_SHORTCUTS,
    onKeydown: mod.onKeydown,
    syncVisualViewport: mod.syncVisualViewport,
    resetStageScroll: mod.resetStageScroll,
  };
}

const pillIds = () =>
  Array.from(document.querySelectorAll('#prSuggestions [data-pill]')).map((p) => p.dataset.pill);

// The module's own handler, driven directly: a real keydown on the document would
// also be answered by every earlier import of the module in this file.
function press(onKeydown, key, target = document) {
  let prevented = false;
  onKeydown({
    key,
    target,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    preventDefault: () => { prevented = true; },
  });
  return prevented;
}

describe('people review queue', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.resetModules();
  });

  it('shows the biggest unnamed cluster with its crops, folders and counters', async () => {
    const { calls } = await boot();

    // The queue is one unnamed cluster, biggest first, with a deeper crop strip.
    expect(calls[0]).toBe('/api/people?named=false&limit=1&offset=0&samples=8');
    expect(document.getElementById('prLoading').classList.contains('hidden')).toBe(true);
    expect(document.getElementById('prCard').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('prDone').classList.contains('hidden')).toBe(true);

    expect(document.getElementById('prMeta').textContent)
      .toBe('Cluster #12 · 34 faces · 9 files');
    expect(document.querySelectorAll('#prCrops img')).toHaveLength(3);
    // Your own cluster ids and folder paths are data, not markup.
    expect(document.getElementById('prFolders').textContent).toContain('Iceland 2023/Day 4');
    expect(document.getElementById('prFolders').innerHTML).toContain('Goa &lt;script&gt;/Day 1');
    expect(document.getElementById('prFolders').innerHTML).not.toContain('<script>');
    expect(document.getElementById('prStats').textContent).toContain('3 to review');
    expect(document.getElementById('prStats').textContent).toContain('8 named');
    expect(document.getElementById('prStats').textContent).toContain('4 dismissed');
  });

  it('offers the likely people as name pills, ranked for this cluster', async () => {
    const { calls } = await boot();

    // The pills are fetched per cluster and ranked by the folders they share.
    const pillQuery = calls.find((u) => u.includes('named=true'));
    expect(pillQuery).toContain('near=12');
    expect(pillQuery).toContain(`limit=${PILLS}`);
    expect(pillQuery).toContain('samples=1');

    const pills = Array.from(document.querySelectorAll('#prSuggestions [data-pill]'));
    expect(pills).toHaveLength(PILLS);
    // Best guess first, and a name is user input: escaped, never parsed as markup.
    expect(pills[0].textContent).toContain('Anna <script>');
    expect(pills[0].innerHTML).toContain('Anna &lt;script&gt;');
    expect(pills[0].innerHTML).not.toContain('<script>');
    expect(pills[0].textContent).toContain('1');
    expect(pills.map((p) => p.dataset.pill)).toEqual(['1', '2', '3', '4', '5', '6']);
    // Why the first one is on top is in its tooltip.
    expect(pills[0].title).toContain('Iceland 2023/Day 4');

    // The rest is one tap away rather than missing (8 named, 6 shown).
    const more = document.querySelector('#prSuggestions [data-more]');
    expect(more).toBeTruthy();
    expect(more.textContent).toContain('2');
  });

  it('matches from a pill straight away', async () => {
    const { calls } = await boot();

    document.querySelector('#prSuggestions [data-pill="2"]').click();
    await flush();

    const merged = JSON.parse(global.fetch.mock.calls.find((c) => c[0] === '/api/people/merge')[1].body);
    // The named person is the target; the reviewed cluster is the only source.
    expect(merged).toEqual({ target_id: 2, source_ids: [12] });
    expect(calls.filter((u) => u.includes('named=false&limit=1'))).toHaveLength(2);
    expect(document.getElementById('prMatchModal').classList.contains('hidden')).toBe(true);
  });

  it('names the cluster as a new person and advances the queue', async () => {
    const { calls, holder } = await boot();
    document.getElementById('prNameInput').value = 'Anna';
    holder.queue = { ...NEXT, people: [], total: 2 };

    document.getElementById('prNameBtn').click();
    await flush();

    expect(calls.filter((u) => u === '/api/people/rename')).toHaveLength(1);
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/people/rename',
      expect.objectContaining({ method: 'POST' })
    );
    // The action is followed by a fresh look at the queue, not a local edit.
    expect(calls.filter((u) => u.includes('named=false&limit=1'))).toHaveLength(2);
  });

  it('creates the person on Enter and refuses an empty name', async () => {
    const { calls } = await boot();
    const input = document.getElementById('prNameInput');

    input.value = '   ';
    input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await flush();
    expect(calls.filter((u) => u === '/api/people/rename')).toHaveLength(0);

    input.value = 'Anna';
    input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await flush();
    expect(calls.filter((u) => u === '/api/people/rename')).toHaveLength(1);
  });

  it('ignores a whole cluster straight away, with no popup', async () => {
    const { calls } = await boot();

    // "Ignore" is one tap/keystroke: no confirm dialog, no toast — the card
    // simply advances to the next cluster.
    const confirmSpy = vi.spyOn(window, 'confirm');
    document.getElementById('prIgnoreBtn').click();
    await flush();

    expect(confirmSpy).not.toHaveBeenCalled();
    expect(calls.filter((u) => u === '/api/people/dismiss')).toHaveLength(1);
    const dismissed = JSON.parse(global.fetch.mock.calls.find((c) => c[0] === '/api/people/dismiss')[1].body);
    expect(dismissed).toEqual({ person_id: 12 });
    expect(calls.filter((u) => u.includes('named=false&limit=1'))).toHaveLength(2);
    confirmSpy.mockRestore();
  });

  it('opens the full list from "More…" and matches from a row', async () => {
    const { calls } = await boot();

    document.querySelector('#prSuggestions [data-more]').click();
    await flush();

    expect(document.getElementById('prMatchModal').classList.contains('hidden')).toBe(false);
    const query = calls.find((u) => u.includes('limit=30'));
    expect(query).toContain('named=true');
    expect(query).toContain('near=12');
    expect(query).toContain('samples=3');

    const rows = Array.from(document.querySelectorAll('#prMatchList [data-match]'));
    expect(rows).toHaveLength(NAMED_NAMES.length);
    expect(rows.map((r) => r.dataset.match)).toEqual(['1', '2', '3', '4', '5', '6', '7', '8']);
    expect(rows[0].innerHTML).toContain('Anna &lt;script&gt;');
    expect(rows[0].innerHTML).not.toContain('<script>');
    // The suggestion says why it is on top.
    expect(rows[0].textContent).toContain('Iceland 2023/Day 4');
    expect(rows[1].textContent).not.toContain('Iceland 2023/Day 4');

    rows[0].click();
    await flush();
    const merged = JSON.parse(global.fetch.mock.calls.find((c) => c[0] === '/api/people/merge')[1].body);
    expect(merged).toEqual({ target_id: 1, source_ids: [12] });
    expect(document.getElementById('prMatchModal').classList.contains('hidden')).toBe(true);
    expect(calls.filter((u) => u.includes('named=false&limit=1'))).toHaveLength(2);
  });

  it('filters the full list by folder path through the server', async () => {
    const { calls } = await boot();
    document.querySelector('#prSuggestions [data-more]').click();
    await flush();

    const box = document.getElementById('prMatchSearch');
    box.value = 'Iceland 2023';
    box.dispatchEvent(new window.Event('input', { bubbles: true }));
    // Debounced: nothing fires while the typing is still going.
    expect(calls.some((u) => u.includes('folder='))).toBe(false);

    await new Promise((resolve) => setTimeout(resolve, 400));
    await flush();
    const filtered = calls.filter((u) => u.includes('folder='));
    expect(filtered).toHaveLength(1);
    expect(filtered[0]).toContain(`folder=${encodeURIComponent('Iceland 2023')}`);
    expect(filtered[0]).toContain('named=true');
    expect(filtered[0]).toContain('limit=30');
    // The card's own pill list is not the filtered one.
    expect(pillIds()).toHaveLength(PILLS);
  });

  it('drives the whole flow from the keyboard', async () => {
    const { calls, onKeydown } = await boot();

    // N focuses the name box, where typing must stay typing.
    expect(press(onKeydown, 'n')).toBe(true);
    expect(document.activeElement).toBe(document.getElementById('prNameInput'));
    expect(calls.filter((u) => u === '/api/people/rename')).toHaveLength(0);
    // A letter typed into the box is not a shortcut.
    expect(press(onKeydown, 'x', document.getElementById('prNameInput'))).toBe(false);
    expect(calls.filter((u) => u === '/api/people/dismiss')).toHaveLength(0);

    // A digit matches the pill carrying it, with no sheet in the way.
    expect(press(onKeydown, '3')).toBe(true);
    await flush();
    expect(global.fetch.mock.calls.some(
      (c) => c[0] === '/api/people/merge' && JSON.parse(c[1].body).target_id === 3
    )).toBe(true);

    // M opens the full list; / jumps to its folder box, 1 matches the top row.
    press(onKeydown, 'm');
    await flush();
    expect(document.getElementById('prMatchModal').classList.contains('hidden')).toBe(false);
    expect(press(onKeydown, '/')).toBe(true);
    expect(document.activeElement).toBe(document.getElementById('prMatchSearch'));
    press(onKeydown, '1');
    await flush();
    expect(global.fetch.mock.calls.some(
      (c) => c[0] === '/api/people/merge' && JSON.parse(c[1].body).target_id === 1
    )).toBe(true);
    expect(document.getElementById('prMatchModal').classList.contains('hidden')).toBe(true);

    // ? opens the key reference, Esc closes it.
    expect(press(onKeydown, '?')).toBe(true);
    expect(document.getElementById('prKeysModal').classList.contains('hidden')).toBe(false);
    press(onKeydown, 'Escape');
    expect(document.getElementById('prKeysModal').classList.contains('hidden')).toBe(true);

    // X is the ignore key: straight away, no dialog.
    const confirmSpy = vi.spyOn(window, 'confirm');
    expect(press(onKeydown, 'x')).toBe(true);
    await flush();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(calls.filter((u) => u === '/api/people/dismiss')).toHaveLength(1);
    confirmSpy.mockRestore();
  });

  it('documents every key it binds, from one table', async () => {
    const { REVIEW_SHORTCUTS, onKeydown } = await boot();
    const entries = REVIEW_SHORTCUTS.flatMap((g) => g.entries);
    // '?' is the only way to discover the reference, so it must be in it.
    expect(entries.some((e) => e.keys.includes('?'))).toBe(true);
    for (const key of ['M', 'N', 'X', 'Enter', '1']) {
      expect(entries.some((e) => e.keys.includes(key))).toBe(true);
    }
    for (const group of REVIEW_SHORTCUTS) {
      expect(group.entries.length).toBeGreaterThan(0);
      for (const entry of group.entries) {
        expect(entry.keys.length).toBeGreaterThan(0);
        expect(entry.description.length).toBeGreaterThan(4);
      }
    }

    // The reference renders straight from that table…
    press(onKeydown, '?');
    const sections = document.querySelectorAll('#prKeysGroups section');
    expect(sections.length).toBe(REVIEW_SHORTCUTS.length);
    expect(document.getElementById('prKeysGroups').textContent).toContain('numbered person pill');

    // …and never advertises a letter the module does not bind.
    for (const letter of entries.flatMap((e) => e.keys).filter((k) => /^[A-Za-z]$/.test(k))) {
      expect(reviewSource).toMatch(new RegExp(`['"]${letter.toLowerCase()}['"]`));
    }
  });

  it('says there is nobody to match against before anything is named', async () => {
    const nobodyNamed = { ...NEXT, counts: { ...NEXT.counts, named: 0 } };
    const empty = { ...NAMED, people: [], total: 0 };
    const { calls, onKeydown } = await boot({ queue: nobodyNamed, named: empty });

    expect(pillIds()).toHaveLength(0);
    expect(document.querySelector('#prSuggestions [data-more]')).toBeNull();
    expect(document.getElementById('prSuggestions').textContent)
      .toContain('No named people yet');

    // M is refused with a hint rather than opening an empty sheet.
    press(onKeydown, 'm');
    await flush();
    expect(calls.some((u) => u.includes('limit=30'))).toBe(false);
    expect(document.getElementById('prMatchModal').classList.contains('hidden')).toBe(true);
  });

  it('explains itself when the named people vanish under it', async () => {
    // The card says there are named people, but by the time the sheet asks there
    // are none left (renamed or merged away elsewhere): it must say so.
    const { calls } = await boot({ picker: { ...NAMED, people: [], total: 0 } });

    expect(pillIds()).toHaveLength(PILLS);
    document.querySelector('#prSuggestions [data-more]').click();
    await flush();

    expect(calls.some((u) => u.includes('limit=30'))).toBe(true);
    expect(document.getElementById('prMatchList').textContent).toContain('No named people yet');
  });

  it('walks into the done state once the queue is empty', async () => {
    const { holder } = await boot();
    holder.queue = { ...NEXT, people: [], total: 0 };

    document.getElementById('prNameInput').value = 'Anna';
    document.getElementById('prNameBtn').click();
    await flush();

    expect(document.getElementById('prCard').classList.contains('hidden')).toBe(true);
    expect(document.getElementById('prDone').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('prDoneText').textContent).toContain('4 faces dismissed');
    expect(document.getElementById('prStats').textContent).toContain('0 to review');
  });

  it('offers a retry instead of a blank page when the API fails', async () => {
    const { calls, holder } = await boot({ fail: true });
    const error = document.getElementById('prError');
    expect(error.classList.contains('hidden')).toBe(false);
    expect(error.textContent).toContain('Could not load the next cluster');
    expect(document.getElementById('prCard').classList.contains('hidden')).toBe(true);

    const before = calls.length;
    holder.fail = false;
    error.querySelector('[data-review-retry]').click();
    await flush();
    expect(calls.length).toBeGreaterThan(before);
    expect(document.getElementById('prCard').classList.contains('hidden')).toBe(false);
    expect(pillIds()).toHaveLength(PILLS);
  });

  it('keeps the card off-screen between two clusters', async () => {
    const { holder } = await boot();
    // The next cluster is slow: while the fetch is in flight neither the stale
    // card nor the "nothing left" state may be painted.
    let release;
    global.fetch = vi.fn((url) => {
      const target = String(url);
      if (target.includes('named=false')) {
        return new Promise((resolve) => {
          release = () => resolve(jsonResponse({ ...holder.queue, people: [], total: 0 }));
        });
      }
      return Promise.resolve(jsonResponse(holder.named));
    });
    document.getElementById('prNameInput').value = 'Anna';
    document.getElementById('prNameBtn').click();
    await flush();

    expect(document.getElementById('prCard').classList.contains('hidden')).toBe(true);
    expect(document.getElementById('prDone').classList.contains('hidden')).toBe(true);
    expect(document.getElementById('prLoading').classList.contains('hidden')).toBe(false);

    release();
    await flush();
    expect(document.getElementById('prDone').classList.contains('hidden')).toBe(false);
  });

  it('keeps the phone layout from overflowing sideways', () => {
    const html = fs.readFileSync(path.join(templatesDir, 'people_review.html'), 'utf8');
    expect(html).toMatch(/<main class="[^"]*overflow-x-hidden/);
    const stats = html.match(/<div id="prStats" class="([^"]*)"/);
    expect(stats).toBeTruthy();
    expect(stats[1]).toContain('min-w-0');
    expect(stats[1]).not.toContain('shrink-0');
    // The pills wrap instead of pushing the card wider than the phone.
    const pills = html.match(/<div id="prSuggestions" class="([^"]*)"/);
    expect(pills).toBeTruthy();
    expect(pills[1]).toContain('flex-wrap');
    expect(pills[1]).toContain('min-w-0');
    // Both sheets are phone full-height sheets with a grabber.
    expect(html.match(/class="ml-sheet fixed/g)).toHaveLength(2);
    expect(html.match(/data-sheet-grabber/g)).toHaveLength(2);
  });

  it('sizes the card to the visual viewport so the keyboard cannot scroll it', async () => {
    const originalMM = window.matchMedia;
    const originalVV = window.visualViewport;
    // A phone with the keyboard up: the visual viewport is short, the layout one
    // is not (which is exactly what makes iOS scroll the stage).
    window.matchMedia = (query) => ({
      matches: String(query).includes('max-width'),
      media: String(query),
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
    });
    window.visualViewport = {
      height: 420,
      addEventListener() {},
      removeEventListener() {},
    };
    try {
      const { syncVisualViewport, resetStageScroll } = await boot();

      // The stage gets the visible height minus whatever sits above it (the
      // header), so the name box stays above the keyboard.
      syncVisualViewport();
      const root = document.documentElement;
      const applied = root.style.getPropertyValue('--pr-vv-height');
      expect(applied).toMatch(/^\d+px$/);
      expect(parseInt(applied, 10)).toBeLessThanOrEqual(420);
      expect(parseInt(applied, 10)).toBeGreaterThanOrEqual(240);

      // …and any scroll a browser did anyway is undone.
      const stage = document.querySelector('main');
      stage.scrollTop = 180;
      resetStageScroll();
      expect(stage.scrollTop).toBe(0);

      // A short visual viewport means a keyboard is up: the face strip steps
      // aside (CSS) so the name box keeps its room above it.
      expect(document.documentElement.classList.contains('pr-kb')).toBe(true);

      // Back to the full viewport: the strip returns.
      window.visualViewport.height = window.innerHeight;
      syncVisualViewport();
      expect(document.documentElement.classList.contains('pr-kb')).toBe(false);

      // Desktop lays out from the document: the override is dropped.
      window.matchMedia = () => ({
        matches: false,
        media: '',
        addEventListener() {},
        removeEventListener() {},
        addListener() {},
        removeListener() {},
      });
      syncVisualViewport();
      expect(root.style.getPropertyValue('--pr-vv-height')).toBe('');
      expect(document.documentElement.classList.contains('pr-kb')).toBe(false);
    } finally {
      window.matchMedia = originalMM;
      window.visualViewport = originalVV;
      document.documentElement.style.removeProperty('--pr-vv-height');
    }
  });

  it('fits and fills one phone screen, with no toasts', () => {
    const html = fs.readFileSync(path.join(templatesDir, 'people_review.html'), 'utf8');
    // Nothing may sit under the ignore button: the card ends there.
    const ignore = html.indexOf('id="prIgnoreBtn"');
    expect(ignore).toBeGreaterThan(-1);
    const afterIgnore = html.slice(ignore);
    expect(afterIgnore).not.toContain('prHint');
    expect(afterIgnore).not.toContain('id="prKeysBtn"');
    expect(afterIgnore).not.toContain('Nothing is uploaded anywhere');
    // The card closes right after the actions row.
    expect(afterIgnore.indexOf('</section>')).toBeLessThan(afterIgnore.indexOf('</main>'));
    // The page carries no toast host, and the module raises none.
    expect(html).not.toContain('toastContainer');
    expect(reviewSource).not.toContain('showToast');
    // The pieces that keep it on one screen: the cover is desktop-only, the
    // chips are desktop-only, the crops go three-up and grow to fill.
    expect(html).toMatch(/\.pr-cover \{[^}]*display: none/);
    expect(html).toMatch(/@media \(min-width: 640px\) \{ \.pr-cover \{ display: block; \} \}/);
    expect(html).toMatch(/id="prCrops" class="pr-crops grid grid-cols-3 sm:grid-cols-8/);
    expect(html).toMatch(/id="prStats" class="hidden sm:flex/);
    expect(html).toMatch(/\.pr-stage \{[^}]*padding-bottom: max\(0\.75rem, var\(--sab/);
    // Phone: the stage is one screen tall (following the visual viewport, so a
    // phone keyboard cannot force a scroll), the card fills it and the crop strip
    // is what takes the slack — no empty space left, nothing to scroll.
    expect(html).toMatch(/@media \(max-width: 639px\) \{[\s\S]*?\.pr-stage \{ height: var\(--pr-vv-height, 100%\)/);
    expect(html).toMatch(/\.pr-fill \{ flex: 1 1 0%; min-height: 0; \}/);
    expect(html).toMatch(/\.pr-crops \{ grid-auto-rows: minmax\(0, 1fr\); \}/);
    expect(html).toMatch(/\.pr-crops \.pr-face \{ height: 100%; \}/);
    expect(html).toMatch(/\.pr-actions \{ margin-top: auto; \}/);
    expect(html).toMatch(/html\.pr-kb \.pr-crops-block \{ display: none; \}/);
    // The fill chain needs the panel to stretch its column (a flex-start column
    // would size to its content and push the card past the fold again).
    expect(html).toMatch(/class="flex items-stretch gap-3 sm:gap-4[^"]*pr-fill"/);
    // The card, its panel, the column, the crop block and the crop grid all take
    // the slack, plus the rule itself.
    expect(html.match(/\bpr-fill\b/g)).toHaveLength(6);
  });
});
