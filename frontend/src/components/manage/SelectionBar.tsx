import {
  FolderInput,
  ImageDown,
  RefreshCw,
  Pencil,
  Star,
  Trash2,
  X,
} from 'lucide-react'
import { Button, IconButton } from '../ui'

interface SelectionBarProps {
  count: number
  total: number
  allSelected: boolean
  allFavorite: boolean
  imageCount: number
  busy?: boolean
  onSelectAll: () => void
  onClear: () => void
  onFavorite: () => void
  onMove: () => void
  onRename: () => void
  onWebp: () => void
  onDelete: () => void
  onRegeneratePosters?: () => void
}

/**
 * Floating bulk-action bar for a multi-file selection. Sits above the phone
 * bottom navigation and clear of the desktop edges.
 */
export function SelectionBar({
  count,
  total,
  allSelected,
  allFavorite,
  imageCount,
  busy = false,
  onSelectAll,
  onClear,
  onFavorite,
  onMove,
  onRename,
  onWebp,
  onDelete,
  onRegeneratePosters,
}: SelectionBarProps) {
  return (
    <div
      role="toolbar"
      aria-label="Selected media actions"
      className="fixed inset-x-0 bottom-16 z-30 mx-auto flex w-fit max-w-[calc(100%-1rem)] flex-wrap items-center justify-center gap-1 rounded-xl border border-border bg-surface px-2 py-1.5 shadow-e2 md:bottom-6"
    >
      <span className="px-1.5 text-xs font-medium text-fg tabular-nums">
        {count.toLocaleString()} selected
      </span>

      <Button
        variant="ghost"
        size="sm"
        className="hidden sm:inline-flex"
        onClick={onSelectAll}
        disabled={busy || allSelected}
      >
        {allSelected ? 'All selected' : `Select all ${total.toLocaleString()}`}
      </Button>

      <IconButton
        aria-label="Add to favorites"
        title={allFavorite ? 'Remove from favorites' : 'Add to favorites'}
        onClick={onFavorite}
        disabled={busy}
      >
        <Star className={allFavorite ? 'fill-amber-400 text-amber-400' : undefined} />
      </IconButton>
      <IconButton
        aria-label="Move selected files"
        title="Move to…"
        onClick={onMove}
        disabled={busy}
      >
        <FolderInput />
      </IconButton>
      <IconButton
        aria-label="Rename selected files"
        title="Modify names"
        onClick={onRename}
        disabled={busy}
      >
        <Pencil />
      </IconButton>
      <IconButton
        aria-label="Optimise selected images to WebP"
        title={imageCount ? `Optimise ${imageCount} image${imageCount === 1 ? '' : 's'} to WebP` : 'No selected images'}
        onClick={onWebp}
        disabled={busy || imageCount === 0}
      >
        <ImageDown />
      </IconButton>
      {onRegeneratePosters ? (
        <IconButton
          aria-label="Regenerate selected video thumbnails"
          title="Regenerate video thumbnails"
          onClick={onRegeneratePosters}
          disabled={busy}
        >
          <RefreshCw />
        </IconButton>
      ) : null}
      <IconButton
        aria-label="Delete selected files"
        title="Delete"
        variant="danger"
        onClick={onDelete}
        disabled={busy}
      >
        <Trash2 />
      </IconButton>
      <IconButton aria-label="Clear selection" title="Clear selection" onClick={onClear}>
        <X />
      </IconButton>
    </div>
  )
}
