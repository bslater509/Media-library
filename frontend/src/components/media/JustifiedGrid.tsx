import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { FileEntry } from '../../lib/api'
import { clamp } from '../../lib/format'
import { layoutRows } from '../../lib/justified'
import { MediaTile, type FileAction } from './MediaTile'

interface JustifiedGridProps {
  files: FileEntry[]
  targetRowHeight: number
  gap?: number
  hintFor?: (file: FileEntry) => string | null
  onOpen: (index: number) => void
  onAction?: (action: FileAction, file: FileEntry) => void
  selectedIds?: ReadonlySet<string>
  selectionMode?: boolean
  onSelect?: (
    file: FileEntry,
    index: number,
    event: React.MouseEvent<HTMLButtonElement>,
  ) => void
}

/** Aspect used for layout: probed dimensions, clamped, 1:1 when unknown. */
function aspectOf(file: FileEntry): number {
  if (!file.width || !file.height) return 1
  return clamp(file.width / file.height, 2 / 3, 3 / 2)
}

/**
 * Row-ordered gallery. Every tile in a row shares one height, so reading order
 * runs strictly left to right and the row fills the container exactly.
 */
export function JustifiedGrid({
  files,
  targetRowHeight,
  gap = 8,
  hintFor,
  onOpen,
  onAction,
  selectedIds,
  selectionMode = false,
  onSelect,
}: JustifiedGridProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(0)

  useLayoutEffect(() => {
    const element = containerRef.current
    if (!element) return
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width ?? 0
      setWidth((current) => (Math.abs(current - next) > 0.5 ? next : current))
    })
    observer.observe(element)
    setWidth(element.getBoundingClientRect().width)
    return () => observer.disconnect()
  }, [])

  const rows = useMemo(
    () => layoutRows(files, width, targetRowHeight, gap, aspectOf),
    [files, width, targetRowHeight, gap],
  )

  const indexById = useMemo(() => {
    const map = new Map<string, number>()
    files.forEach((file, index) => map.set(file.id, index))
    return map
  }, [files])

  const handleTileSelect = useCallback(
    (index: number, event: React.MouseEvent<HTMLButtonElement>) => {
      const file = files[index]
      if (file && onSelect) onSelect(file, index, event)
    },
    [files, onSelect],
  )

  return (
    <div ref={containerRef} className="flex w-full flex-col" style={{ gap }}>
      {rows.map((row, rowIndex) => (
        <div
          key={`row-${rowIndex}`}
          className="flex w-full"
          style={{ gap, height: Math.round(row.height) }}
        >
          {row.items.map(({ item, aspect }) => {
            const height = Math.round(row.height)
            const tileWidth = Math.max(1, Math.round(height * aspect))
            const index = indexById.get(item.id) ?? 0
            return (
              <MediaTile
                key={item.id}
                index={index}
                file={item}
                height={height}
                width={tileWidth}
                hint={hintFor?.(item) ?? null}
                onOpen={onOpen}
                onAction={onAction}
                selected={selectedIds?.has(item.path) ?? false}
                selectionMode={selectionMode}
                onSelect={onSelect ? handleTileSelect : undefined}
              />
            )
          })}
        </div>
      ))}
      {width === 0 ? <GridSkeleton /> : null}
    </div>
  )
}

function GridSkeleton() {
  return (
    <div className="flex flex-wrap gap-2" aria-hidden="true">
      {[210, 210, 210, 210, 210, 210].map((height, index) => (
        <div
          key={index}
          className="animate-pulse rounded-md bg-surface-2"
          style={{ height, width: Math.round(height * (index % 3 === 0 ? 1.5 : 0.75)) }}
        />
      ))}
    </div>
  )
}
