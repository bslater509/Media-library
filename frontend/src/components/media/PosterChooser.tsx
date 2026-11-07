import { useEffect, useState } from 'react'
import { Check, Clock, Film, Loader2, X } from 'lucide-react'
import { toast } from 'sonner'
import type { FileEntry } from '../../lib/api'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { cn } from '../../lib/cn'
import { useSetPoster } from '../../lib/queries'
import { Button } from '../ui'

interface PosterChooserProps {
  file: FileEntry
  currentTime: number
  onClose: () => void
  onApplied: (thumbMtime: number) => void
}

/** Pick a video's poster from sampled frames or the current playhead. */
export function PosterChooser({
  file,
  currentTime,
  onClose,
  onApplied,
}: PosterChooserProps) {
  const candidates = useQuery({
    queryKey: ['thumbnails', 'candidates', file.path],
    queryFn: () => api.thumbnailCandidates(file.path, 30),
    staleTime: 60_000,
  })
  const setPoster = useSetPoster()
  const [timestamp, setTimestamp] = useState<number | null>(null)

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const apply = async () => {
    if (timestamp === null) return
    try {
      const result = await setPoster.mutateAsync({ path: file.path, timestamp })
      toast.success('Poster updated')
      onApplied(result.thumb_mtime)
      onClose()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not set the poster')
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Choose a poster frame"
      className="fixed inset-0 z-[120] flex items-end justify-center bg-black/70 p-0 sm:items-center sm:p-6"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="flex max-h-[92dvh] w-full max-w-3xl flex-col overflow-hidden rounded-t-xl border border-border bg-surface text-fg shadow-e2 sm:rounded-xl">
        <header className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Film aria-hidden="true" className="size-4 text-fg-subtle" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold">Choose a poster frame</p>
            <p className="truncate text-[11px] text-fg-subtle">{file.name}</p>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="rounded-sm p-1 text-fg-subtle hover:bg-surface-2 hover:text-fg"
          >
            <X className="size-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {candidates.isLoading ? (
            <div className="flex items-center justify-center py-16 text-fg-subtle">
              <Loader2 aria-label="Loading frames" className="size-5 animate-spin" />
            </div>
          ) : candidates.isError ? (
            <p className="py-10 text-center text-sm text-fg-muted">
              Could not read the frames for this clip.
            </p>
          ) : (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-5">
              {(candidates.data?.frames ?? []).map((frame) => {
                const active =
                  timestamp !== null && Math.abs(timestamp - frame.timestamp) < 0.001
                return (
                  <button
                    key={frame.timestamp}
                    type="button"
                    onClick={() => setTimestamp(frame.timestamp)}
                    aria-pressed={active}
                    className={cn(
                      'group relative aspect-video overflow-hidden rounded-md border bg-surface-2',
                      active
                        ? 'border-accent ring-2 ring-accent'
                        : 'border-border hover:border-border-strong',
                    )}
                  >
                    <img
                      src={frame.url}
                      alt={`Frame at ${frame.label}`}
                      loading="lazy"
                      className="size-full object-cover"
                    />
                    <span className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-gradient-to-t from-black/80 to-transparent px-1.5 pt-4 pb-1 text-[10px] font-medium text-white">
                      <span className="inline-flex items-center gap-1 tabular-nums">
                        <Clock aria-hidden="true" className="size-2.5" />
                        {frame.label}
                      </span>
                      {active ? (
                        <Check aria-hidden="true" className="size-3 text-accent" />
                      ) : null}
                    </span>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-3">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setTimestamp(Math.max(0, currentTime))}
          >
            Use current position ({currentTime.toFixed(1)}s)
          </Button>
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button
              onClick={() => void apply()}
              disabled={timestamp === null || setPoster.isPending}
            >
              {setPoster.isPending ? 'Saving…' : 'Set poster'}
            </Button>
          </div>
        </footer>
      </div>
    </div>
  )
}
