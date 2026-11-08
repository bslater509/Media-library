import { describe, expect, it } from 'vitest'
import type { FileEntry } from './api'
import { defaultKeepPath } from './dedupe'

const file = (path: string, size: number) => ({ path, size }) as FileEntry

describe('defaultKeepPath', () => {
  it('keeps the largest file regardless of list order', () => {
    expect(
      defaultKeepPath([
        file('/small.mp4', 10),
        file('/large.mp4', 30),
        file('/medium.mp4', 20),
      ]),
    ).toBe('/large.mp4')
  })

  it('keeps the first file when sizes tie', () => {
    expect(
      defaultKeepPath([file('/first.mp4', 10), file('/second.mp4', 10)]),
    ).toBe('/first.mp4')
  })

  it('returns an empty path for an empty group', () => {
    expect(defaultKeepPath([])).toBe('')
  })
})
