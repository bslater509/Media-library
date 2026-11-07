import { describe, expect, it, vi } from 'vitest'
import { createViewRecorder } from './history'

/** Spy on the record calls with their bump flag. */
function spy() {
  return vi.fn((path: string, seconds: number, bump: boolean) => undefined)
}

describe('createViewRecorder', () => {
  it('records an image once on open (bump) and nothing more on close', () => {
    const record = spy()
    const recorder = createViewRecorder('/lib/photo.jpg', false, { record })
    recorder.opened()
    recorder.position(0)
    recorder.closing()
    recorder.closing()
    expect(record).toHaveBeenCalledTimes(1)
    expect(record).toHaveBeenCalledWith('/lib/photo.jpg', 0, true)
  })

  it('bumps at the ~10 s check-in and sends the close record without a bump', () => {
    vi.useFakeTimers()
    try {
      const record = spy()
      const recorder = createViewRecorder('/lib/clip.mp4', true, { record })
      recorder.opened()
      expect(record).not.toHaveBeenCalled()

      recorder.playing(2)
      recorder.position(9.5)
      vi.advanceTimersByTime(10_000)
      expect(record).toHaveBeenCalledTimes(1)
      expect(record).toHaveBeenCalledWith('/lib/clip.mp4', 9.5, true)

      recorder.position(31)
      recorder.closing()
      expect(record).toHaveBeenCalledTimes(2)
      expect(record).toHaveBeenLastCalledWith('/lib/clip.mp4', 31, false)

      // The closing record is debounced: repeating it must not re-send.
      recorder.closing()
      expect(record).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('sends at most one bumping record per session', () => {
    vi.useFakeTimers()
    try {
      const record = spy()
      const recorder = createViewRecorder('/lib/clip.mp4', true, { record })
      // One viewing, checked in twice after a pause: the repeat is not a
      // second play.
      recorder.playing(1)
      vi.advanceTimersByTime(10_000)
      recorder.playing(20)
      vi.advanceTimersByTime(10_000)
      expect(record).toHaveBeenCalledTimes(2)
      expect(record).toHaveBeenNthCalledWith(1, '/lib/clip.mp4', 1, true)
      expect(record).toHaveBeenNthCalledWith(2, '/lib/clip.mp4', 20, false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('cancels the pending check-in and bumps the early close instead', () => {
    vi.useFakeTimers()
    try {
      const record = spy()
      const recorder = createViewRecorder('/lib/clip.mp4', true, { record })
      recorder.playing(3)
      recorder.closing()
      vi.advanceTimersByTime(20_000)
      expect(record).toHaveBeenCalledTimes(1)
      // Nothing was checked in yet, so the closing record counts the play.
      expect(record).toHaveBeenCalledWith('/lib/clip.mp4', 3, true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('records 0 seconds (bump) for a video that never got to play', () => {
    const record = spy()
    const recorder = createViewRecorder('/lib/clip.mp4', true, { record })
    recorder.opened()
    recorder.closing()
    expect(record).toHaveBeenCalledTimes(1)
    expect(record).toHaveBeenCalledWith('/lib/clip.mp4', 0, true)
  })
})
