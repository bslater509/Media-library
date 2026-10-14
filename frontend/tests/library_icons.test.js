import { describe, it, expect } from 'vitest';
import { ICONS, icon, hydrateIcons, DEFAULT_ICON_CLASS } from '../src/library/icons.js';
// The mobile sheet is static HTML, so its icon placeholders are the contract
// between the template and the shared icon set.
import sheetHtml from '../../templates/partials/mobile_controls_sheet.html?raw';

// Every icon the lightbox overlay bar renders.
const LIGHTBOX_ICONS = [
  'gauge', 'repeat-2', 'camera', 'film', 'info', 'download',
  'folder-input', 'pencil', 'trash-2', 'hard-drive', 'monitor', 'clock',
];

describe('icon()', () => {
  it('inlines the SVG with the requested class and no fixed size', () => {
    const svg = icon('camera', 'lb-icon');
    expect(svg.startsWith('<svg')).toBe(true);
    expect(svg).toContain('viewBox="0 0 24 24"');
    expect(svg).toContain('class="lb-icon"');
    expect(svg).toContain('stroke="currentColor"');
    // Sizing comes from the class, not the source width/height attributes.
    expect(svg).not.toMatch(/ width="\d+"/);
    expect(svg).not.toMatch(/ height="\d+"/);
    expect(svg).not.toContain('lucide-camera');
    expect(svg).not.toContain('<!--');
  });

  it('defaults to the shared icon class and hides the glyph from assistive tech', () => {
    const svg = icon('info');
    expect(svg).toContain(`class="${DEFAULT_ICON_CLASS}"`);
    expect(svg).toContain('aria-hidden="true"');
  });

  it('returns an empty string for an unknown icon', () => {
    expect(icon('definitely-not-an-icon')).toBe('');
  });

  it('resolves every icon the lightbox bar uses', () => {
    LIGHTBOX_ICONS.forEach((name) => {
      expect(icon(name), name).toContain('<svg');
    });
  });
});

describe('hydrateIcons()', () => {
  it('replaces placeholders and keeps emoji for unknown names', () => {
    document.body.innerHTML = `
      <span data-lb-icon="search">🔍</span>
      <span data-lb-icon="trash-2" data-lb-icon-class="w-4 h-4 text-red-400">🗑️</span>
      <span data-lb-icon="not-real">✏️</span>
    `;

    const count = hydrateIcons(document.body);

    expect(count).toBe(2);
    const [searchEl, trashEl, unknownEl] = document.body.querySelectorAll('span');
    expect(searchEl.innerHTML).toContain('<svg');
    expect(searchEl.innerHTML).toContain(`class="${DEFAULT_ICON_CLASS}"`);
    expect(trashEl.innerHTML).toContain('class="w-4 h-4 text-red-400"');
    // Unknown name: the placeholder text survives as a fallback.
    expect(unknownEl.textContent).toBe('✏️');
  });

  it('handles a missing root without throwing', () => {
    expect(hydrateIcons(null)).toBe(0);
  });
});

describe('mobile controls sheet placeholders', () => {
  it('only references icons that exist in the shared set', () => {
    const names = [...sheetHtml.matchAll(/data-lb-icon="([^"]+)"/g)].map((m) => m[1]);
    expect(names.length).toBeGreaterThan(0);
    names.forEach((name) => {
      expect(ICONS[name], name).toBeTruthy();
    });
  });
});
