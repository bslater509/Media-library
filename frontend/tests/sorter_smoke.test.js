import { describe, it, expect, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { sorterStateSource as sorterState } from './sources.js';

// The sorter entrypoint was split from one large module into several feature
// modules. This smoke test loads the real page markup (partials expanded, as
// the server does) and imports the entrypoint, so a broken import, a missing
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
  const html = expandIncludes(fs.readFileSync(path.join(templatesDir, 'index.html'), 'utf8'), templatesDir);
  const bodyMatch = html.match(/<body[^>]*>([\s\S]*)<\/body>/i);
  let body = bodyMatch ? bodyMatch[1] : html;
  body = body.replace(/<script[\s\S]*?<\/script>/gi, '');
  document.body.innerHTML = body;
}

describe('sorter entrypoint smoke test', () => {
  it('imports the split entrypoint and wires the page without errors', async () => {
    installPage();

    // The bootstrap awaits config/destination fetches; keep them off the network.
    const realFetch = globalThis.fetch;
    const fetchMock = vi.fn(() => Promise.resolve({ ok: true, json: async () => ({}) }));
    globalThis.fetch = fetchMock;
    window.fetch = fetchMock;

    const errors = [];
    const originalError = console.error;
    console.error = (...args) => { errors.push(args.join(' ')); };
    try {
      await import('../src/app/index.js');
      document.dispatchEvent(new Event('DOMContentLoaded'));
      await Promise.resolve();
      await Promise.resolve();
    } finally {
      console.error = originalError;
      globalThis.fetch = realFetch;
    }

    // The bootstrap must have reached the config/destination loads.
    expect(fetchMock).toHaveBeenCalled();

    const fatal = errors.filter((e) => /is not defined|is not a function|Cannot read/.test(e));
    expect(fatal).toEqual([]);
  });

  it('renders toast messages as text, never as markup', () => {
    // The sorter toast is the error surface, and its messages embed filenames
    // and server error text; inserting them as HTML would be stored XSS.
    expect(sorterState).not.toMatch(/toast\.innerHTML/);
    expect(sorterState).toContain('text.textContent = message');
    expect(sorterState).toContain('toast.setAttribute("role", "alert")');
  });
});
