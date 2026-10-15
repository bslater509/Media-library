import { describe, it, expect, vi, beforeEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { state } from '../src/library/state.js';
import { openFancybox } from '../src/library/lightbox.js';

const templatesDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../templates');

// Fancybox frames a video slide at the item's own `videoRatio` and falls back to
// 16/9, which letterboxes portrait clips. The library therefore forwards the
// probed dimensions; files that have not been probed keep the 16:9 default.
describe('lightbox video sizing', () => {
  let show;

  beforeEach(() => {
    show = vi.fn();
    globalThis.Fancybox = { show };
    state.filteredFiles = [
      {
        type: 'video', path: '/media/portrait.mp4', name: 'portrait.mp4',
        media_url: '/api/backup/media?path=portrait.mp4', thumb_url: '/t1',
        width: 1080, height: 1920, duration: 12, size_formatted: '1.0 MB',
      },
      {
        type: 'video', path: '/media/unprobed.mkv', name: 'unprobed.mkv',
        media_url: '/api/backup/media?path=unprobed.mkv', thumb_url: '/t2',
        width: null, height: null, duration: 3, size_formatted: '1.0 MB',
      },
      {
        type: 'image', path: '/media/photo.jpg', name: 'photo.jpg',
        media_url: '/api/backup/media?path=photo.jpg', thumb_url: '/t3',
        width: 800, height: 600, size_formatted: '0.2 MB',
      },
    ];
  });

  it('forwards the probed aspect ratio for videos and nothing else', () => {
    openFancybox(0);
    expect(show).toHaveBeenCalledTimes(1);

    const [items] = show.mock.calls[0];
    expect(items.map((i) => i.type)).toEqual(['html5video', 'html5video', 'image']);

    expect(typeof items[0].videoRatio).toBe('number');
    expect(items[0].videoRatio).toBeCloseTo(1080 / 1920, 6);

    // Not probed yet: no ratio, so Fancybox keeps its 16:9 default.
    expect(items[1].videoRatio).toBeUndefined();

    // Images are not touched by the video sizing path.
    expect(items[2].videoRatio).toBeUndefined();
  });

  // The two CSS halves of the same fix live in the page markup: the content box
  // measures against the whole slide (instead of Fancybox's 960x540 16:9 box)
  // and the bottom control bar gets its own band reserved inside video slides so
  // the picture never runs underneath it.
  it('keeps the video sizing and caption bar band rules in the page markup', () => {
    const css = fs.readFileSync(path.join(templatesDir, 'library.html'), 'utf8');
    const block = (selector) => {
      const i = css.indexOf(selector);
      return i === -1 ? '' : css.slice(i, css.indexOf('}', i));
    };

    const content = block('.fancybox__slide.has-html5video .fancybox__content');
    expect(content).toContain('width: 100%');
    expect(content).toContain('height: 100%');

    const slide = block('.fancybox__slide.has-html5video {');
    expect(slide).toContain('padding-bottom');
  });
});
