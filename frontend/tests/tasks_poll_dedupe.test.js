import { describe, it, expect } from 'vitest';
import { payloadChanged } from '../src/library/tasks.js';

// The unified status poll runs every 2-6s. Renders are skipped for any
// subsystem whose payload has not changed since the last poll, so an idle
// server does not rewrite the tasks modal (and its class names) forever.
describe('system status poll — re-render dedupe', () => {
  it('reports a subsystem as unchanged when it repeats exactly', () => {
    const payload = { status: 'idle', pending_count: 0, percent: 100 };
    expect(payloadChanged('dedupe-repeat', payload)).toBe(true);
    expect(payloadChanged('dedupe-repeat', { ...payload, pending_count: 0 })).toBe(false);
  });

  it('re-renders as soon as any field of the payload moves', () => {
    const payload = { status: 'idle', pending_count: 0 };
    expect(payloadChanged('dedupe-change', payload)).toBe(true);
    expect(payloadChanged('dedupe-change', { status: 'idle', pending_count: 1 })).toBe(true);
  });

  it('tracks each subsystem independently', () => {
    expect(payloadChanged('dedupe-a', { n: 1 })).toBe(true);
    expect(payloadChanged('dedupe-b', { n: 1 })).toBe(true);
    expect(payloadChanged('dedupe-a', { n: 1 })).toBe(false);
  });

  it('treats an unserialisable payload as changed rather than silently skipping it', () => {
    const circular = {};
    circular.self = circular;
    expect(payloadChanged('dedupe-circular', circular)).toBe(true);
    expect(payloadChanged('dedupe-circular', circular)).toBe(true);
  });
});
