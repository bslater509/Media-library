import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  FolderInput,
  FolderOpen,
  Gauge,
  Info,
  Pencil,
  Repeat,
  Star,
  StarOff,
  Trash2,
  X,
} from 'lucide-react'
import type { FileEntry } from '../../lib/api'
import { cn } from '../../lib/cn'
import {
  codecLabel,
  formatClock,
  formatDuration,
  imageLabel,
  resolutionLabel,
} from '../../lib/format'
import type { FileAction } from './MediaTile'
import { IconButton } from '../ui'

interface MediaLightboxProps {
  files: FileEntry[]
  index: number
  onIndexChange: (index: number) => void
  onClose: () => void
  onAction?: (action: FileAction, file: FileEntry) => void
}

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2] as const

/** Fullscreen viewer: byte-range video, poster images, metadata and download. */
export function MediaLightbox({
  files,
  index,
  onIndexChange,
  onClose,
  onAction,
}: MediaLightboxProps) {
  const file = files[index]
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const [loop, setLoop] = useState(false)
  const [speed, setSpeed] = useState<number>(1)
  const [showInfo, setShowInfo] = useState(false)

  const next = useCallback(() => {
    if (index < files.length - 1) onIndexChange(index + 1)
  }, [files.length, index, onIndexChange])

  const previous = useCallback(() => {
    if (index > 0) onIndexChange(index - 1)
  }, [index, onIndexChange])

  const cycleSpeed = useCallback(() => {
    setSpeed((current) => {
      const position = SPEEDS.indexOf(current as (typeof SPEEDS)[number])
      return SPEEDS[(position + 1) % SPEEDS.length]
    })
  }, [])

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = speed
  }, [speed, index])

  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previousOverflow
    }
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
      } else if (event.key === 'ArrowRight') {
        event.preventDefault()
        next()
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault()
        previous()
      } else if (event.key === 'l' || event.key === 'L') {
        setLoop((value) => !value)
      } else if (event.key === 'i' || event.key === 'I') {
        setShowInfo((value) => !value)
      } else if (event.key === ' ' && videoRef.current) {
        event.preventDefault()
        if (videoRef.current.paused) void videoRef.current.play()
        else videoRef.current.pause()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [next, onClose, previous])

  if (!file) return null

  const duration = formatDuration(file.duration)
  const resolution =
    file.type === 'image'
      ? imageLabel(file.width, file.height)
      : resolutionLabel(file.width, file.height)
  const codec = codecLabel(file.video_codec)
  const captured = formatClock(file.capture_time)
  const isVideo = file.type === 'video'

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={file.name}
      className="fixed inset-0 z-[100] flex flex-col bg-black/95 text-white"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <header className="flex shrink-0 items-center gap-2 px-3 py-2 sm:px-4">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{file.name}</p>
          <p className="truncate font-mono text-[11px] text-white/55">{file.path}</p>
        </div>
        <span className="hidden shrink-0 font-mono text-[11px] text-white/55 sm:inline">
          {index + 1} / {files.length}
        </span>
        <IconButton
          aria-label="Close viewer"
          title="Close (Esc)"
          variant="ghost"
          size={36}
          className="text-white/80 hover:bg-white/10 hover:text-white"
          onClick={onClose}
          autoFocus
        >
          <X />
        </IconButton>
      </header>

      <div
        className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden"
        onClick={() => onClose()}
      >
        {index > 0 ? (
          <IconButton
            aria-label="Previous item"
            title="Previous (←)"
            size={36}
            className="absolute left-2 z-10 bg-black/50 text-white hover:bg-black/70"
            onClick={(event) => {
              event.stopPropagation()
              previous()
            }}
          >
            <ChevronLeft />
          </IconButton>
        ) : null}

        {isVideo && file.playable ? (
          <video
            key={file.id}
            ref={videoRef}
            src={file.media_url}
            poster={file.has_thumb ? file.thumb_url : undefined}
            controls
            autoPlay
            loop={loop}
            playsInline
            className="max-h-full max-w-full"
            onClick={(event) => event.stopPropagation()}
          />
        ) : (
          <img
            key={file.id}
            src={file.has_thumb ? file.thumb_url : file.media_url}
            alt={file.name}
            className="max-h-full max-w-full object-contain"
            onClick={(event) => event.stopPropagation()}
          />
        )}

        {isVideo && !file.playable ? (
          <div className="pointer-events-none absolute inset-x-0 bottom-16 flex justify-center">
            <p className="rounded-md bg-black/80 px-3 py-2 text-xs text-amber-200">
              This clip needs transcoding before the browser can play it. Use the
              current app for the converted stream.
            </p>
          </div>
        ) : null}

        {index < files.length - 1 ? (
          <IconButton
            aria-label="Next item"
            title="Next (→)"
            size={36}
            className="absolute right-2 z-10 bg-black/50 text-white hover:bg-black/70"
            onClick={(event) => {
              event.stopPropagation()
              next()
            }}
          >
            <ChevronRight />
          </IconButton>
        ) : null}
      </div>

      <footer className="shrink-0 border-t border-white/10 bg-black/70">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-[11px] text-white/70 sm:px-4">
          <span className="font-mono">{file.size_formatted}</span>
          {resolution ? <span className="text-white/90">{resolution}</span> : null}
          {codec ? (
            <span
              className={cn(
                'rounded-sm px-1.5 py-0.5 text-[10px] font-semibold',
                codec === 'H.265'
                  ? 'bg-emerald-600/80 text-white'
                  : 'bg-white/10 text-white/85',
              )}
            >
              {codec}
            </span>
          ) : null}
          {duration ? <span>{duration}</span> : null}
          {captured ? <span>{captured}</span> : null}
          <span className="ml-auto flex items-center gap-1">
            {isVideo ? (
              <>
                <IconButton
                  aria-label="Playback speed"
                  title={`Speed ${speed}× (tap to change)`}
                  size={32}
                  className="text-white/75 hover:bg-white/10 hover:text-white"
                  onClick={cycleSpeed}
                >
                  <Gauge />
                </IconButton>
                <span className="w-8 text-center font-mono">{speed}×</span>
                <IconButton
                  aria-label="Toggle loop"
                  title="Loop (L)"
                  size={32}
                  className={cn(
                    'text-white/75 hover:bg-white/10 hover:text-white',
                    loop && 'bg-white/15 text-white',
                  )}
                  onClick={() => setLoop((value) => !value)}
                >
                  <Repeat />
                </IconButton>
              </>
            ) : null}
            <IconButton
              aria-label="Toggle details"
              title="Details (I)"
              size={32}
              className="text-white/75 hover:bg-white/10 hover:text-white"
              onClick={() => setShowInfo((value) => !value)}
            >
              <Info />
            </IconButton>
            {onAction ? (
              <>
                <IconButton
                  aria-label={file.is_favorite ? 'Remove favorite' : 'Add favorite'}
                  title={file.is_favorite ? 'Remove favorite' : 'Add favorite'}
                  size={32}
                  className={cn(
                    'text-white/75 hover:bg-white/10 hover:text-white',
                    file.is_favorite && 'text-amber-300',
                  )}
                  onClick={() => onAction(file.is_favorite ? 'unfavorite' : 'favorite', file)}
                >
                  {file.is_favorite ? <Star className="fill-current" /> : <StarOff />}
                </IconButton>
                <IconButton
                  aria-label="Rename file"
                  title="Rename"
                  size={32}
                  className="text-white/75 hover:bg-white/10 hover:text-white"
                  onClick={() => onAction('rename', file)}
                >
                  <Pencil />
                </IconButton>
                <IconButton
                  aria-label="Move file"
                  title="Move to…"
                  size={32}
                  className="text-white/75 hover:bg-white/10 hover:text-white"
                  onClick={() => onAction('move', file)}
                >
                  <FolderInput />
                </IconButton>
                <IconButton
                  aria-label="Delete file"
                  title="Delete"
                  variant="danger"
                  size={32}
                  className="text-danger/90 hover:bg-danger/20"
                  onClick={() => onAction('delete', file)}
                >
                  <Trash2 />
                </IconButton>
              </>
            ) : null}
            <a
              href={`/?folder=${encodeURIComponent(file.folder_path)}`}
              aria-label="Manage in the current app"
              title="Open this folder in the current app (move, rename, delete)"
              className="inline-flex size-8 items-center justify-center rounded-sm text-white/75 hover:bg-white/10 hover:text-white"
              onClick={(event) => event.stopPropagation()}
            >
              <FolderOpen className="size-4" />
            </a>
            <a
              href={`${file.media_url}&download=true`}
              download={file.name}
              aria-label="Download file"
              title="Download"
              className="inline-flex size-8 items-center justify-center rounded-sm text-white/75 hover:bg-white/10 hover:text-white"
              onClick={(event) => event.stopPropagation()}
            >
              <Download className="size-4" />
            </a>
            <a
              href={file.media_url}
              target="_blank"
              rel="noreferrer"
              aria-label="Open original"
              title="Open original in a new tab"
              className="inline-flex size-8 items-center justify-center rounded-sm text-white/75 hover:bg-white/10 hover:text-white"
              onClick={(event) => event.stopPropagation()}
            >
              <ExternalLink className="size-4" />
            </a>
          </span>
        </div>

        {showInfo ? (
          <dl className="grid grid-cols-1 gap-x-6 gap-y-1 border-t border-white/10 px-3 py-2 text-[11px] sm:grid-cols-2 sm:px-4">
            <InfoRow label="Folder" value={file.folder_path} mono />
            <InfoRow label="Dimensions" value={
              file.width && file.height ? `${file.width} × ${file.height}` : 'Not probed'
            } />
            <InfoRow label="Video codec" value={file.video_codec ?? '—'} />
            <InfoRow label="Audio codec" value={file.audio_codec ?? '—'} />
            <InfoRow label="Frame rate" value={file.fps ? `${file.fps.toFixed(2)} fps` : '—'} />
            <InfoRow label="Duration" value={duration ?? '—'} />
            <InfoRow label="Extension" value={file.ext} />
            <InfoRow label="Modified" value={formatClock(file.mtime) ?? '—'} />
          </dl>
        ) : null}
      </footer>
    </div>,
    document.body,
  )
}

function InfoRow({
  label,
  value,
  mono = false,
}: {
  label: string
  value: string
  mono?: boolean
}) {
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-3">
      <dt className="shrink-0 text-white/45">{label}</dt>
      <dd className={cn('min-w-0 truncate text-right text-white/85', mono && 'font-mono')}>
        {value}
      </dd>
    </div>
  )
}
