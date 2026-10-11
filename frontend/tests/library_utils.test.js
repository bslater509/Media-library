import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  escapeHtml,
  safeAttrEscape,
  formatBytes,
  formatDuration,
  getResolutionLabel,
  getAspectRatio,
  showToast,
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
  });
});
