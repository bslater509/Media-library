import { describe, it, expect, beforeEach } from 'vitest';
import {
  nameCollator,
  TILE_SCALES,
  TILE_SCALE_LABELS,
  TILE_SCALE_KEY,
  loadTileScale,
  SIDEBAR_TREE_KEY,
  loadExpandedFolders,
  persistExpandedFolders,
  state,
} from '../src/library/state.js';

describe('library/state.js unit tests', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  describe('nameCollator', () => {
    it('sorts strings with natural numeric ordering', () => {
      const items = ['file10.mp4', 'file2.mp4', 'file1.mp4'];
      items.sort((a, b) => nameCollator.compare(a, b));
      expect(items).toEqual(['file1.mp4', 'file2.mp4', 'file10.mp4']);
    });
  });

  describe('tile scale loader', () => {
    it('returns default 1.0 when no scale is stored in localStorage', () => {
      expect(loadTileScale()).toBe(1.0);
    });

    it('loads stored valid scale from localStorage', () => {
      localStorage.setItem(TILE_SCALE_KEY, '1.5');
      expect(loadTileScale()).toBe(1.5);
    });

    it('falls back to 1.0 on invalid or out-of-range scale value', () => {
      localStorage.setItem(TILE_SCALE_KEY, '9.99');
      expect(loadTileScale()).toBe(1.0);
    });
  });

  describe('sidebar tree expanded folders persistence', () => {
    it('loads an empty set when nothing is stored', () => {
      const set = loadExpandedFolders();
      expect(set instanceof Set).toBe(true);
      expect(set.size).toBe(0);
    });

    it('persists and retrieves expanded folder paths', () => {
      const original = new Set(['folderA', 'folderA/subB']);
      persistExpandedFolders(original);

      const retrieved = loadExpandedFolders();
      expect(retrieved.has('folderA')).toBe(true);
      expect(retrieved.has('folderA/subB')).toBe(true);
      expect(retrieved.size).toBe(2);
    });

    it('handles malformed localStorage JSON gracefully', () => {
      localStorage.setItem(SIDEBAR_TREE_KEY, 'not-json-string');
      const retrieved = loadExpandedFolders();
      expect(retrieved instanceof Set).toBe(true);
      expect(retrieved.size).toBe(0);
    });
  });

  describe('default state shape', () => {
    it('verifies standard properties exist on state object', () => {
      expect(state).toHaveProperty('basePath');
      expect(state).toHaveProperty('currentPath');
      expect(state).toHaveProperty('rootFolders');
      expect(state).toHaveProperty('subfolders');
      expect(state).toHaveProperty('allFiles');
      expect(state).toHaveProperty('activeFilter');
      expect(state).toHaveProperty('activeSort');
      expect(state.activeFilter).toBe('all');
    });
  });
});
