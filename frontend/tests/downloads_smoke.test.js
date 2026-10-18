import { describe, it, expect, vi, beforeEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// The /downloads page: the target-folder chooser (shared with /people, so the
// page no longer carries its own picker markup) and the target label it drives.

const here = path.dirname(fileURLToPath(import.meta.url));
const templatesDir = path.resolve(here, '../../templates');

function expandIncludes(html, dir) {
  return html.replace(/\{\{\s*include\s+(['"])(.*?)\1\s*\}\}/g, (_m, _q, file) => {
    const filePath = path.resolve(dir, file);
    return expandIncludes(fs.readFileSync(filePath, 'utf8'), path.dirname(filePath));
  });
}

function installPage() {
  const html = expandIncludes(
    fs.readFileSync(path.join(templatesDir, 'downloads.html'), 'utf8'),
    templatesDir
  );
  const bodyMatch = html.match(/<body[^>]*>([\s\S]*)<\/body>/i);
  let body = bodyMatch ? bodyMatch[1] : html;
  body = body.replace(/<script[\s\S]*?<\/script>/gi, '');
  document.body.innerHTML = body;
}

function jsonResponse(payload) {
  return { ok: true, status: 200, json: () => Promise.resolve(payload) };
}

function stubFetch({ folders = null } = {}) {
  global.fetch = vi.fn((url) => {
    const target = String(url);
    if (target.startsWith('/api/backup/folders')) {
      const q = new URL(target, 'http://localhost').searchParams;
      const folder = q.get('path') || '';
      return Promise.resolve(jsonResponse({
        current_path: folder || '/media',
        is_root: !folder,
        subfolders: (folders && folders[folder]) || [],
        indexing: false,
      }));
    }
    if (target.startsWith('/api/backup/upload/url/history')) {
      return Promise.resolve(jsonResponse({ history: [], total: 0, limit: 50, offset: 0 }));
    }
    if (target.startsWith('/api/backup/upload/url/status')) {
      return Promise.resolve(jsonResponse({
        status: 'idle',
        active: [],
        queued: [],
        history: [],
        tools: { ytdlp: true, gallery_dl: false },
      }));
    }
    return Promise.resolve(jsonResponse({ success: true }));
  });
}

async function flush(times = 8) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function boot(options) {
  installPage();
  stubFetch(options);
  await import('../src/downloads/index.js');
  await flush();
}

describe('downloads page folder chooser', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.resetModules();
    localStorage.clear();
  });

  it('picks the target folder from the shared chooser', async () => {
    const folders = {
      '': [{ name: 'Trips', path: '/media/Trips', is_zip: false }],
      '/media/Trips': [
        { name: 'Iceland', path: '/media/Trips/Iceland', is_zip: false },
        { name: 'archive.zip', path: '/media/Trips/archive.zip', is_zip: true },
      ],
    };
    await boot({ folders });

    document.getElementById('dlBrowseBtn').click();
    await flush();
    expect(document.querySelector('[data-picker-list]')).toBeTruthy();

    document.querySelector('[data-picker-dir="/media/Trips"]').click();
    await flush();
    expect(document.querySelector('[data-picker-dir="/media/Trips/Iceland"]')).toBeTruthy();
    // An archive is never offered as a download target.
    expect(document.querySelector('[data-picker-dir="/media/Trips/archive.zip"]')).toBeNull();

    document.querySelector('[data-picker-confirm]').click();
    await flush();
    expect(document.getElementById('dlTargetLabel').textContent).toBe('/media/Trips');
    const sheet = document.querySelector('[data-picker-list]').closest('.ml-sheet');
    expect(sheet.classList.contains('hidden')).toBe(true);
  });

  it('does not carry its own picker markup any more', () => {
    const html = fs.readFileSync(path.join(templatesDir, 'downloads.html'), 'utf8');
    expect(html).not.toContain('dlPickerModal');
    expect(html).not.toContain('confirmDlPickerBtn');
  });
});
