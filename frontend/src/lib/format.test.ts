import { describe, expect, it } from 'vitest'
import {
  codecLabel,
  formatBytes,
  formatDuration,
  imageLabel,
  isHevc,
  resolutionLabel,
} from './format'

describe('formatDuration', () => {
  it('renders MM:SS below an hour and HH:MM:SS above', () => {
    expect(formatDuration(493.4)).toBe('8:13')
    expect(formatDuration(59)).toBe('0:59')
    expect(formatDuration(3661)).toBe('1:01:01')
  })

  it('returns null for missing or nonsensical durations', () => {
    expect(formatDuration(null)).toBeNull()
    expect(formatDuration(0)).toBeNull()
    expect(formatDuration(-4)).toBeNull()
  })
})

describe('resolutionLabel', () => {
  it('buckets by the longest edge so portrait 4K still reads 4K', () => {
    expect(resolutionLabel(3840, 2160)).toBe('4K')
    expect(resolutionLabel(2160, 3840)).toBe('4K')
    expect(resolutionLabel(1920, 1080)).toBe('1080p')
    expect(resolutionLabel(1280, 720)).toBe('720p')
  })

  it('does not pretend tiny clips are a known tier', () => {
    expect(resolutionLabel(640, 480)).toBe('SD')
    expect(resolutionLabel(320, 240)).toBe('320×240')
    expect(resolutionLabel(null, 1080)).toBeNull()
  })
})

describe('imageLabel', () => {
  it('uses megapixels for photos', () => {
    expect(imageLabel(4000, 3000)).toBe('12 MP')
    expect(imageLabel(797, 1200)).toBe('1 MP')
  })

  it('shows exact pixels for very small images', () => {
    expect(imageLabel(200, 200)).toBe('200×200')
  })
})

describe('codecLabel', () => {
  it('normalises the codec names the probe returns', () => {
    expect(codecLabel('h264')).toBe('H.264')
    expect(codecLabel('hevc')).toBe('H.265')
    expect(codecLabel('av1')).toBe('AV1')
    expect(codecLabel(null)).toBeNull()
    expect(isHevc('h265')).toBe(true)
    expect(isHevc('h264')).toBe(false)
  })
})

describe('formatBytes', () => {
  it('keeps bytes exact below 1 KiB and rounds larger units', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(483)).toBe('483 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(5_242_880)).toBe('5.0 MB')
    expect(formatBytes(1_572_864_000)).toBe('1.5 GB')
  })
})
