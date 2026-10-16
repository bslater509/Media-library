import { describe, it, expect, vi, beforeEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// ECharts needs a real canvas, which happy-dom does not provide. Only `init` is
// exercised (the option object is asserted via setOption), so a tiny stub keeps
// the DOM/render contract under test without a canvas dependency.
const setOption = vi.fn();
const chartOn = vi.fn();
vi.mock('echarts/core', () => ({
  use: vi.fn(),
  init: vi.fn(() => ({ setOption, on: chartOn, resize: vi.fn(), dispose: vi.fn() })),
}));

const here = path.dirname(fileURLToPath(import.meta.url));
const templatesDir = path.resolve(here, '../../templates');

function expandIncludes(html, dir) {
  return html.replace(/\{\{\s*include\s+(['"])(.*?)\1\s*\}\}/g, (_m, _q, file) => {
    const filePath = path.resolve(dir, file);
    return expandIncludes(fs.readFileSync(filePath, 'utf8'), path.dirname(filePath));
  });
}

function installPage() {
  const html = expandIncludes(fs.readFileSync(path.join(templatesDir, 'analytics.html'), 'utf8'), templatesDir);
  const bodyMatch = html.match(/<body[^>]*>([\s\S]*)<\/body>/i);
  let body = bodyMatch ? bodyMatch[1] : html;
  // happy-dom tries to fetch/execute <script> tags it parses; drop them. This
  // also means window.THEME_MANAGER is absent, so the page must fall back to its
  // default chart palette instead of throwing.
  body = body.replace(/<script[\s\S]*?<\/script>/gi, '');
  document.body.innerHTML = body;
}

const PAYLOAD = {
  generated_at: 1700000000,
  library_root: '/media',
  limits: { folders: 15, files: 25 },
  totals: {
    bytes: 1073741824,
    bytes_formatted: '1.0 GB',
    files: 12,
    videos: 5,
    images: 7,
    other: 0,
    video_bytes: 1000000000,
    video_bytes_formatted: '953.7 MB',
    image_bytes: 73741824,
    image_bytes_formatted: '70.3 MB',
    folders: 4,
    zip_folders: 1,
  },
  by_type: [
    { type: 'video', count: 5, bytes: 1000000000, bytes_formatted: '953.7 MB', percent_bytes: 93.1 },
    { type: 'image', count: 7, bytes: 73741824, bytes_formatted: '70.3 MB', percent_bytes: 6.9 },
  ],
  by_extension: {
    items: [
      { ext: '.mp4', count: 5, bytes: 1000000000, bytes_formatted: '953.7 MB', percent_bytes: 93.1 },
      { ext: '.webp', count: 7, bytes: 73741824, bytes_formatted: '70.3 MB', percent_bytes: 6.9 },
    ],
    count: 12,
    bytes: 1073741824,
    other_count: 0,
    other_bytes: 0,
    other_bytes_formatted: '0 B',
    other_percent_bytes: 0,
  },
  folders: {
    root_bytes: 1073741824,
    root_bytes_formatted: '1.0 GB',
    root_files: 12,
    top: [
      {
        path: '/media/Movies',
        name: 'Movies',
        rel_path: 'Movies',
        bytes: 800000000,
        bytes_formatted: '762.9 MB',
        files: 3,
        percent_bytes: 74.5,
        is_zip: false,
        children: [
          { path: '/media/Movies/Series', name: 'Series', rel_path: 'Movies/Series', bytes: 300000000, bytes_formatted: '286.1 MB', files: 1, is_zip: false },
        ],
      },
      {
        path: null,
        name: 'Smaller folders (2)',
        rel_path: null,
        bytes: 1000,
        bytes_formatted: '1000 B',
        files: 2,
        percent_bytes: 0.1,
        is_zip: false,
        is_group: true,
        children: [],
      },
    ],
    largest: [
      {
        path: '/media/Movies',
        name: 'Movies',
        rel_path: 'Movies',
        bytes: 800000000,
        bytes_formatted: '762.9 MB',
        files: 3,
        percent_bytes: 74.5,
        is_zip: false,
      },
      {
        path: '/media/Images',
        name: 'Images',
        rel_path: 'Images',
        bytes: 73741824,
        bytes_formatted: '70.3 MB',
        files: 7,
        percent_bytes: 6.9,
        is_zip: false,
      },
    ],
  },
  largest_files: [
    {
      name: '<img src=x onerror=alert(1)>.mp4',
      path: '/media/Movies/big.mp4',
      folder_path: '/media/Movies',
      type: 'video',
      ext: '.mp4',
      size: 800000000,
      size_formatted: '762.9 MB',
      width: 3840,
      height: 2160,
      duration: 3600,
      video_codec: 'h264',
      thumb_url: '/api/backup/thumbnail?path=%2Fmedia%2FMovies%2Fbig.mp4',
      media_url: '/api/backup/media?path=%2Fmedia%2FMovies%2Fbig.mp4',
    },
    {
      name: 'shot.webp',
      path: '/media/Images/shot.webp',
      folder_path: '/media/Images',
      type: 'image',
      ext: '.webp',
      size: 1024,
      size_formatted: '1.0 KB',
      width: 1920,
      height: 1080,
      thumb_url: '/api/backup/thumbnail?path=%2Fmedia%2FImages%2Fshot.webp',
      media_url: '/api/backup/media?path=%2Fmedia%2FImages%2Fshot.webp',
    },
  ],
  quality: {
    buckets: [
      { key: '4k', label: '4K', count: 1, bytes: 800000000, bytes_formatted: '762.9 MB' },
      { key: '1440p', label: '1440p', count: 0, bytes: 0, bytes_formatted: '0 B' },
      { key: '1080p', label: '1080p', count: 2, bytes: 100000000, bytes_formatted: '95.4 MB' },
      { key: '720p', label: '720p', count: 1, bytes: 100000000, bytes_formatted: '95.4 MB' },
      { key: 'sd', label: 'SD', count: 1, bytes: 100000000, bytes_formatted: '95.4 MB' },
      { key: 'unknown', label: 'n/a', count: 0, bytes: 0, bytes_formatted: '0 B' },
    ],
    videos: 5,
    video_bytes: 1000000000,
    video_bytes_formatted: '953.7 MB',
    probed_videos: 5,
    unprobed_videos: 0,
    high_fps_count: 1,
    fps_threshold: 50,
    total_duration: 7200,
    total_duration_formatted: '2h 0m',
  },
  reclaimable: {
    duplicate_groups: 3,
    duplicate_files: 6,
    duplicate_bytes: 2048,
    duplicate_bytes_formatted: '2.0 KB',
    duplicate_ignored_pairs: 1,
    webp_saved_bytes: 1024,
    webp_saved_formatted: '1.0 KB',
    webp_converted_count: 4,
    webp_skipped_count: 0,
    webp_pending_count: 2,
    webp_pending_bytes: 512,
    webp_pending_bytes_formatted: '512 B',
    zip_eligible_folders: 1,
    zip_eligible_bytes: 4096,
    zip_eligible_bytes_formatted: '4.0 KB',
    empty_folders: 1,
  },
  health: {
    missing_thumbs: 2,
    unprobed: 0,
    missing_animated: 3,
    unhashed_visual: 5,
    needs_transcode: 1,
    stale_folders: 0,
    stale_files: 0,
    prune_aborted: false,
    prune_reason: '',
    is_crawling: false,
    is_complete: true,
  },
};

function stubFetch(payload = PAYLOAD) {
  const calls = [];
  global.fetch = vi.fn((url) => {
    calls.push(url);
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
  });
  return calls;
}

async function flush(times = 6) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function boot(payload = PAYLOAD) {
  installPage();
  const calls = stubFetch(payload);
  await import('../src/analytics/index.js');
  await flush();
  return calls;
}

describe('analytics page publisher', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.resetModules();
  });

  it('loads the overview and renders the KPI strip, tables, cards and pills', async () => {
    const calls = await boot();

    expect(calls[0]).toBe('/api/analytics/overview');
    expect(document.getElementById('anBody').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('anLoading').classList.contains('hidden')).toBe(true);

    // KPI strip: six tiles with the formatted totals.
    const kpis = document.getElementById('anKpis');
    expect(kpis.children).toHaveLength(6);
    expect(kpis.textContent).toContain('1.0 GB');

    // Ranked folder rows link into the Library at that folder.
    const folderRows = document.getElementById('anFolderRows');
    expect(folderRows.querySelectorAll('tr')).toHaveLength(2);
    expect(folderRows.textContent).toContain('Movies');
    const folderLink = folderRows.querySelector('a').getAttribute('href');
    expect(folderLink).toBe('/?folder=%2Fmedia%2FMovies');

    // Largest files: the malformed name must be escaped, not executed.
    const fileRows = document.getElementById('anFileRows');
    expect(fileRows.querySelector('img')).toBeNull();
    expect(fileRows.innerHTML).toContain('&lt;img src=x');
    expect(fileRows.textContent).toContain('762.9 MB');
    expect(fileRows.textContent).toContain('4K');

    expect(document.getElementById('anReclaim').textContent).toContain('size-match duplicate group');
    expect(document.getElementById('anHealthPills').textContent).toContain('Index fresh');
    expect(document.getElementById('anHealthPills').textContent).toContain('2 missing thumbnails');
    expect(document.getElementById('anMobileSubtitle').textContent).toBe('12 files · 1.0 GB');
  });

  it('mounts a treemap with clickable folder blocks and skips the aggregate group', async () => {
    await boot();

    const treemapCall = setOption.mock.calls.find(([option]) => option.series?.[0]?.type === 'treemap');
    expect(treemapCall).toBeTruthy();
    const series = treemapCall[0].series[0];
    expect(series.data.map((d) => d.name)).toEqual(['Movies', 'Smaller folders (2)']);
    expect(series.data[0].path).toBe('/media/Movies');
    expect(series.data[0].children).toHaveLength(1);
    // The aggregate "smaller folders" node carries no path, so a click on it
    // must not navigate anywhere.
    expect(series.data[1].path).toBeNull();
    expect(series.data[1].is_group).toBe(true);

    // One donut, one extension bar, one resolution bar.
    const types = setOption.mock.calls.map(([o]) => o.series?.[0]?.type);
    expect(types).toEqual(['treemap', 'pie', 'bar', 'bar']);
  });

  it('reloads with refresh=1 when the Refresh button is pressed', async () => {
    const calls = await boot();
    expect(calls).toHaveLength(1);

    document.getElementById('anRefreshBtn').click();
    await flush();

    expect(calls[1]).toBe('/api/analytics/overview?refresh=1');
  });

  it('previews a largest file in place and links its folder into the Library', async () => {
    await boot();

    const firstRow = document.getElementById('anFileRows').querySelector('tr');
    firstRow.click();

    const modal = document.getElementById('anPreviewModal');
    expect(modal.classList.contains('hidden')).toBe(false);
    expect(document.getElementById('anPreviewName').textContent).toContain('.mp4');
    expect(document.getElementById('anPreviewVideo').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('anPreviewVideo').getAttribute('src')).toBe('/api/backup/media?path=%2Fmedia%2FMovies%2Fbig.mp4');
    expect(document.getElementById('anPreviewOpenBtn').getAttribute('href')).toBe('/?folder=%2Fmedia%2FMovies');
    expect(document.getElementById('anPreviewMeta').textContent).toContain('4K');
  });

  it('shows a readable error state instead of a blank page when the API fails', async () => {
    installPage();
    global.fetch = vi.fn(() => Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) }));
    await import('../src/analytics/index.js');
    await flush();

    const error = document.getElementById('anError');
    expect(error.classList.contains('hidden')).toBe(false);
    expect(error.textContent).toContain('503');
    expect(document.getElementById('anBody').classList.contains('hidden')).toBe(true);
  });
});
