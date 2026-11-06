import { Star } from 'lucide-react'
import type { FileEntry } from '../../lib/api'
import { cn } from '../../lib/cn'
import { codecLabel, formatDuration, isHevc, imageLabel, resolutionLabel } from '../../lib/format'
import { MediaPlaceholder, MediaImage } from './MediaImage'

interface MediaTileProps {
  file: FileEntry
  height: number
  width: number
  hint?: string | null
  onOpen: () => void
}

const CODE_BADGE = 'bg-black/65 text-white/90'

/** One justified-grid cell: poster, badges and a hover filename strip. */
export function MediaTile({ file, height, width, hint, onOpen }: MediaTileProps) {
  const duration = formatDuration(file.duration)
  const resolution =
    file.type === 'image'
      ? imageLabel(file.width, file.height)
      : resolutionLabel(file.width, file.height)
  const codec = codecLabel(file.video_codec)
  const favorite = file.is_favorite
  const fps = file.fps && file.fps >= 50 ? `${Math.round(file.fps)}fps` : null
  const isVideo = file.type === 'video'

  return (
    <button
      type="button"
      onClick={onOpen}
      title={file.name}
      aria-label={`${file.name}${duration ? `, ${duration}` : ''}`}
      className={cn(
        'group relative block h-full w-full overflow-hidden rounded-md bg-surface-2',
        'outline-none ring-accent-ring transition-shadow focus-visible:ring-2',
        'hover:shadow-e1',
      )}
      style={{ width, height }}
    >
      {file.has_thumb || file.type === 'image' ? (
        <MediaImage file={file} />
      ) : (
        <MediaPlaceholder />
      )}

      {/* Readability scrims, only where a badge sits. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-12 bg-gradient-to-b from-black/45 to-transparent opacity-70"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 bottom-0 h-14 bg-gradient-to-t from-black/60 to-transparent opacity-80"
      />

      <div className="pointer-events-none absolute inset-x-1.5 top-1.5 flex items-start justify-between gap-1">
        <div className="flex flex-wrap items-center gap-1">
          {resolution ? (
            <span className="rounded-sm bg-black/70 px-1.5 py-0.5 text-[10px] leading-4 font-semibold text-white/95 tabular-nums">
              {resolution}
            </span>
          ) : null}
          {fps ? (
            <span className="rounded-sm bg-black/60 px-1 py-0.5 text-[10px] leading-4 font-medium text-white/80 tabular-nums">
              {fps}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-1">
          {codec ? (
            <span
              className={cn(
                'rounded-sm px-1.5 py-0.5 text-[10px] leading-4 font-semibold',
                isHevc(file.video_codec) ? 'bg-emerald-600/85 text-white' : CODE_BADGE,
              )}
            >
              {codec}
            </span>
          ) : null}
          {favorite ? (
            <span className="rounded-sm bg-black/65 p-0.5 text-amber-300">
              <Star aria-hidden="true" className="size-3 fill-amber-300" />
            </span>
          ) : null}
        </div>
      </div>

      <div className="pointer-events-none absolute inset-x-1.5 bottom-1.5 flex items-end justify-between gap-2">
        <span className="flex min-w-0 flex-col rounded-sm bg-black/55 px-1.5 py-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
          <span className="truncate text-[11px] leading-4 font-medium text-white/90">
            {file.name}
          </span>
          {hint ? (
            <span className="truncate text-[10px] leading-3 text-white/60">{hint}</span>
          ) : null}
        </span>
        {isVideo && duration ? (
          <span className="shrink-0 rounded-sm bg-black/75 px-1.5 py-0.5 text-[10px] leading-4 font-semibold text-white tabular-nums">
            {duration}
          </span>
        ) : null}
      </div>

      {isVideo && file.has_animated ? (
        <span className="pointer-events-none absolute top-1.5 left-1/2 -translate-x-1/2 rounded-sm bg-accent/85 px-1 py-0.5 text-[9px] leading-3 font-bold tracking-[0.08em] text-white uppercase opacity-0 transition-opacity group-hover:opacity-100">
          anim
        </span>
      ) : null}
    </button>
  )
}
