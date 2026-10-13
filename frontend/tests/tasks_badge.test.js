import { describe, it, expect } from 'vitest';
import {
  formatTaskCount,
  computeTaskSummary,
  describePrimaryTask,
} from '../src/shared/tasks_badge.js';

describe('library/shared/tasks_badge.js unit tests', () => {
  describe('formatTaskCount', () => {
    it('returns an empty string for zero or missing counts', () => {
      expect(formatTaskCount(0)).toBe('');
      expect(formatTaskCount(null)).toBe('');
      expect(formatTaskCount(undefined)).toBe('');
    });

    it('renders plain numbers up to the cap', () => {
      expect(formatTaskCount(1)).toBe('1');
      expect(formatTaskCount(42)).toBe('42');
      expect(formatTaskCount(99)).toBe('99');
    });

    it('caps at 99+', () => {
      expect(formatTaskCount(100)).toBe('99+');
      expect(formatTaskCount(12345)).toBe('99+');
    });
  });

  describe('computeTaskSummary', () => {
    it('is idle with no sources', () => {
      expect(computeTaskSummary()).toEqual({ count: 0, active: false });
      expect(computeTaskSummary({})).toEqual({ count: 0, active: false });
    });

    it('sums active + queued transfers, library ops and transcodes', () => {
      const summary = computeTaskSummary({
        transfers: { active: { filename: 'a.mp4' }, queued: [{ id: 1 }, { id: 2 }] },
        libraryOps: { active: { op: 'move' }, queued: [{ id: 3 }] },
        transcode: { active: null, queue_length: 4 },
      });
      expect(summary.count).toBe(1 + 2 + 1 + 1 + 4);
      expect(summary.active).toBe(true);
    });

    it('counts URL download active and queued jobs as items', () => {
      const summary = computeTaskSummary({
        urlDownloads: { active: [{ id: 1 }, { id: 2 }], queued: [{ id: 3 }] },
      });
      expect(summary.count).toBe(3);
    });

    it('counts animated preview queue via total_queued', () => {
      expect(computeTaskSummary({ animated: { active: true, total_queued: 5 } }).count).toBe(5);
      expect(computeTaskSummary({ animated: { active: null, total_queued: 0, queue_length: 2 } }).count).toBe(2);
    });

    it('counts bulk workers as one while they are running', () => {
      const summary = computeTaskSummary({
        index: { status: 'crawling' },
        backup: { status: 'backing_up' },
        thumbnails: { status: 'generating', pending_count: 2000 },
        webp: { status: 'converting', pending_count: 500 },
      });
      expect(summary.count).toBe(4);
      expect(summary.active).toBe(true);
    });

    it('ignores bulk workers that are idle', () => {
      const summary = computeTaskSummary({
        index: { status: 'idle' },
        thumbnails: { status: 'generating', pending_count: 0 },
        webp: { status: 'idle', pending_count: 0 },
        batchZip: { status: 'idle' },
        batchUnpack: { status: 'paused' },
      });
      expect(summary.count).toBe(0);
      expect(summary.active).toBe(false);
    });

    it('counts ZIP jobs and batch runs while active', () => {
      const summary = computeTaskSummary({
        zip: { is_active: true, tasks: [{ status: 'running', type: 'compress' }] },
        batchZip: { status: 'running' },
        batchUnpack: { status: 'processing' },
      });
      expect(summary.count).toBe(3);
    });

    it('includes client-side uploads as active + queued items', () => {
      expect(computeTaskSummary({ uploads: { active: 2, queued: 3 } }).count).toBe(5);
    });
  });

  describe('describePrimaryTask', () => {
    it('reports nothing when idle', () => {
      expect(describePrimaryTask({})).toBe('');
    });

    it('describes an active transfer with its percentage', () => {
      expect(describePrimaryTask({
        transfers: { active: { percent: 42.5 } },
      })).toBe('Moving 43%');
    });

    it('describes queued transfers as a count', () => {
      expect(describePrimaryTask({
        transfers: { active: null, queued: [{ id: 1 }, { id: 2 }] },
      })).toBe('2 transfers');
    });

    it('prefers transfers over other subsystems', () => {
      expect(describePrimaryTask({
        transfers: { active: { percent: 10 } },
        index: { status: 'crawling', percent: 50 },
      })).toBe('Moving 10%');
    });

    it('describes library ops with their verb', () => {
      expect(describePrimaryTask({
        libraryOps: { active: { op: 'move' }, queued: [] },
      })).toBe('Moving');
      expect(describePrimaryTask({
        libraryOps: { active: null, queued: [{ id: 1 }] },
      })).toBe('1 op');
    });

    it('describes index and backup sweeps', () => {
      expect(describePrimaryTask({ index: { status: 'crawling', percent: 33.33 } })).toBe('Indexing 33%');
      expect(describePrimaryTask({ backup: { status: 'backing_up', progress_percent: 40 } })).toBe('Backup 40%');
    });

    it('describes transcodes, animated previews and queued downloads', () => {
      expect(describePrimaryTask({ transcode: { active: { percent: 75 }, queue_length: 2 } })).toBe('75% (3)');
      expect(describePrimaryTask({ animated: { active: { id: 1 }, percent: 50, total_queued: 3 } })).toBe('Anim 50%');
      expect(describePrimaryTask({ animated: { active: null, total_queued: 2 } })).toBe('2 anim');
      expect(describePrimaryTask({ urlDownloads: { active: [], queued: [{ id: 1 }] } })).toBe('1 dl queued');
    });

    it('describes active uploads', () => {
      expect(describePrimaryTask({ uploads: { active: 1, queued: 0, percent: 60 } })).toBe('Uploading 60%');
    });
  });
});