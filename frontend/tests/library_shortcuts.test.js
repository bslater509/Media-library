import { describe, it, expect } from 'vitest';
import shortcutsModalHtml from '../../templates/partials/library_shortcuts_modal.html?raw';
import libraryHtml from '../../templates/library.html?raw';
import mobileSheetHtml from '../../templates/partials/mobile_controls_sheet.html?raw';
import { SHORTCUT_GROUPS } from '../src/library/shortcuts.js';
import { librarySource } from './sources.js';

// The library binds two dozen shortcuts and never told anyone about them. These
// assertions pin the `?` reference to the bindings it documents, so the help
// cannot quietly go stale or unreachable.
const DOCUMENTED_GLOBAL_KEYS = ['Ctrl', 'A', 'Esc', 'T', '?'];

describe('keyboard shortcut reference', () => {
  it('documents the grid-level bindings the entrypoint actually implements', () => {
    const entry = SHORTCUT_GROUPS.find((g) => g.title === 'Library');
    expect(entry).toBeTruthy();
    const keys = entry.entries.flatMap((e) => e.keys);
    DOCUMENTED_GLOBAL_KEYS.forEach((k) => expect(keys).toContain(k));

    // Each of these is a real branch in the keydown handler.
    expect(librarySource).toMatch(/toggleSelectAll/);
    expect(librarySource).toMatch(/openThemeModal/);
    expect(librarySource).toMatch(/key === '\?'/);
  });

  it('documents the viewer bindings that index.js handles', () => {
    const viewer = SHORTCUT_GROUPS.find((g) => g.title.includes('Viewer'));
    expect(viewer).toBeTruthy();
    const keys = viewer.entries.flatMap((e) => e.keys);
    ['F', 'I', 'R', 'D', 'M', 'L', 'P', 'S', 'Space', ',', '.', '[', ']'].forEach((k) =>
      expect(keys).toContain(k)
    );

    // Spot-check that the descriptions match real entrypoint branches.
    expect(librarySource).toMatch(/toggleCurrentLightboxFavorite/);
    expect(librarySource).toMatch(/convertLightboxVideoToX265/);
    expect(librarySource).toMatch(/toggleLightboxVideoLoop/);
    expect(librarySource).toMatch(/captureCurrentFrameAsPoster/);
  });

  it('has a description and at least one key for every entry', () => {
    SHORTCUT_GROUPS.forEach((group) => {
      expect(group.title).toBeTruthy();
      expect(group.entries.length).toBeGreaterThan(0);
      group.entries.forEach((e) => {
        expect(e.keys.length).toBeGreaterThan(0);
        expect(e.description.length).toBeGreaterThan(4);
      });
    });
  });

  it('never advertises a key the library does not bind', () => {
    // Guard against inventing shortcuts: the documented letters must appear as
    // key comparisons somewhere in the bundle.
    const letters = SHORTCUT_GROUPS.flatMap((g) => g.entries.flatMap((e) => e.keys)).filter(
      (k) => k.length === 1 && /[A-Za-z]/.test(k)
    );
    letters.forEach((letter) => {
      const pattern = new RegExp(`key === '${letter.toLowerCase()}'`, 'i');
      expect(librarySource).toMatch(pattern);
    });
  });
});

describe('keyboard shortcut reference — markup', () => {
  it('is a full-height mobile sheet with a scrim, grabber and labelled dialog', () => {
    expect(shortcutsModalHtml).toMatch(/id="shortcutsModal"/);
    expect(shortcutsModalHtml).toMatch(/class="ml-sheet fixed/);
    expect(shortcutsModalHtml).toMatch(/ml-sheet-card/);
    expect(shortcutsModalHtml).toMatch(/data-sheet-grabber/);
    expect(shortcutsModalHtml).toMatch(/id="shortcutsModalTitle"/);
    expect(shortcutsModalHtml).toMatch(/id="shortcutsGroups"/);
    expect(shortcutsModalHtml).toMatch(/id="closeShortcutsBtn"/);
    expect(shortcutsModalHtml).toMatch(/id="closeShortcutsFooterBtn"/);
  });

  it('is included by the library page and reachable on desktop and phones', () => {
    expect(libraryHtml).toContain("{{include 'partials/library_shortcuts_modal.html'}}");
    expect(libraryHtml).toMatch(/id="shortcutsHelpBtn"/);
    expect(mobileSheetHtml).toMatch(/id="mobileShortcutsBtn"/);
    // Every static icon placeholder must resolve to a real SVG (icons.test.js
    // covers the registry, this pins the new ones).
    expect(libraryHtml).toMatch(/data-lb-icon="keyboard"/);
    expect(shortcutsModalHtml).toMatch(/data-lb-icon="keyboard"/);
  });

  it('wires open/close and the sheet-drag dismissal in the bundle', () => {
    ['openShortcutsHelp', 'closeShortcutsHelp', 'isShortcutsHelpOpen', 'initShortcutsHelp'].forEach(
      (fn) => expect(librarySource).toContain(fn)
    );
    // theme.js registers the sheet so swipe-down dismisses it like the others.
    expect(librarySource).toMatch(/shortcutsModal: closeShortcutsHelp/);
    // The overlay must not fight a text field being typed into.
    expect(librarySource).toMatch(/isShortcutsHelpOpen\(\)[\s\S]{0,200}Escape/);
  });
});
