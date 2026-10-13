import { describe, it, expect } from 'vitest';
import { readRecentTargets, rememberRecentTarget } from '../src/library/recent_targets.js';

describe('library/recent_targets.js unit tests', () => {
  it('starts empty', () => {
    expect(readRecentTargets()).toEqual([]);
  });

  it('remembers targets newest first', () => {
    rememberRecentTarget('/tv');
    rememberRecentTarget('/movies');
    expect(readRecentTargets()).toEqual(['/movies', '/tv']);
  });

  it('moves a repeated target to the front instead of duplicating it', () => {
    rememberRecentTarget('/tv');
    rememberRecentTarget('/movies');
    rememberRecentTarget('/tv');
    expect(readRecentTargets()).toEqual(['/tv', '/movies']);
  });

  it('caps the list at eight entries', () => {
    for (let i = 0; i < 12; i++) rememberRecentTarget(`/folder-${i}`);
    const list = readRecentTargets();
    expect(list).toHaveLength(8);
    expect(list[0]).toBe('/folder-11');
    expect(list).not.toContain('/folder-3');
  });

  it('ignores empty targets and recovers from corrupt storage', () => {
    expect(rememberRecentTarget('')).toEqual([]);
    localStorage.setItem('lib_dl_recent_targets', 'not json');
    expect(readRecentTargets()).toEqual([]);
    localStorage.setItem('lib_dl_recent_targets', JSON.stringify({ nope: true }));
    expect(readRecentTargets()).toEqual([]);
  });
});
