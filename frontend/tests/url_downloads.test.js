import { describe, it, expect } from 'vitest';
import {
  urlDownloadsActivityCount,
  urlDownloadsSummaryText,
  urlDownloadTitle,
  urlDownloadRowHtml,
  urlDownloadRowsHtml,
} from '../src/library/url_downloads.js';

const job = (overrides = {}) => ({
  id: 'dl_abc',
  url: 'https://example.com/watch?v=1',
  destination_dir: '/library',
  status: 'downloading',
  percent: 42.5,
  speed: '1.2MiB/s',
  eta: '00:12',
  current_title: 'Clip One',
  playlist_index: 0,
  playlist_count: 0,
  imported_count: 0,
  downloader: 'yt-dlp',
  error: null,
  ...overrides,
});

describe('library/url_downloads.js unit tests', () => {
  describe('urlDownloadsActivityCount', () => {
    it('counts active plus queued jobs', () => {
      expect(urlDownloadsActivityCount({
        active: [job(), job()],
        queued: [job({ status: 'queued' })],
      })).toBe(3);
    });

    it('is 0 for missing or empty payloads', () => {
      expect(urlDownloadsActivityCount(null)).toBe(0);
      expect(urlDownloadsActivityCount({})).toBe(0);
      expect(urlDownloadsActivityCount({ active: [], queued: [] })).toBe(0);
    });
  });

  describe('urlDownloadsSummaryText', () => {
    it('reports a single active job percentage', () => {
      expect(urlDownloadsSummaryText({ active: [job({ percent: 42.5 })], queued: [] })).toBe('DL 43%');
    });

    it('reports multiple active jobs as a count', () => {
      expect(urlDownloadsSummaryText({ active: [job(), job()], queued: [] })).toBe('DL 2 active');
    });

    it('reports queued-only and idle states', () => {
      expect(urlDownloadsSummaryText({ active: [], queued: [job({ status: 'queued' })] })).toBe('1 queued');
      expect(urlDownloadsSummaryText({ active: [], queued: [], history: [] })).toBe('Idle');
    });
  });

  describe('urlDownloadTitle', () => {
    it('prefers the live title', () => {
      expect(urlDownloadTitle(job())).toBe('Clip One');
    });

    it('falls back to the URL without its scheme', () => {
      expect(urlDownloadTitle(job({ current_title: '' }))).toBe('example.com/watch?v=1');
    });

    it('falls back to the job id', () => {
      expect(urlDownloadTitle({ id: 'dl_x', url: '' })).toBe('dl_x');
    });
  });

  describe('urlDownloadRowHtml', () => {
    it('renders an active job with progress, speed, ETA and downloader', () => {
      const html = urlDownloadRowHtml(job());
      expect(html).toContain('Clip One');
      expect(html).toContain('downloading');
      expect(html).toContain('1.2MiB/s');
      expect(html).toContain('ETA 00:12');
      expect(html).toContain('yt-dlp');
      expect(html).toContain('width:43%');
      expect(html).toContain('data-urldl-cancel="dl_abc"');
    });

    it('shows the playlist position when known', () => {
      const html = urlDownloadRowHtml(job({ playlist_index: 3, playlist_count: 10 }));
      expect(html).toContain('3/10');
    });

    it('offers cancel for queued jobs but not finished ones', () => {
      expect(urlDownloadRowHtml(job({ status: 'queued' }))).toContain('data-urldl-cancel');
      expect(urlDownloadRowHtml(job({ status: 'completed', imported_count: 4 })))
        .not.toContain('data-urldl-cancel');
    });

    it('summarises completed and failed jobs', () => {
      const done = urlDownloadRowHtml(job({ status: 'completed', imported_count: 4, downloader: 'gallery-dl' }));
      expect(done).toContain('4 files');
      expect(done).toContain('gallery-dl');

      const failed = urlDownloadRowHtml(job({ status: 'failed', error: 'yt-dlp exited with code 1' }));
      expect(failed).toContain('yt-dlp exited with code 1');
    });

    it('escapes titles and never emits a live progress bar for finished jobs', () => {
      const html = urlDownloadRowHtml(job({ status: 'completed', current_title: '<script>x</script>' }));
      expect(html).toContain('&lt;script&gt;');
      expect(html).not.toContain('<script>x</script>');
      expect(html).not.toContain('bg-emerald-500 transition-all');
    });
  });

  describe('urlDownloadRowsHtml', () => {
    it('orders active, then queued, then newest history first', () => {
      const html = urlDownloadRowsHtml({
        active: [job({ id: 'dl_active', current_title: 'Active' })],
        queued: [job({ id: 'dl_queued', current_title: 'Queued', status: 'queued' })],
        history: [
          job({ id: 'dl_old', current_title: 'Older', status: 'completed' }),
          job({ id: 'dl_new', current_title: 'Newest', status: 'completed' }),
        ],
      });
      expect(html.indexOf('Active')).toBeLessThan(html.indexOf('Queued'));
      expect(html.indexOf('Queued')).toBeLessThan(html.indexOf('Newest'));
      expect(html.indexOf('Newest')).toBeLessThan(html.indexOf('Older'));
    });

    it('caps the history tail while keeping every active job', () => {
      const history = Array.from({ length: 30 }, (_, i) =>
        job({ id: `dl_h${i}`, current_title: `Hist ${i}`, status: 'completed' }));
      const html = urlDownloadRowsHtml(
        { active: [job({ current_title: 'Active' })], queued: [], history },
        { historyLimit: 3 },
      );
      expect(html).toContain('Active');
      expect(html).toContain('Hist 29');
      expect(html).toContain('Hist 27');
      expect(html).not.toContain('Hist 26');
    });

    it('returns an empty string when there is nothing to show', () => {
      expect(urlDownloadRowsHtml({ active: [], queued: [], history: [] })).toBe('');
      expect(urlDownloadRowsHtml(null)).toBe('');
    });
  });
});
