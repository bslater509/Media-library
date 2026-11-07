import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { createPortal } from 'react-dom'
import {
  ChevronLeft,
  ChevronRight,
  Clapperboard,
  Download,
  ExternalLink,
  Film,
  FolderInput,
  FolderOpen,
  Gauge,
  Info,
  Loader2,
  Minimize2,
  Pencil,
  Repeat,
  Star,
  StarOff,
  Trash2,
  X,
} from 'lucide-react'
import { api, type FileEntry, type X265Job } from '../../lib/api'
import { IS_MOUNTED_PREVIEW } from '../../lib/base'
import { cn } from '../../lib/cn'
import { useGenerateAnimated } from '../../lib/queries'
import { PosterChooser } from './PosterChooser'
import {
  codecLabel,
  formatClock,
  formatDuration,
  imageLabel,
  isHevc,
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
  const [x265, setX265] = useState<X265Job | null>(null)
  const [posterVersion, setPosterVersion] = useState(0)
  const [chooserOpen, setChooserOpen] = useState(false)
  const [chooserTime, setChooserTime] = useState(0)
  const generateAnimated = useGenerateAnimated()
  const queryClient = useQueryClient()
  const x265Active = x265?.status === 'queued' || x265?.status === 'running'

  useEffect(() => {
    setPosterVersion(0)
    setChooserOpen(false)
  }, [file.path])

  useEffect(() => {
    let cancelled = false
    setX265(null)
    api
      .x265Status(file.path)
      .then((result) => {
        if (cancelled) return
        if (result.job && ['queued', 'running'].includes(result.job.status)) {
          setX265(result.job)
        }
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [file.path])

  useEffect(() => {
    if (!x265 || !['queued', 'running'].includes(x265.status)) return
    let cancelled = false
    const timer = window.setInterval(async () => {
      try {
        const result = await api.x265Status(file.path)
        if (cancelled || !result.job) return
        setX265(result.job)
        if (result.job.status === 'completed') {
          window.clearInterval(timer)
          toast.success('Converted to H.265')
          void queryClient.invalidateQueries({ queryKey: ['browse'] })
          onClose()
        } else if (['failed', 'cancelled'].includes(result.job.status)) {
          window.clearInterval(timer)
          if (result.job.status === 'failed') {
            toast.error(result.job.error || 'Conversion failed')
          }
        }
      } catch {
        /* transient poll error; the next tick retries */
      }
    }, 1000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [x265?.id, x265?.status, file.path, onClose, queryClient])

  const startConversion = async () => {
    try {
      const job = await api.x265Start(file.path)
      setX265(job)
      toast.info('Converting to H.265…')
    } catch (error) {
      try {
        const result = await api.x265Status(file.path)
        if (result.job && ['queued', 'running'].includes(result.job.status)) {
          setX265(result.job)
          return
        }
      } catch {
        /* fall through to the error toast */
      }
      toast.error(error instanceof Error ? error.message : 'Could not start conversion')
    }
  }

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
      // The poster chooser is its own modal above the viewer (it handles
      // Escape itself); navigation keys must not act through it.
      if (chooserOpen) return
      const target = event.target
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT')
      ) {
        // A nested dialog owns the focus (e.g. rename); do not turn typed
        // keys into navigation or playback.
        return
      }
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
  }, [chooserOpen, next, onClose, previous])

  if (!file) return null

  const duration = formatDuration(file.duration)
  const resolution =
    file.type === 'image'
      ? imageLabel(file.width, file.height)
      : resolutionLabel(file.width, file.height)
  const codec = codecLabel(file.video_codec)
  const captured = formatClock(file.capture_time)
  const isVideo = file.type === 'video'
  const videoSrc = file.playable
    ? file.media_url
    : (file.transcode_url ?? file.media_url)
  const posterSrc = file.has_thumb
    ? `${file.thumb_url}${posterVersion ? `&v=${posterVersion}` : ''}`
    : undefined

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

        {isVideo ? (
          <video
            key={file.id}
            ref={videoRef}
            src={videoSrc}
            poster={posterSrc}
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
            src={posterSrc ?? file.media_url}
            alt={file.name}
            className="max-h-full max-w-full object-contain"
            onClick={(event) => event.stopPropagation()}
          />
        )}

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

      {x265 ? (
        <div className="absolute inset-x-3 bottom-28 z-20 mx-auto max-w-lg rounded-md border border-white/15 bg-slate-900/95 p-3 text-white shadow-e2 sm:inset-x-auto sm:right-4 sm:bottom-32 sm:w-96">
          <div className="flex items-center gap-2">
            <Loader2
              aria-hidden="true"
              className={
                x265Active ? 'size-4 animate-spin text-accent' : 'size-4 text-white/50'
              }
            />
            <p className="min-w-0 flex-1 truncate text-xs font-medium">
              {x265.status === 'completed'
                ? 'Converted to H.265'
                : x265.status === 'failed'
                  ? 'Conversion failed'
                  : x265.status === 'cancelled'
                    ? 'Conversion cancelled'
                    : 'Converting to H.265…'}
            </p>
            <span className="shrink-0 font-mono text-[11px] tabular-nums text-white/60">
              {Math.round(x265.percent)}%
            </span>
            {!x265Active ? (
              <button
                type="button"
                aria-label="Dismiss conversion status"
                onClick={() => setX265(null)}
                className="text-white/55 hover:text-white"
              >
                <X className="size-3.5" />
              </button>
            ) : null}
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/15">
            <div
              className="h-full rounded-full bg-accent transition-[width] duration-500"
              style={{ width: `${Math.max(2, Math.min(100, x265.percent))}%` }}
            />
          </div>
          <div className="mt-1.5 flex items-center justify-between gap-2 text-[10px] text-white/55">
            <span className="truncate">
              {x265.error
                ? x265.error
                : [
                    x265.fps ? `${x265.fps} fps` : '',
                    x265.speed ? `${x265.speed}` : '',
                    x265.eta ? `ETA ${x265.eta}` : '',
                  ]
                    .filter(Boolean)
                    .join(' · ') || 'One job runs at a time'}
            </span>
            {x265Active ? (
              <button
                type="button"
                onClick={() => {
                  void api.x265Cancel(x265.id).catch(() => {})
                  toast.info('Cancelling…')
                }}
                className="shrink-0 rounded-sm border border-white/20 px-2 py-0.5 text-white/75 hover:bg-white/10 hover:text-white"
              >
                Cancel
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

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
                  aria-label="Choose poster frame"
                  title="Thumbnail chooser"
                  size={32}
                  className="text-white/75 hover:bg-white/10 hover:text-white"
                  onClick={() => {
                    setChooserTime(videoRef.current?.currentTime ?? 0)
                    setChooserOpen(true)
                  }}
                >
                  <Film />
                </IconButton>
                <IconButton
                  aria-label="Generate animated preview"
                  title="Generate animated preview"
                  size={32}
                  className="text-white/75 hover:bg-white/10 hover:text-white"
                  onClick={() =>
                    generateAnimated.mutate(file.path, {
                      onSuccess: (status) =>
                        toast.success(
                          status.queued
                            ? 'Animated preview queued'
                            : 'Animated preview ready',
                        ),
                      onError: (error) =>
                        toast.error(
                          error instanceof Error
                            ? error.message
                            : 'Could not generate the preview',
                        ),
                    })
                  }
                  disabled={generateAnimated.isPending}
                >
                  {generateAnimated.isPending ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Clapperboard />
                  )}
                </IconButton>
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
            {isVideo && !isHevc(file.video_codec) ? (
              <IconButton
                aria-label="Convert to H.265 / HEVC"
                title="Convert to x265 / HEVC"
                size={32}
                className="text-white/75 hover:bg-white/10 hover:text-white"
                onClick={() => void startConversion()}
                disabled={x265Active}
              >
                {x265Active ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Minimize2 />
                )}
              </IconButton>
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
            {IS_MOUNTED_PREVIEW ? (
              <a
                href={`/?folder=${encodeURIComponent(file.folder_path)}`}
                aria-label="Manage in the current app"
                title="Open this folder in the current app (move, rename, delete)"
                className="inline-flex size-8 items-center justify-center rounded-sm text-white/75 hover:bg-white/10 hover:text-white"
                onClick={(event) => event.stopPropagation()}
              >
                <FolderOpen className="size-4" />
              </a>
            ) : null}
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

      {chooserOpen ? (
        <PosterChooser
          file={file}
          currentTime={chooserTime}
          onClose={() => setChooserOpen(false)}
          onApplied={(stamp) => setPosterVersion(stamp)}
        />
      ) : null}
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
