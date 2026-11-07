import { memo, useState } from 'react'
import {
  Check,
  FolderInput,
  MoreVertical,
  Pencil,
  Star,
  StarOff,
  Trash2,
} from 'lucide-react'
import type { FileEntry } from '../../lib/api'
import { cn } from '../../lib/cn'
import { codecLabel, formatDuration, imageLabel, isHevc, resolutionLabel } from '../../lib/format'
import {
  Menu,
  MenuContent,
  MenuItem,
  MenuSeparator,
  MenuTrigger,
} from '../ui'
import { MediaPlaceholder, MediaImage } from './MediaImage'

export type FileAction =
  | 'favorite'
  | 'unfavorite'
  | 'rename'
  | 'move'
  | 'delete'

interface MediaTileProps {
  /** Position of the file in the grid row order (stable shallow-compare prop). */
  index: number
  file: FileEntry
  height: number
  width: number
  hint?: string | null
  onOpen: (index: number) => void
  onAction?: (action: FileAction, file: FileEntry) => void
  selected?: boolean
  selectionMode?: boolean
  onSelect?: (index: number, event: React.MouseEvent<HTMLButtonElement>) => void
}

const CODE_BADGE = 'bg-black/65 text-white/90'

/** One justified-grid cell: poster, badges and an actions menu. */
export const MediaTile = memo(function MediaTile({
  file,
  height,
  width,
  hint,
  onOpen,
  onAction,
  selected = false,
  selectionMode = false,
  onSelect,
  index,
}: MediaTileProps) {
  const [menuOpen, setMenuOpen] = useState(false)
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
    <div
      className={cn(
        'group relative shrink-0 rounded-md',
        selected && 'ring-2 ring-accent ring-offset-2 ring-offset-bg',
      )}
      style={{ width, height }}
      onContextMenu={(event) => {
        if (!onAction) return
        event.preventDefault()
        setMenuOpen(true)
      }}
    >
      <button
        type="button"
        onClick={(event) => {
          if (onSelect && (selectionMode || event.metaKey || event.ctrlKey || event.shiftKey)) {
            event.preventDefault()
            onSelect(index, event)
            return
          }
          onOpen(index)
        }}
        title={file.name}
        aria-label={`${file.name}${duration ? `, ${duration}` : ''}`}
        aria-pressed={onSelect ? selected : undefined}
        className={cn(
          'absolute inset-0 block h-full w-full overflow-hidden rounded-md bg-surface-2',
          'outline-none ring-accent-ring transition-shadow focus-visible:ring-2',
          'hover:shadow-e1',
        )}
      >
        {file.has_thumb || file.type === 'image' ? (
          <MediaImage file={file} />
        ) : (
          <MediaPlaceholder />
        )}

        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 top-0 h-12 bg-gradient-to-b from-black/45 to-transparent opacity-70"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 bottom-0 h-14 bg-gradient-to-t from-black/60 to-transparent opacity-80"
        />

        <div className="pointer-events-none absolute inset-x-1.5 top-1.5 flex items-start justify-between gap-1">
          <div className="flex flex-wrap items-center gap-1 pr-7">
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

        {favorite ? (
          <span className="pointer-events-none absolute bottom-1.5 left-1.5 rounded-sm bg-black/65 p-0.5 text-amber-300 group-hover:opacity-0">
            <Star aria-hidden="true" className="size-3 fill-amber-300" />
          </span>
        ) : null}
      </button>

      {onSelect ? (
        <button
          type="button"
          aria-label={selected ? `Deselect ${file.name}` : `Select ${file.name}`}
          aria-pressed={selected}
          title={selected ? 'Deselect' : 'Select'}
          onClick={(event) => {
            event.stopPropagation()
            onSelect(index, event)
          }}
          className={cn(
            'absolute top-1 left-1 z-10 flex size-5 items-center justify-center rounded-sm border transition-opacity',
            selected
              ? 'border-accent bg-accent text-accent-fg opacity-100'
              : 'border-white/70 bg-black/45 text-transparent opacity-0 group-hover:opacity-100 focus-visible:opacity-100',
            selectionMode && !selected && 'opacity-100',
            'max-md:opacity-100',
          )}
        >
          <Check aria-hidden="true" className="size-3.5" />
        </button>
      ) : null}

      {onAction ? (
        <div className="absolute top-1 right-1 z-10">
          <Menu open={menuOpen} onOpenChange={setMenuOpen}>
            <MenuTrigger asChild>
              <button
                type="button"
                aria-label={`Actions for ${file.name}`}
                title="Actions"
                className={cn(
                  'inline-flex size-6 items-center justify-center rounded-sm bg-black/60 text-white/85',
                  'opacity-0 transition-opacity hover:bg-black/80 group-hover:opacity-100',
                  'focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-accent-ring',
                  'max-md:opacity-100',
                )}
              >
                <MoreVertical aria-hidden="true" className="size-3.5" />
              </button>
            </MenuTrigger>
            <MenuContent>
              <MenuItem
                onSelect={() => onAction(favorite ? 'unfavorite' : 'favorite', file)}
              >
                {favorite ? (
                  <StarOff aria-hidden="true" className="size-4" />
                ) : (
                  <Star aria-hidden="true" className="size-4" />
                )}
                {favorite ? 'Remove favorite' : 'Add to favorites'}
              </MenuItem>
              <MenuItem onSelect={() => onAction('rename', file)}>
                <Pencil aria-hidden="true" className="size-4" />
                Rename
              </MenuItem>
              <MenuItem onSelect={() => onAction('move', file)}>
                <FolderInput aria-hidden="true" className="size-4" />
                Move to…
              </MenuItem>
              <MenuSeparator />
              <MenuItem variant="danger" onSelect={() => onAction('delete', file)}>
                <Trash2 aria-hidden="true" className="size-4" />
                Delete
              </MenuItem>
            </MenuContent>
          </Menu>
        </div>
      ) : null}
    </div>
  )
})
