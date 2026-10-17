import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  escapeHtml,
  safeAttrEscape,
  formatBytes,
  formatDuration,
  getResolutionLabel,
  getAspectRatio,
  getCodecLabel,
  showToast,
  formatTimestamp,
  formatBitrate,
  shouldPlayAnimatedThumb,
} from '../src/library/utils.js';

describe('library/utils.js unit tests', () => {
  describe('escapeHtml & safeAttrEscape', () => {
    it('escapes special HTML characters properly', () => {
      expect(escapeHtml('<script>alert("xss") & \'foo\'</script>')).toBe(
        '&lt;script&gt;alert(&quot;xss&quot;) &amp; &#39;foo&#39;&lt;/script&gt;'
      );
      expect(escapeHtml(null)).toBe('');
      expect(escapeHtml(undefined)).toBe('');
      expect(escapeHtml(123)).toBe('123');
    });

    it('safely escapes attribute strings with URL encoding', () => {
      expect(safeAttrEscape('hello world/test.mp4')).toBe('hello%20world%2Ftest.mp4');
      expect(safeAttrEscape("it's cool")).toBe('it%27s%20cool');
      expect(safeAttrEscape('')).toBe('');
    });
  });

  describe('formatBytes', () => {
    it('formats byte numbers into human readable strings', () => {
      expect(formatBytes(0)).toBe('0 B');
      expect(formatBytes(500)).toBe('500 B');
      expect(formatBytes(1024)).toBe('1 KB');
      expect(formatBytes(1024 * 1024)).toBe('1 MB');
      expect(formatBytes(1536 * 1024, 1)).toBe('1.5 MB');
      expect(formatBytes(1024 * 1024 * 1024)).toBe('1 GB');
    });
  });

  describe('formatDuration', () => {
    it('formats seconds into MM:SS and HH:MM:SS', () => {
      expect(formatDuration(0)).toBe('00:00');
      expect(formatDuration(45)).toBe('00:45');
      expect(formatDuration(65)).toBe('01:05');
      expect(formatDuration(3600)).toBe('1:00:00');
      expect(formatDuration(3665)).toBe('1:01:05');
      expect(formatDuration(null)).toBe('00:00');
      expect(formatDuration('invalid')).toBe('00:00');
    });
  });

  describe('getResolutionLabel', () => {
    it('identifies standard video resolutions', () => {
      expect(getResolutionLabel(3840, 2160)).toBe('4K');
      expect(getResolutionLabel(2560, 1440)).toBe('2K');
      expect(getResolutionLabel(1920, 1080)).toBe('1080p');
      expect(getResolutionLabel(1280, 720)).toBe('720p');
      expect(getResolutionLabel(854, 480)).toBe('480p');
      expect(getResolutionLabel(640, 360)).toBe('');
      expect(getResolutionLabel(0, 0)).toBe('');
    });

    it('handles vertical / rotated video orientations', () => {
      expect(getResolutionLabel(1080, 1920)).toBe('1080p');
      expect(getResolutionLabel(2160, 3840)).toBe('4K');
    });
  });

  describe('getCodecLabel', () => {
    it('maps the H.264/H.265 families to friendly labels', () => {
      expect(getCodecLabel('h264')).toBe('H.264');
      expect(getCodecLabel('avc1')).toBe('H.264');
      expect(getCodecLabel('hevc')).toBe('H.265');
      expect(getCodecLabel('h265')).toBe('H.265');
      expect(getCodecLabel('HEVC')).toBe('H.265');
    });

    it('uppercases other codecs and returns empty for unknown input', () => {
      expect(getCodecLabel('mpeg4')).toBe('MPEG-4');
      expect(getCodecLabel('mpeg2video')).toBe('MPEG-2');
      expect(getCodecLabel('vp9')).toBe('VP9');
      expect(getCodecLabel('av1')).toBe('AV1');
      expect(getCodecLabel('')).toBe('');
      expect(getCodecLabel(null)).toBe('');
      expect(getCodecLabel(undefined)).toBe('');
    });
  });

  describe('getAspectRatio', () => {
    it('identifies standard aspect ratios', () => {
      expect(getAspectRatio(1920, 1080)).toBe('16:9');
      expect(getAspectRatio(1080, 1920)).toBe('9:16');
      expect(getAspectRatio(1000, 1000)).toBe('1:1');
      expect(getAspectRatio(400, 300)).toBe('4:3');
      expect(getAspectRatio(300, 400)).toBe('3:4');
      expect(getAspectRatio(0, 0)).toBe('');
    });
  });

  describe('showToast', () => {
    it('appends toast to container or body and removes after timeout', () => {
      const toastContainer = document.createElement('div');
      toastContainer.id = 'toastContainer';
      document.body.appendChild(toastContainer);

      showToast('Action successful', 'success');

      const toast = toastContainer.querySelector('div');
      expect(toast).not.toBeNull();
      expect(toast.textContent).toContain('Action successful');
      expect(toast.textContent).toContain('✓');
    });

    it('renders messages as text so a filename can never inject markup', () => {
      const toastContainer = document.createElement('div');
      toastContainer.id = 'toastContainer';
      document.body.appendChild(toastContainer);

      showToast('Deleted "<img src=x onerror=alert(1)>.mp4"', 'error');

      const toast = toastContainer.querySelector('div');
      expect(toast.textContent).toContain('<img src=x onerror=alert(1)>.mp4');
      expect(toast.querySelector('img')).toBeNull();
      expect(toast.innerHTML).toContain('&lt;img src=x');
    });

    it('announces errors assertively and everything else politely', () => {
      const toastContainer = document.createElement('div');
      toastContainer.id = 'toastContainer';
      document.body.appendChild(toastContainer);

      showToast('boom', 'error');
      showToast('fine', 'info');

      const toasts = toastContainer.querySelectorAll('div');
      expect(toasts[0].getAttribute('role')).toBe('alert');
      expect(toasts[1].getAttribute('role')).toBe('status');
    });
  });

  describe('formatTimestamp', () => {
    it('formats epoch seconds and milliseconds to local string', () => {
      expect(formatTimestamp(null)).toBe('Unknown');
      expect(formatTimestamp(0)).toBe('Unknown');
      expect(formatTimestamp('invalid')).toBe('Unknown');
      // Valid epoch timestamp (1700000000 -> Nov 14, 2023)
      const res = formatTimestamp(1700000000);
      expect(res).not.toBe('Unknown');
      expect(typeof res).toBe('string');
    });
  });

  describe('formatBitrate', () => {
    it('calculates Mbps and kbps from bytes and duration seconds', () => {
      expect(formatBitrate(0, 10)).toBe('');
      expect(formatBitrate(1000, 0)).toBe('');
      expect(formatBitrate(null, null)).toBe('');

      // 10,000,000 bytes over 10s = 8,000,000 bps = 8.0 Mbps
      expect(formatBitrate(10000000, 10)).toBe('8.0 Mbps');

      // 50,000 bytes over 10s = 40,000 bps = 40 kbps
      expect(formatBitrate(50000, 10)).toBe('40 kbps');
    });
  });

  describe('shouldPlayAnimatedThumb', () => {
    it('never animates non-video tiles', () => {
      expect(shouldPlayAnimatedThumb({ isVideo: false, hasHoverPointer: true, isHovered: true })).toBe(false);
      expect(shouldPlayAnimatedThumb({ isVideo: false, isVisible: true, animatedThumbs: true })).toBe(false);
    });

    it('on desktop, plays only while the tile is hovered (toggle independent)', () => {
      const base = { isVideo: true, hasHoverPointer: true };
      expect(shouldPlayAnimatedThumb({ ...base, isHovered: true, animatedThumbs: false })).toBe(true);
      expect(shouldPlayAnimatedThumb({ ...base, isHovered: true, animatedThumbs: true })).toBe(true);
      expect(shouldPlayAnimatedThumb({ ...base, isHovered: false, animatedThumbs: true })).toBe(false);
      expect(shouldPlayAnimatedThumb({ ...base, isHovered: false, isVisible: true, animatedThumbs: true })).toBe(false);
    });

    it('on touch, keeps scroll-into-view behaviour gated by the toggle', () => {
      const base = { isVideo: true, hasHoverPointer: false };
      expect(shouldPlayAnimatedThumb({ ...base, isVisible: true, animatedThumbs: true })).toBe(true);
      expect(shouldPlayAnimatedThumb({ ...base, isVisible: false, animatedThumbs: true })).toBe(false);
      expect(shouldPlayAnimatedThumb({ ...base, isVisible: true, animatedThumbs: false })).toBe(false);
      // Hover is ignored without a hover-capable pointer.
      expect(shouldPlayAnimatedThumb({ ...base, isVisible: false, isHovered: true, animatedThumbs: true })).toBe(false);
    });

    it('defaults to not playing with no arguments', () => {
      expect(shouldPlayAnimatedThumb()).toBe(false);
    });
  });
});
