import { describe, expect, it } from 'vitest'
import { clampAspect, layoutRows } from './justified'

interface Item {
  id: string
  width: number
  height: number
}

const aspect = (item: Item) => item.width / item.height

function item(id: string, width: number, height: number): Item {
  return { id, width, height }
}

describe('clampAspect', () => {
  it('clamps portrait and ultrawide ratios to the 2:3–3:2 window', () => {
    expect(clampAspect(0.2)).toBeCloseTo(2 / 3)
    expect(clampAspect(4)).toBeCloseTo(1.5)
    expect(clampAspect(1)).toBe(1)
  })

  it('falls back to 1:1 for missing or invalid dimensions', () => {
    expect(clampAspect(Number.NaN)).toBe(1)
    expect(clampAspect(0)).toBe(1)
    expect(clampAspect(-2)).toBe(1)
  })
})

describe('layoutRows', () => {
  it('returns no rows before the container has been measured', () => {
    expect(layoutRows([item('a', 1600, 900)], 0, 200, 8, aspect)).toEqual([])
  })

  it('fills each complete row to the container width', () => {
    const items = Array.from({ length: 10 }, (_, index) =>
      item(String(index), 1600, 900),
    )
    const rows = layoutRows(items, 1000, 200, 8, aspect)

    expect(rows.length).toBeGreaterThan(1)
    for (const row of rows.slice(0, -1)) {
      const widths = row.items.map(({ aspect: ratio }) => row.height * ratio)
      const total = widths.reduce((sum, width) => sum + width, 0)
      const gaps = 8 * (row.items.length - 1)
      expect(total + gaps).toBeCloseTo(1000, 3)
    }
  })

  it('keeps reading order and never drops an item', () => {
    const items = Array.from({ length: 7 }, (_, index) =>
      item(String(index), index % 2 === 0 ? 1600 : 900, index % 2 === 0 ? 900 : 1600),
    )
    const rows = layoutRows(items, 700, 180, 8, aspect)
    const flat = rows.flatMap((row) => row.items.map(({ item: value }) => value.id))
    expect(flat).toEqual(items.map((value) => value.id))
  })

  it('sizes a trailing partial row at the target height', () => {
    const rows = layoutRows([item('only', 1600, 900)], 1200, 210, 8, aspect)
    expect(rows).toHaveLength(1)
    expect(rows[0].height).toBe(210)
  })

  it('applies the clamp when building a row', () => {
    const rows = layoutRows([item('tall', 400, 4000)], 500, 200, 8, aspect)
    expect(rows[0].items[0].aspect).toBeCloseTo(2 / 3)
  })
})
