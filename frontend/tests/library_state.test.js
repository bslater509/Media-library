import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
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
      localStorage.setItem(TILE_SCALE_KEY, '2.0');
      expect(loadTileScale()).toBe(2.0);
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
      expect(state).toHaveProperty('animObserver');
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
      expect(els).toHaveProperty('generateFolderAnimatedBtnText');
      expect(els).toHaveProperty('generateAllAnimatedBtn');
      expect(els).toHaveProperty('generateAllAnimatedBtnText');
      expect(els).toHaveProperty('triggerAllAnimWorkerBtn');
      expect(els).toHaveProperty('triggerQueueAllAnimBtn');
      expect(els).toHaveProperty('mobileGenerateAllAnimatedTrigger');
      expect(els).toHaveProperty('selectionAnimBtn');
      expect(els).toHaveProperty('searchTruncationBanner');
      expect(els).toHaveProperty('searchTruncationText');
      expect(els).toHaveProperty('dismissSearchTruncationBtn');
    });

    it('exposes search truncation state properties', () => {
      expect(state).toHaveProperty('searchTruncated');
      expect(state.searchTruncated).toBe(false);
      expect(state).toHaveProperty('searchLimit');
      expect(state.searchLimit).toBe(1000);
    });
  });

  describe('mobile thumbnail-size buttons', () => {
    // The mobile Controls sheet renders five preset buttons (XS–XL) whose
    // data-scale values must line up, in order, with TILE_SCALES. A previous
    // XL value of 1.5 was not a valid preset, so tapping XL silently did
    // nothing (and reverted on reload) — this guards against that drift.
    const sheetHtml = readFileSync(
      'templates/partials/mobile_controls_sheet.html',
      'utf8'
    );

    it('renders one preset button per TILE_SCALES entry, in order', () => {
      const scales = [...sheetHtml.matchAll(/id="mobileTileSizeBtn[A-Z]+"[^>]*data-scale="([0-9.]+)"/g)]
        .map((m) => parseFloat(m[1]));
      expect(scales).toEqual(TILE_SCALES);
    });

    it('XL maps to the largest preset, not an off-list scale', () => {
      const xl = sheetHtml.match(/id="mobileTileSizeBtnXL"[^>]*data-scale="([0-9.]+)"/);
      expect(xl).not.toBeNull();
      expect(parseFloat(xl[1])).toBe(TILE_SCALES[TILE_SCALES.length - 1]);
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
