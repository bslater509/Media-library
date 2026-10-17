import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { hashToHex, makeUploadId, uploadFile, uploadState } from '../src/library/uploads.js';

// Interrupted uploads used to restart from byte zero and leave their staged
// `<upload_id>.part` behind forever. These tests pin the resume contract: a
// stable id per (path, size, mtime), a status probe before the first chunk, and
// an explicit byte offset on every chunk so a retry cannot double-append.

const CHUNK = 4;

function makeFileObj(name = 'clip.mp4', size = 10, mtime = 1234, id = 'u_resume_test') {
  const bytes = new Uint8Array(size);
  for (let i = 0; i < size; i++) bytes[i] = i + 1;
  const file = new File([bytes], name, { type: 'video/mp4' });
  return {
    file,
    id,
    relativePath: name,
    size,
    mtime,
    status: 'uploading',
    uploadedBytes: 0,
  };
}

describe('upload id is stable and resumable', () => {
  it('derives the same id for the same file and a different one when it changes', async () => {
    const a = await makeUploadId({ relativePath: 'trip/clip.mp4', size: 10, mtime: 5 });
    const b = await makeUploadId({ relativePath: 'trip/clip.mp4', size: 10, mtime: 5 });
    const c = await makeUploadId({ relativePath: 'trip/clip.mp4', size: 11, mtime: 5 });
    const d = await makeUploadId({ relativePath: 'other/clip.mp4', size: 10, mtime: 5 });

    expect(a).toBe(b);
    expect(a).not.toBe(c);
    expect(a).not.toBe(d);
    // Must satisfy the server's `^[a-zA-Z0-9_-]{1,64}$` staging-name rule.
    expect(a).toMatch(/^[a-zA-Z0-9_-]+$/);
    expect(a.length).toBeLessThanOrEqual(64);
  });

  it('falls back to a plain hash where crypto.subtle is unavailable (plain-http phone)', () => {
    // secure contexts only: a phone on http://192.168.x.x has no subtle crypto.
    const saved = window.crypto;
    try {
      Object.defineProperty(window, 'crypto', { value: undefined, configurable: true });
      return makeUploadId({ relativePath: 'a.mp4', size: 1, mtime: 2 }).then((id) => {
        expect(id).toBe('u_' + hashToHex('a.mp4|1|2'));
        expect(id).toMatch(/^[a-zA-Z0-9_-]+$/);
      });
    } finally {
      Object.defineProperty(window, 'crypto', { value: saved, configurable: true });
    }
  });
});

describe('uploadFile resumes an interrupted transfer', () => {
  let originalFetch;

  beforeEach(() => {
    originalFetch = global.fetch;
    uploadState.chunkSize = CHUNK;
    uploadState.isCancelled = false;
    uploadState.targetFolder = '/library/target';
    uploadState.queue = [];
    uploadState.activeUploads = 0;
    uploadState.startTime = Date.now();
    uploadState.totalBytes = 0;
    document.body.innerHTML = '<div id="toastContainer"></div>';
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('starts at the chunk the server already holds and never re-sends it', async () => {
    const fileObj = makeFileObj();
    const chunks = [];
    let finalized = null;

    global.fetch = vi.fn(async (url, opts = {}) => {
      const target = String(url);
      if (target.startsWith('/api/backup/upload/status')) {
        // The first chunk (bytes 0-3) is already staged from an earlier attempt.
        return { ok: true, json: async () => ({ status: 'ok', received_bytes: 4, exists: true }) };
      }
      if (target.includes('/api/backup/upload/chunk')) {
        chunks.push({
          index: opts.body.get('chunk_index'),
          offset: opts.body.get('offset'),
          total: opts.body.get('total_chunks'),
        });
        return { ok: true, json: async () => ({ status: 'ok' }) };
      }
      if (target.includes('/api/backup/upload/finalize')) {
        finalized = JSON.parse(opts.body);
        return { ok: true, json: async () => ({ status: 'ok', path: '/library/target/clip.mp4' }) };
      }
      throw new Error('unexpected fetch: ' + target);
    });

    await uploadFile(fileObj);

    // size 10 / chunk 4 => chunks 0,1,2; chunk 0 was already received.
    expect(chunks.map((c) => c.index)).toEqual(['1', '2']);
    expect(chunks.map((c) => c.offset)).toEqual(['4', '8']);
    expect(chunks.every((c) => c.total === '3')).toBe(true);
    expect(fileObj.status).toBe('done');
    expect(fileObj.uploadedBytes).toBe(10);
    expect(finalized.upload_id).toBe(fileObj.id);
    expect(finalized.total_size).toBe(10);
  });

  it('sends every chunk from zero when the server holds nothing', async () => {
    const fileObj = makeFileObj();
    const offsets = [];

    global.fetch = vi.fn(async (url, opts = {}) => {
      const target = String(url);
      if (target.startsWith('/api/backup/upload/status')) {
        return { ok: true, json: async () => ({ status: 'ok', received_bytes: 0, exists: false }) };
      }
      if (target.includes('/api/backup/upload/chunk')) {
        offsets.push(opts.body.get('offset'));
        return { ok: true, json: async () => ({ status: 'ok' }) };
      }
      return { ok: true, json: async () => ({ status: 'ok' }) };
    });

    await uploadFile(fileObj);

    expect(offsets).toEqual(['0', '4', '8']);
  });

  it('still uploads when the status probe fails', async () => {
    const fileObj = makeFileObj();
    const offsets = [];

    global.fetch = vi.fn(async (url, opts = {}) => {
      const target = String(url);
      if (target.startsWith('/api/backup/upload/status')) throw new Error('offline');
      if (target.includes('/api/backup/upload/chunk')) {
        offsets.push(opts.body.get('offset'));
        return { ok: true, json: async () => ({ status: 'ok' }) };
      }
      return { ok: true, json: async () => ({ status: 'ok' }) };
    });

    await uploadFile(fileObj);

    expect(offsets).toEqual(['0', '4', '8']);
    expect(fileObj.status).toBe('done');
  });

  it('finalizes without re-sending anything when every chunk is staged', async () => {
    const fileObj = makeFileObj();
    let chunkCalls = 0;

    global.fetch = vi.fn(async (url, opts = {}) => {
      const target = String(url);
      if (target.startsWith('/api/backup/upload/status')) {
        return { ok: true, json: async () => ({ status: 'ok', received_bytes: 10, exists: true }) };
      }
      if (target.includes('/api/backup/upload/chunk')) {
        chunkCalls += 1;
        return { ok: true, json: async () => ({ status: 'ok' }) };
      }
      return { ok: true, json: async () => ({ status: 'ok', path: '/library/target/clip.mp4' }) };
    });

    await uploadFile(fileObj);

    expect(chunkCalls).toBe(0);
    expect(fileObj.status).toBe('done');
  });
});
