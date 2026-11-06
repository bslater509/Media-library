import { describe, expect, it } from 'vitest'
import { buildCapabilities, hasSeenOnboarding, markOnboardingSeen } from './onboarding'

describe('buildCapabilities', () => {
  it('lists only the features that are actually enabled', () => {
    const groups = buildCapabilities({
      browse: true,
      search: true,
      uploads: false,
      manage: false,
      sorter: true,
      dedupe: false,
    })
    expect(groups.available.map((item) => item.key)).toEqual([
      'browse',
      'search',
      'sorter',
    ])
    expect(groups.pending.map((item) => item.key)).toEqual([
      'workers',
      'x265',
      'batch',
    ])
  })

  it('handles a missing feature map', () => {
    const groups = buildCapabilities(undefined)
    expect(groups.available).toEqual([])
    expect(groups.pending).toHaveLength(3)
  })
})

describe('onboarding persistence', () => {
  it('degrades to "not seen" without browser storage', () => {
    expect(hasSeenOnboarding()).toBe(false)
    expect(() => markOnboardingSeen()).not.toThrow()
  })
})
