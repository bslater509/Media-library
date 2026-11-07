import { Play } from 'lucide-react'
import type { FileEntry } from '../../lib/api'
import { cn } from '../../lib/cn'
import { formatDuration } from '../../lib/format'
import { useWatchHistoryResume } from '../../lib/queries'
import { Button } from '../ui'
import { MediaImage, MediaPlaceholder } from './MediaImage'

interface ContinueCardProps {
  file: FileEntry
  onOpen: (file: FileEntry) => void
}

/**
 * One in-progress video: poster, progress bar and a Resume action.
 * Clicking the poster or the button opens the fullscreen viewer at the
 * stored position.
 */
export function ContinueCard({ file, onOpen }: ContinueCardProps) {
  const percent =
    file.duration && file.duration > 0 && file.seconds_watched
      ? Math.min(100, Math.round((file.seconds_watched / file.duration) * 100))
      : null
  return (
    <div className="flex w-60 shrink-0 flex-col overflow-hidden rounded-md border border-border bg-surface">
      <button
        type="button"
        onClick={() => onOpen(file)}
        title={file.name}
        aria-label={`Resume ${file.name}`}
        className="relative block aspect-video w-full overflow-hidden bg-surface-2 outline-none ring-accent-ring transition-shadow focus-visible:ring-2 hover:shadow-e1"
      >
        {file.has_thumb || file.type === 'image' ? (
          <MediaImage file={file} animateOnHover={false} />
        ) : (
          <MediaPlaceholder />
        )}
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-black/70 to-transparent"
        />
        <span className="pointer-events-none absolute inset-x-2 bottom-1.5 truncate text-left text-[11px] font-medium text-white">
          {file.name}
        </span>
      </button>
      <div aria-hidden="true" className="h-1 w-full bg-surface-3">
        {percent !== null ? (
          <div className="h-full bg-accent" style={{ width: `${percent}%` }} />
        ) : null}
      </div>
      <div className="flex items-center justify-between gap-2 px-2.5 py-2">
        <span className="min-w-0 truncate text-[11px] text-fg-subtle tabular-nums">
          {formatDuration(file.seconds_watched) ?? '0:00'} /{' '}
          {formatDuration(file.duration) ?? '—'}
        </span>
        <Button size="sm" onClick={() => onOpen(file)}>
          <Play /> Resume
        </Button>
      </div>
    </div>
  )
}

interface ContinueRailProps {
  onOpen: (file: FileEntry) => void
  className?: string
}

/**
 * "Continue watching" strip: the most recent in-progress videos, library-wide.
 * Renders nothing while it is loading or empty, so folders that the user
 * never touched stay untouched.
 */
export function ContinueRail({ onOpen, className }: ContinueRailProps) {
  const resume = useWatchHistoryResume(8)
  const files = resume.data?.files ?? []
  if (resume.isLoading || files.length === 0) return null
  return (
    <section aria-label="Continue watching" className={cn('flex flex-col gap-3', className)}>
      <h2 className="text-[13px] font-semibold tracking-wide text-fg-muted uppercase">
        Continue watching
      </h2>
      <div className="flex gap-3 overflow-x-auto pb-1">
        {files.map((file) => (
          <ContinueCard key={file.path} file={file} onOpen={onOpen} />
        ))}
      </div>
    </section>
  )
}
