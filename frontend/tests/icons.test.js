import { describe, it, expect } from 'vitest';
import { ICONS, icon, hydrateIcons, DEFAULT_ICON_CLASS } from '../src/shared/icons.js';
// The mobile sheet and both Background Tasks modals are static HTML, so their
// icon placeholders are the contract between the templates and the icon set.
import sheetHtml from '../../templates/partials/mobile_controls_sheet.html?raw';
import tasksModalHtml from '../../templates/partials/tasks_modal.html?raw';
import sorterTasksModalHtml from '../../templates/partials/sorter_tasks_modal.html?raw';
import libraryHtml from '../../templates/library.html?raw';
import { librarySource, sorterSource } from './sources.js';
// Every icon the lightbox overlay bar renders.
const LIGHTBOX_ICONS = [
  'gauge', 'repeat-2', 'camera', 'film', 'star', 'info', 'download',
  'folder-input', 'pencil', 'trash-2', 'hard-drive', 'monitor', 'clock',
  'shrink',
];

// Every icon the two Background Tasks modals render.
const TASKS_MODAL_ICONS = [
  'archive', 'arrow-right-left', 'ban', 'check', 'circle-check', 'circle-x',
  'clapperboard', 'cog', 'database', 'folder', 'folder-tree', 'hard-drive',
  'images', 'loader-circle', 'package-open', 'refresh-cw', 'rocket',
  'scan-face', 'search', 'settings', 'sparkles', 'trash-2', 'upload', 'wrench',
  'x', 'zap',
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

  it('resolves every icon the Background Tasks modals use', () => {
    TASKS_MODAL_ICONS.forEach((name) => {
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

// Shared helper: every `data-lb-icon` name a static template references must
// exist in the set, and its `data-lb-icon-class` must carry a size so the SVG
// is not left at the browser default.
function expectPlaceholdersResolve(html, label) {
  const names = [...html.matchAll(/data-lb-icon="([^"]+)"/g)].map((m) => m[1]);
  expect(names.length, label).toBeGreaterThan(0);
  names.forEach((name) => {
    expect(ICONS[name], `${label}: ${name}`).toBeTruthy();
  });

  const placeholders = [...html.matchAll(/<span[^>]*data-lb-icon="[^"]+"[^>]*>/g)].map((m) => m[0]);
  placeholders.forEach((tag) => {
    expect(tag, `${label}: ${tag}`).toMatch(/data-lb-icon-class="[^"]*w-\d/);
  });
}

describe('static template icon placeholders', () => {
  it('only references icons that exist in the shared set (mobile controls sheet)', () => {
    expectPlaceholdersResolve(sheetHtml, 'mobile controls sheet');
  });

  it('only references icons that exist in the shared set (library tasks modal)', () => {
    expectPlaceholdersResolve(tasksModalHtml, 'library tasks modal');
  });

  it('only references icons that exist in the shared set (sorter tasks modal)', () => {
    expectPlaceholdersResolve(sorterTasksModalHtml, 'sorter tasks modal');
  });

  it('only references icons that exist in the shared set (library page shell)', () => {
    // The mobile top bar's one-tap Analytics link hydrates `gauge` here.
    expectPlaceholdersResolve(libraryHtml, 'library page');
  });

  it('leaves no emoji behind in either Background Tasks modal', () => {
    const emoji = /[\u2190-\u2BFF\u2600-\u27BF\uFE0F]|\p{Extended_Pictographic}/u;
    [tasksModalHtml, sorterTasksModalHtml].forEach((html, i) => {
      const offenders = html
        .split('\n')
        .map((line, n) => [n + 1, line.trim()])
        .filter(([, line]) => emoji.test(line));
      // The only permitted glyphs are the plain text arrows used mid-sentence.
      offenders.forEach(([n, line]) => {
        expect(line.replace(/[→➔]/g, ''), `line ${n}: ${line}`).not.toMatch(emoji);
      });
    });
  });
});

// The JS-rendered rows of both modals (upload queue, transcode/transfer history)
// pass icon names through `icon()`; a typo there would silently render nothing.
describe('icon() call sites in the modal row renderers', () => {
  it('only names icons that exist in the shared set', () => {
    const sources = [
      ['frontend/src/library/index.js', librarySource],
      ['frontend/src/app/index.js', sorterSource],
    ];
    sources.forEach(([label, source]) => {
      const calls = [...source.matchAll(/\bicon\(([^)]*)\)/g)].map((m) => m[1]);
      expect(calls.length, label).toBeGreaterThan(0);
      calls.forEach((args) => {
        const names = [...args.matchAll(/'([a-z0-9-]+)'|"([a-z0-9-]+)"/g)]
          .map((m) => m[1] || m[2]);
        expect(names.length, `${label}: icon(${args})`).toBeGreaterThan(0);
        names.forEach((name) => {
          expect(ICONS[name], `${label}: ${name}`).toBeTruthy();
        });
      });
    });
  });
});
