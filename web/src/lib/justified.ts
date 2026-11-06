/**
 * Justified-row layout maths.
 *
 * Tiles are packed into rows that share one height, chosen so the row fills the
 * container exactly. Aspect ratios are clamped to 2:3–3:2 so one very tall or
 * very wide item cannot dominate a row, and unknown dimensions fall back to 1:1.
 */

export interface AspectItem {
  aspect: number
}

export interface JustifiedRow<T> {
  items: Array<{ item: T; aspect: number }>
  height: number
}

const MIN_ASPECT = 2 / 3
const MAX_ASPECT = 3 / 2

export function clampAspect(aspect: number): number {
  if (!Number.isFinite(aspect) || aspect <= 0) return 1
  return Math.min(MAX_ASPECT, Math.max(MIN_ASPECT, aspect))
}

export function layoutRows<T>(
  items: readonly T[],
  containerWidth: number,
  targetRowHeight: number,
  gap: number,
  getAspect: (item: T) => number,
): JustifiedRow<T>[] {
  if (containerWidth <= 0 || items.length === 0) return []

  const rows: JustifiedRow<T>[] = []
  let current: Array<{ item: T; aspect: number }> = []
  let aspectSum = 0

  const flush = (fill: boolean) => {
    if (current.length === 0) return
    const gaps = gap * (current.length - 1)
    const height = fill
      ? (containerWidth - gaps) / aspectSum
      : targetRowHeight
    rows.push({ items: current, height: Math.max(40, height) })
    current = []
    aspectSum = 0
  }

  for (const item of items) {
    const aspect = clampAspect(getAspect(item))
    current.push({ item, aspect })
    aspectSum += aspect
    if (aspectSum * targetRowHeight + gap * (current.length - 1) >= containerWidth) {
      flush(true)
    }
  }

  flush(false)
  return rows
}
