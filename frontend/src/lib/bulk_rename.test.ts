import { describe, expect, it } from 'vitest'
import { bulkName } from './bulk_rename'

describe('bulkName', () => {
  it('prefixes the base name and keeps the extension', () => {
    expect(bulkName('clip.mp4', 'prefix', '2024-', '', '')).toBe('2024-clip.mp4')
  })

  it('suffixes the base name only', () => {
    expect(bulkName('photo.final.jpg', 'suffix', ' (raw)', '', '')).toBe(
      'photo.final (raw).jpg',
    )
  })

  it('replaces every occurrence in the base name, never the extension', () => {
    expect(bulkName('a.b.mov', 'replace', '', '.', '_')).toBe('a_b.mov')
    expect(bulkName('x.mp4', 'replace', '', '', 'y')).toBe('x.mp4')
  })

  it('handles extensionless names', () => {
    expect(bulkName('README', 'prefix', 'x-', '', '')).toBe('x-README')
  })
})
