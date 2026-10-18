import { describe, it, expect, vi, beforeEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// The /people page: cluster grid, naming, curation (merge/split/dismiss),
// person media with video moments, and the disabled state. Fancybox and the
// theme customizer are absent in happy-dom, so the page must degrade quietly.

const here = path.dirname(fileURLToPath(import.meta.url));
const templatesDir = path.resolve(here, '../../templates');

function expandIncludes(html, dir) {
  return html.replace(/\{\{\s*include\s+(['"])(.*?)\1\s*\}\}/g, (_m, _q, file) => {
    const filePath = path.resolve(dir, file);
    return expandIncludes(fs.readFileSync(filePath, 'utf8'), path.dirname(filePath));
  });
}

function installPage() {
  const html = expandIncludes(fs.readFileSync(path.join(templatesDir, 'people.html'), 'utf8'), templatesDir);
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
  total_files: 10,
  pending_count: 0,
  processed_count: 10,
  percent: 100,
  current_file: '',
  faces_total: 4,
  people_total: 2,
  unnamed_people: 1,
  dismissed_faces: 1,
  is_paused: false,
  cache_files: 4,
  cache_bytes: 4096,
  cache_size_formatted: '4.0 KB',
  cache_max_bytes: 2147483648,
  cache_max_size_formatted: '2.0 GB',
  threshold: 0.36,
  threshold_default: 0.36,
  threshold_preset: 'balanced',
  threshold_presets: { strict: 0.28, balanced: 0.36, loose: 0.46 },
};

const PEOPLE = {
  people: [
    {
      id: 1,
      name: 'Anna <script>',
      face_count: 3,
      media_count: 2,
      cover_url: '/api/people/crop?face_id=1&v=face_abc.jpg',
      sample_urls: ['/api/people/crop?face_id=1&v=face_abc.jpg'],
      is_named: true,
    },
    { id: 2, name: null, face_count: 1, media_count: 1, cover_url: '/api/people/crop?face_id=2&v=face_def.jpg', sample_urls: [], is_named: false },
  ],
  counts: { people: 2, named: 1, unnamed: 1, faces: 4, dismissed: 1, files: 3 },
  total: 2,
  limit: 120,
  offset: 0,
  status: STATUS,
};

const PERSON = {
  id: 1,
  name: 'Anna',
  created_at: 1,
  face_count: 3,
  media_total: 1,
  truncated: false,
  offset: 0,
  limit: 60,
  sort: 'count',
  faces: [
    { id: 1, crop_url: '/api/people/crop?face_id=1&v=a.jpg', frame_time: null, name: 'shot.jpg' },
    { id: 2, crop_url: '/api/people/crop?face_id=2&v=b.jpg', frame_time: 12.5, name: 'clip.mp4' },
  ],
  media: [
    {
      name: 'clip.mp4',
      path: '/media/trips/clip.mp4',
      type: 'video',
      width: 1920,
      height: 1080,
      duration: 42,
      size_formatted: '12.0 MB',
      thumb_url: '/api/backup/thumbnail?path=%2Fmedia%2Ftrips%2Fclip.mp4',
      media_url: '/api/backup/media?path=%2Fmedia%2Ftrips%2Fclip.mp4',
      frames: 2,
      moments: [
        { frame_time: 0, crop_url: '/api/people/crop?face_id=2&v=b.jpg' },
        { frame_time: 12.5, crop_url: '/api/people/crop?face_id=3&v=c.jpg' },
      ],
    },
  ],
};

function jsonResponse(payload) {
  return { ok: true, status: 200, json: () => Promise.resolve(payload) };
}

function stubFetch({ status = STATUS, people = PEOPLE, person = PERSON, fail = false, autoMatch = null } = {}) {
  const calls = [];
  const posts = [];
  global.fetch = vi.fn((url, options) => {
    calls.push(String(url));
    if (options && options.body) {
      try {
        posts.push({ url: String(url), body: JSON.parse(options.body) });
      } catch (e) {
        // not JSON (form data): nothing to inspect
      }
    }
    if (fail) return Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({ detail: 'boom' }) });
    const target = String(url);
    if (target.startsWith('/api/people/status')) return Promise.resolve(jsonResponse(status));
    if (target.startsWith('/api/people/auto-match')) {
      // The endpoint answers the dry run and the real pass with the same shape;
      // the client only ever reads `matched`/counts from them.
      const body = JSON.parse((options && options.body) || '{}');
      const canned = autoMatch || {};
      const payload = body.dry_run
        ? canned.preview || { matched: 0, faces: 0, people: 0, remaining_unnamed: 0, threshold: 0.36, by_person: [] }
        : canned.result || { success: true, removed: 0, faces: 0, people: 0 };
      return Promise.resolve(jsonResponse(payload));
    }
    if (/^\/api\/people\/\d+/.test(target)) return Promise.resolve(jsonResponse(person));
    if (target.startsWith('/api/people')) {
      // Mirror the server: paged, newest-cluster ordering aside, with the
      // optional named=true/false filter the merge picker relies on.
      const q = new URL(target, 'http://localhost').searchParams;
      const limit = Number(q.get('limit') || 120);
      const offset = Number(q.get('offset') || 0);
      const named = q.get('named');
      const all = (people.people || []).filter((p) => {
        if (named === 'false') return !p.is_named;
        if (named === 'true') return Boolean(p.is_named);
        return true;
      });
      return Promise.resolve(jsonResponse({
        ...people,
        people: all.slice(offset, offset + limit),
        total: all.length,
        limit,
        offset,
      }));
    }
    return Promise.resolve(jsonResponse({ success: true }));
  });
  // The request bodies the page sent, for tests that care what was posted
  // (the URL list stays a plain array of strings for the existing assertions).
  calls.posts = posts;
  return calls;
}

async function flush(times = 8) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function boot(options) {
  installPage();
  const calls = stubFetch(options);
  await import('../src/people/index.js');
  await flush();
  return calls;
}

describe('people page publisher', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.resetModules();
  });

  it('renders the cluster grid with counts and escaped names', async () => {
    const calls = await boot();

    expect(calls[0]).toContain('/api/people?limit=');
    expect(document.getElementById('ppLoading').classList.contains('hidden')).toBe(true);
    expect(document.getElementById('ppPeopleView').classList.contains('hidden')).toBe(false);

    const cards = document.querySelectorAll('#ppGrid [data-person]');
    expect(cards).toHaveLength(2);
    expect(cards[0].getAttribute('data-person')).toBe('1');
    // A person's name is user input: it must be escaped, never parsed as HTML.
    expect(cards[0].innerHTML).toContain('Anna &lt;script&gt;');
    expect(cards[0].innerHTML).not.toContain('<script>');
    expect(cards[1].textContent).toContain('Unnamed');
    expect(document.getElementById('ppCounts').textContent).toContain('2 people');
    expect(document.getElementById('ppCounts').textContent).toContain('1 named');
    expect(document.getElementById('ppMobileSubtitle').textContent).toContain('2 people');
    // The strictness control reflects the stored preset.
    const activePreset = document.querySelector('#ppPresets [data-preset="balanced"]');
    expect(activePreset.className).toContain('bg-emerald-600');
  });

  it('links to the review queue, badged with the unmatched count', async () => {
    await boot();

    const link = document.getElementById('ppReviewBtn');
    expect(link.getAttribute('href')).toBe('/people/review');
    expect(link.title).toContain('unnamed cluster');
    const badge = document.getElementById('ppReviewCount');
    expect(badge.classList.contains('hidden')).toBe(false);
    expect(badge.textContent).toBe('1');
  });

  it('says so on the review link when nothing is unmatched', async () => {
    await boot({ people: { ...PEOPLE, counts: { ...PEOPLE.counts, unnamed: 0 } } });

    expect(document.getElementById('ppReviewCount').classList.contains('hidden')).toBe(true);
    expect(document.getElementById('ppReviewBtn').title).toContain('every cluster is named');
  });

  it('shows the disabled notice and disables starting a scan', async () => {    await boot({ status: { ...STATUS, enabled: false, engine_available: false, engine_reason: 'OpenCV is not installed' } });

    const notice = document.getElementById('ppNotice');
    expect(notice.classList.contains('hidden')).toBe(false);
    expect(notice.textContent).toContain('FACE_RECOGNITION_ENABLED');
    expect(document.getElementById('ppScanAllBtn').disabled).toBe(true);
    expect(document.getElementById('ppPauseBtn').disabled).toBe(true);
  });

  it('explains a missing engine and still lists existing clusters', async () => {
    await boot({
      status: { ...STATUS, engine_available: false, engine_reason: 'Face models not downloaded (x.onnx)' },
    });
    const notice = document.getElementById('ppNotice');
    expect(notice.textContent).toContain('Face models not downloaded');
    expect(document.querySelectorAll('#ppGrid [data-person]')).toHaveLength(2);
  });

  it('opens a person, renders faces and media, and jumps to a video moment', async () => {
    await boot();

    document.querySelector('#ppGrid [data-person="1"]').click();
    await flush();

    expect(document.getElementById('ppPeopleView').classList.contains('hidden')).toBe(true);
    expect(document.getElementById('ppPersonView').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('ppPersonName').value).toBe('Anna');
    expect(document.getElementById('ppPersonMeta').textContent).toContain('3 faces');

    const faces = document.querySelectorAll('#ppFaces [data-face]');
    expect(faces).toHaveLength(2);
    // Selecting a face enables the split/dismiss actions (the strip re-renders,
    // so re-query rather than reusing the detached node).
    expect(document.getElementById('ppSplitBtn').disabled).toBe(true);
    faces[1].click();
    expect(document.querySelector('#ppFaces [data-face="2"]').getAttribute('aria-pressed')).toBe('true');
    expect(document.getElementById('ppSplitBtn').disabled).toBe(false);
    expect(document.getElementById('ppFaceSelCount').textContent).toBe('1 selected');

    const moments = document.querySelectorAll('#ppMedia [data-moment]');
    expect(moments).toHaveLength(2);
    expect(moments[1].textContent).toContain('0:12');
    expect(moments[1].getAttribute('data-time')).toBe('12.5');
    // Without Fancybox on the page, opening a moment must not throw.
    expect(() => moments[1].click()).not.toThrow();
  });

  it('renders crop and thumbnail URLs verbatim (never percent-encoded)', async () => {
    await boot();

    const cover = document.querySelector('#ppGrid [data-person="1"] img');
    expect(cover).toBeTruthy();
    expect(cover.getAttribute('src')).toBe('/api/people/crop?face_id=1&v=face_abc.jpg');
    expect(cover.getAttribute('src')).not.toContain('%2F');

    document.querySelector('#ppGrid [data-person="1"]').click();
    await flush();

    const faceImg = document.querySelector('#ppFaces [data-face="1"] img');
    expect(faceImg.getAttribute('src')).toBe('/api/people/crop?face_id=1&v=a.jpg');
    const mediaImg = document.querySelector('#ppMedia [data-media="0"] img');
    expect(mediaImg.getAttribute('src')).toBe('/api/backup/thumbnail?path=%2Fmedia%2Ftrips%2Fclip.mp4');
  });

  it('resets the stage scroll when switching between views', async () => {
    await boot();
    const stage = document.querySelector('main');
    const scrollSpy = vi.spyOn(stage, 'scrollTop', 'set');

    document.querySelector('#ppGrid [data-person="1"]').click();
    await flush();
    expect(scrollSpy).toHaveBeenCalledWith(0);

    scrollSpy.mockClear();
    document.getElementById('ppBackBtn').click();
    await flush();
    expect(scrollSpy).toHaveBeenCalledWith(0);
    // The document does not scroll on this page (the body is viewport-locked).
    expect(document.getElementById('ppPeopleView').classList.contains('hidden')).toBe(false);
    scrollSpy.mockRestore();
  });

  it('names a cluster through the API and repaints the grid', async () => {
    const calls = await boot();
    document.querySelector('#ppGrid [data-person="2"]').click();
    await flush();

    document.getElementById('ppPersonName').value = 'Bob';
    document.getElementById('ppSaveNameBtn').click();
    await flush();

    const renameCall = calls.find((u) => u === '/api/people/rename');
    expect(renameCall).toBeTruthy();
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/people/rename',
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('dismisses selected faces only after confirmation', async () => {
    const calls = await boot();
    document.querySelector('#ppGrid [data-person="1"]').click();
    await flush();

    document.querySelector('#ppFaces [data-face]').click();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    document.getElementById('ppSplitBtn').disabled = true; // no-op guard
    document.getElementById('ppDismissFacesBtn').click();
    await flush();
    expect(confirmSpy).toHaveBeenCalled();
    expect(calls.filter((u) => u === '/api/people/dismiss')).toHaveLength(0);

    confirmSpy.mockReturnValue(true);
    document.querySelector('#ppFaces [data-face="2"]').click();
    document.getElementById('ppDismissFacesBtn').click();
    await flush();
    expect(calls.filter((u) => u === '/api/people/dismiss')).toHaveLength(1);
    confirmSpy.mockRestore();
  });

  it('merges clusters through the modal', async () => {
    const calls = await boot();
    document.querySelector('#ppGrid [data-person="1"]').click();
    await flush();

    document.getElementById('ppMergeBtn').click();
    await flush();
    expect(document.getElementById('ppMergeModal').classList.contains('hidden')).toBe(false);
    const option = document.querySelector('#ppMergeList [data-merge="2"]');
    expect(option).toBeTruthy();
    option.click();
    expect(document.getElementById('ppMergeCount').textContent).toBe('1 selected');

    document.getElementById('ppMergeConfirmBtn').click();
    await flush();
    expect(calls.filter((u) => u === '/api/people/merge')).toHaveLength(1);
    expect(document.getElementById('ppMergeModal').classList.contains('hidden')).toBe(true);
  });

  it('offers only unnamed clusters in the merge picker, with their faces', async () => {
    const named = {
      id: 1, name: 'Anna', face_count: 9, media_count: 9, is_named: true,
      cover_url: '/api/people/crop?face_id=1&v=a.jpg',
      sample_urls: ['/api/people/crop?face_id=1&v=a.jpg'],
    };
    const unnamed = Array.from({ length: 61 }, (_, i) => ({
      id: 100 + i,
      name: null,
      face_count: 5,
      media_count: 4,
      is_named: false,
      cover_url: `/api/people/crop?face_id=${100 + i}&v=c${i}.jpg`,
      sample_urls: [
        `/api/people/crop?face_id=${100 + i}&v=c${i}.jpg`,
        `/api/people/crop?face_id=${200 + i}&v=d${i}.jpg`,
      ],
    }));
    // The server ranks same-folder clusters first and annotates them.
    unnamed[0] = {
      ...unnamed[0],
      shared_folders: 1,
      shared_faces: 5,
      shared_folder_names: ['Iceland 2023'],
    };
    const calls = stubFetch({ people: { ...PEOPLE, people: [named, ...unnamed], total: 62 } });
    installPage();
    await import('../src/people/index.js');
    await flush();

    // Open the named cluster: the picker must not offer it (nor any other named
    // person) as a merge source.
    document.querySelector('#ppGrid [data-person="1"]').click();
    await flush();
    document.getElementById('ppMergeBtn').click();
    await flush();

    expect(calls.some((u) => u.includes('/api/people?') && u.includes('named=false'))).toBe(true);
    // ...and ranked against the cluster it was opened from.
    expect(calls.some((u) => u.includes('named=false') && u.includes('near=1'))).toBe(true);
    const rows = Array.from(document.querySelectorAll('#ppMergeList [data-merge]'));
    expect(rows).toHaveLength(60);
    expect(rows.map((r) => r.dataset.merge)).not.toContain('1');
    expect(document.getElementById('ppMergeList').textContent).not.toContain('Anna');
    // Each row shows the cluster's faces, not just one cover.
    expect(rows[0].querySelectorAll('img')).toHaveLength(2);
    // A candidate filed in the same folder says so, so the ranking is legible.
    expect(rows[0].textContent).toContain('Iceland 2023');
    expect(rows[1].textContent).not.toContain('Iceland 2023');

    // The rest is one tap away rather than missing.
    const more = document.querySelector('#ppMergeList [data-merge-more]');
    expect(more).toBeTruthy();
    more.click();
    await flush();
    expect(calls.some((u) => u.includes('offset=60') && u.includes('named=false'))).toBe(true);
    expect(document.querySelectorAll('#ppMergeList [data-merge]')).toHaveLength(61);
    expect(document.querySelector('#ppMergeList [data-merge-more]')).toBeNull();
  });

  it('filters the merge candidates by folder path, through the server', async () => {
    const target = {
      id: 1, name: 'Anna', face_count: 3, media_count: 2, is_named: true,
      cover_url: null, sample_urls: [],
    };
    const inFolder = {
      id: 7, name: null, face_count: 4, media_count: 3, is_named: false,
      cover_url: '/api/people/crop?face_id=7&v=g.jpg',
      sample_urls: ['/api/people/crop?face_id=7&v=g.jpg'],
      match_folders: ['Iceland 2023/Day 4'],
    };
    const calls = stubFetch({ people: { ...PEOPLE, people: [target, inFolder], total: 2 } });
    installPage();
    await import('../src/people/index.js');
    await flush();
    document.querySelector('#ppGrid [data-person="1"]').click();
    await flush();
    document.getElementById('ppMergeBtn').click();
    await flush();

    const box = document.getElementById('ppMergeSearch');
    expect(box.getAttribute('placeholder')).toContain('folder path');
    box.value = 'Iceland 2023/Day 4';
    box.dispatchEvent(new Event('input', { bubbles: true }));
    // Debounced: no request fires while the typing is still going.
    expect(calls.some((u) => u.includes('folder='))).toBe(false);

    await new Promise((resolve) => setTimeout(resolve, 400));
    await flush();
    const filtered = calls.filter((u) => u.includes('folder='));
    expect(filtered).toHaveLength(1);
    expect(filtered[0]).toContain(`folder=${encodeURIComponent('Iceland 2023/Day 4')}`);
    expect(filtered[0]).toContain('near=1');
    expect(filtered[0]).toContain('named=false');
    // The matched folder is shown on the row, so the result explains itself.
    expect(document.querySelector('#ppMergeList [data-merge="7"]').textContent)
      .toContain('Iceland 2023/Day 4');

    // Clearing the box searches the whole unnamed set again.
    box.value = '';
    box.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 400));
    await flush();
    expect(calls[calls.length - 1]).not.toContain('folder=');
    expect(calls[calls.length - 1]).toContain('near=1');
  });

  it('says so when every other cluster is named', async () => {
    const onlyNamed = [
      { id: 1, name: 'Anna', face_count: 3, media_count: 2, is_named: true, cover_url: null, sample_urls: [] },
      { id: 2, name: 'Bob', face_count: 1, media_count: 1, is_named: true, cover_url: null, sample_urls: [] },
    ];
    await boot({ people: { ...PEOPLE, people: onlyNamed, total: 2 } });
    document.querySelector('#ppGrid [data-person="1"]').click();
    await flush();
    document.getElementById('ppMergeBtn').click();
    await flush();

    expect(document.querySelectorAll('#ppMergeList [data-merge]')).toHaveLength(0);
    expect(document.getElementById('ppMergeList').textContent).toContain('every other cluster is named');
  });

  it('queues scans and re-groups from the toolbar', async () => {
    const calls = await boot();

    document.getElementById('ppScanAllBtn').click();
    await flush();
    expect(calls.filter((u) => u === '/api/people/scan')).toHaveLength(1);

    document.getElementById('ppScanFolder').value = '/media/trips';
    document.getElementById('ppScanFolderBtn').click();
    await flush();
    expect(calls.filter((u) => u === '/api/people/scan')).toHaveLength(2);

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    document.getElementById('ppReclusterBtn').click();
    await flush();
    expect(calls.filter((u) => u === '/api/people/recluster')).toHaveLength(1);
    confirmSpy.mockRestore();

    document.querySelector('#ppPresets [data-preset="loose"]').click();
    await flush();
    expect(calls.filter((u) => u === '/api/people/threshold')).toHaveLength(1);
    expect(localStorage.getItem('pp_strictness')).toBe('loose');
  });

  it('previews an auto-match, then applies it only after confirmation', async () => {
    const preview = {
      success: true,
      threshold: 0.36,
      matched: 488,
      faces: 1432,
      people: 20,
      remaining_unnamed: 14566,
      by_person: [
        { person_id: 263, name: 'Dare Taylor', clusters: 109, faces: 338 },
        { person_id: 533, name: 'Emily black', clusters: 75, faces: 286 },
        { person_id: 12711, name: 'Piper blush', clusters: 54, faces: 217 },
        { person_id: 7124, name: 'Elise Laurenne', clusters: 32, faces: 133 },
      ],
    };
    const result = { success: true, removed: 488, faces: 1432, people: 20, remaining_unnamed: 14566 };
    const calls = await boot({ autoMatch: { preview, result } });
    const autoPosts = () => calls.posts.filter((p) => p.url === '/api/people/auto-match');

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    document.getElementById('ppAutoMatchBtn').click();
    await flush();

    // The preview is a dry run: it asks first, and shows what would move.
    expect(autoPosts()).toHaveLength(1);
    expect(autoPosts()[0].body.dry_run).toBe(true);
    expect(confirmSpy.mock.calls[0][0]).toContain('488');
    expect(confirmSpy.mock.calls[0][0]).toContain('Dare Taylor +338');
    expect(calls.filter((u) => u === '/api/people/auto-match')).toHaveLength(1);

    confirmSpy.mockReturnValue(true);
    document.getElementById('ppAutoMatchBtn').click();
    await flush();
    expect(autoPosts()).toHaveLength(3);
    expect(autoPosts()[2].body.dry_run).toBeUndefined();
    confirmSpy.mockRestore();
  });

  it('says nothing to auto-match when no cluster is close enough', async () => {
    const calls = await boot({
      autoMatch: { preview: { success: true, threshold: 0.28, matched: 0, faces: 0, people: 0, remaining_unnamed: 15054 } },
    });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    document.getElementById('ppAutoMatchBtn').click();
    await flush();

    expect(calls.filter((u) => u === '/api/people/auto-match')).toHaveLength(1);
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('prefills the scan folder from a ?folder= deep link', async () => {
    installPage();
    stubFetch();
    const original = window.location.search;
    try {
      window.history.replaceState({}, '', '/people?folder=/media/albums');
      await import('../src/people/index.js');
      await flush();
      expect(document.getElementById('ppScanFolder').value).toBe('/media/albums');
    } finally {
      window.history.replaceState({}, '', `/people${original}`);
    }
  });

  it('keeps the mobile layout from overflowing sideways', () => {
    const html = fs.readFileSync(path.join(templatesDir, 'people.html'), 'utf8');
    // The stage clips horizontal overflow, and the counts chips shrink/wrap
    // instead of pushing the page wider than the phone.
    expect(html).toMatch(/<main class="[^"]*overflow-x-hidden/);
    const counts = html.match(/<div id="ppCounts" class="([^"]*)"/);
    expect(counts).toBeTruthy();
    expect(counts[1]).not.toContain('shrink-0');
    expect(counts[1]).toContain('min-w-0');
    const progress = html.match(/<span id="ppProgressText" class="([^"]*)"/);
    expect(progress[1]).toContain('truncate');
  });

  it('renders the merge sheet with a grabber and big cover thumbnails', async () => {
    await boot();
    document.querySelector('#ppGrid [data-person="1"]').click();
    await flush();
    document.getElementById('ppMergeBtn').click();
    await flush();

    const wrap = document.getElementById('ppMergeModal');
    expect(wrap.classList.contains('ml-sheet')).toBe(true);
    const card = wrap.querySelector('.ml-sheet-card');
    expect(card).toBeTruthy();
    expect(card.querySelector('[data-sheet-grabber]')).toBeTruthy();
    // Covers are readable on a phone, not 44px specks.
    const cover = card.querySelector('#ppMergeList img');
    expect(cover.className).toContain('w-16');
    expect(cover.className).toContain('h-16');
  });

  it('dismisses the merge sheet with a downward swipe on the grabber', async () => {
    const originalMM = window.matchMedia;
    // Happy-dom reports no media matches by default; the sheet is a phone-only
    // behaviour, so answer the width query as a 412px viewport would.
    window.matchMedia = (query) => ({
      matches: String(query).includes('max-width'),
      media: String(query),
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
    });
    try {
      await boot();
      document.querySelector('#ppGrid [data-person="1"]').click();
      await flush();
      document.getElementById('ppMergeBtn').click();
      await flush();
      const wrap = document.getElementById('ppMergeModal');
      const card = wrap.querySelector('.ml-sheet-card');
      const grabber = card.querySelector('[data-sheet-grabber]');

      const fire = (type, clientY) => card.dispatchEvent(
        new MouseEvent(type, { clientY, bubbles: true, cancelable: true })
      );

      fire('pointerdown', 100); // not on the grabber: ignored
      fire('pointerup', 400);
      expect(wrap.classList.contains('hidden')).toBe(false);

      grabber.dispatchEvent(new MouseEvent('pointerdown', { clientY: 100, bubbles: true, cancelable: true }));
      fire('pointermove', 300);
      expect(card.style.transform).toBe('translateY(200px)');
      fire('pointerup', 300);
      await new Promise((resolve) => setTimeout(resolve, 300));
      expect(wrap.classList.contains('hidden')).toBe(true);
    } finally {
      window.matchMedia = originalMM;
    }
  });

  it('shows an error state instead of a blank page when the API fails', async () => {
    await boot({ fail: true });
    const error = document.getElementById('ppError');
    expect(error.classList.contains('hidden')).toBe(false);
    expect(error.textContent).toContain('Could not load people');
    expect(document.getElementById('ppLoading').classList.contains('hidden')).toBe(true);
  });
});
