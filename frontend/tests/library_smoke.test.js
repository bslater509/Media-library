import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// The library entrypoint was split from one large module into several feature
// modules. This smoke test loads the real page markup (partials expanded, as the
// server does) and imports the entrypoint, so a broken import, a missing
// element lookup or a stray free identifier fails here rather than in the
// browser.

const here = path.dirname(fileURLToPath(import.meta.url));
const templatesDir = path.resolve(here, '../../templates');

function expandIncludes(html, dir) {
  return html.replace(/\{\{\s*include\s+(['"])(.*?)\1\s*\}\}/g, (_m, _q, file) => {
    const filePath = path.resolve(dir, file);
    return expandIncludes(fs.readFileSync(filePath, 'utf8'), path.dirname(filePath));
  });
}

// setup.js registers a beforeEach that clears the body, so the markup has to be
// installed inside the test (before the entrypoint is imported).
function installPage() {
  const html = expandIncludes(fs.readFileSync(path.join(templatesDir, 'library.html'), 'utf8'), templatesDir);
  const bodyMatch = html.match(/<body[^>]*>([\s\S]*)<\/body>/i);
  let body = bodyMatch ? bodyMatch[1] : html;
  // happy-dom tries to fetch/execute <script> tags it parses; drop them.
  body = body.replace(/<script[\s\S]*?<\/script>/gi, '');
  document.body.innerHTML = body;
}

describe('library entrypoint smoke test', () => {
  it('imports the split entrypoint and exposes the global app API', async () => {
    installPage();
    const errors = [];
    const originalError = console.error;
    console.error = (...args) => { errors.push(args.join(' ')); };
    try {
      await import('../src/library/index.js');
      // init() runs synchronously up to its first await.
      await Promise.resolve();
    } finally {
      console.error = originalError;
    }

    expect(window.libraryApp).toBeTruthy();
    [
      'openRenameModal',
      'openDeleteModal',
      'openDuplicatesModal',
      'openCompressZipModal',
      'openThumbnailChooser',
      'openThemeModal',
      'openMediaInfoModal',
      'openMoveModalFromLightbox',
    ].forEach((name) => expect(window.libraryApp[name]).toBeTypeOf('function'));

    // The split must not surface as a runtime error while wiring up the page.
    const fatal = errors.filter((e) => /is not defined|is not a function|Cannot read/.test(e));
    expect(fatal).toEqual([]);
  });
});
