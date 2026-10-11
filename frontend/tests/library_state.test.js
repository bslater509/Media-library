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
  ANIMATED_THUMBS_KEY,
  loadAnimatedThumbs,
  persistAnimatedThumbs,
  state,
  els,
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

  describe('els DOM object shape', () => {
    it('verifies stage and sticky navigation element keys exist on els', () => {
      expect(els).toHaveProperty('mainStage');
      expect(els).toHaveProperty('libraryStickyHeader');
      expect(els).toHaveProperty('galleryContentArea');
      expect(els).toHaveProperty('scrollToTopBtn');
    });

    it('verifies sidebar drawer and backdrop element keys exist on els', () => {
      expect(els).toHaveProperty('toggleSidebarBtn');
      expect(els).toHaveProperty('closeSidebarBtn');
      expect(els).toHaveProperty('sidebarDrawer');
      expect(els).toHaveProperty('sidebarBackdrop');
    });

    it('verifies mobile single-line header and bottom sheet element keys exist on els', () => {
      expect(els).toHaveProperty('mobileHeader');
      expect(els).toHaveProperty('mobileSidebarToggleBtn');
      expect(els).toHaveProperty('mobileBackBtn');
      expect(els).toHaveProperty('mobileFolderTitleBtn');
      expect(els).toHaveProperty('mobileFolderTitleText');
      expect(els).toHaveProperty('mobileControlsBtn');
      expect(els).toHaveProperty('mobileControlsBadge');
      expect(els).toHaveProperty('mobileControlsModal');
      expect(els).toHaveProperty('mobileControlsSheet');
      expect(els).toHaveProperty('closeMobileControlsBtn');
      expect(els).toHaveProperty('doneMobileControlsBtn');
      expect(els).toHaveProperty('mobileTabSearchBtn');
      expect(els).toHaveProperty('mobileTabDisplayBtn');
      expect(els).toHaveProperty('mobileTabActionsBtn');
      expect(els).toHaveProperty('mobileTabSystemBtn');
    });

    it('verifies thumbnail chooser modal element keys exist on els', () => {
      expect(els).toHaveProperty('thumbnailChooserModal');
      expect(els).toHaveProperty('thumbChooserGrid');
      expect(els).toHaveProperty('thumbChooserScrubber');
      expect(els).toHaveProperty('thumbChooserPreviewImg');
      expect(els).toHaveProperty('thumbChooserPreviewVideo');
      expect(els).toHaveProperty('thumbChooserCandCount');
    });

    it('verifies animated preview toolbar, sheet and batch button element keys exist on els', () => {
      expect(els).toHaveProperty('toggleAnimatedPreviewsBtn');
      expect(els).toHaveProperty('mobileSheetAnimatedPreviewsToggle');
      expect(els).toHaveProperty('generateFolderAnimatedBtn');
    });
  });

  describe('animated thumbnails persistence', () => {
    it('defaults to false when nothing is in localStorage', () => {
      expect(loadAnimatedThumbs()).toBe(false);
    });

    it('persists and loads animated preview preference', () => {
      persistAnimatedThumbs(true);
      expect(loadAnimatedThumbs()).toBe(true);
      expect(localStorage.getItem(ANIMATED_THUMBS_KEY)).toBe('true');

      persistAnimatedThumbs(false);
      expect(loadAnimatedThumbs()).toBe(false);
      expect(localStorage.getItem(ANIMATED_THUMBS_KEY)).toBe('false');
    });
  });
});
