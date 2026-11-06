import { useEffect, useMemo, useState } from 'react'
import { Film, ImageOff } from 'lucide-react'
import type { FileEntry } from '../../lib/api'
import { cn } from '../../lib/cn'

interface MediaImageProps {
  file: FileEntry
  className?: string
  /** Play the animated preview while hovered (desktop only). */
  animateOnHover?: boolean
}

function useHoverCapable(): boolean {
  const [capable, setCapable] = useState(false)
  useEffect(() => {
    const query = window.matchMedia('(hover: hover) and (pointer: fine)')
    setCapable(query.matches)
    const onChange = () => setCapable(query.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])
  return capable
}

/**
 * Poster for a tile or lightbox.
 *
 * Prefers the cached poster, falls back to the original image (the browser
 * scales it), and only shows a placeholder for a video whose poster has not
 * been generated yet.
 */
export function MediaImage({ file, className, animateOnHover = true }: MediaImageProps) {
  const hoverCapable = useHoverCapable()
  const [failed, setFailed] = useState(false)
  const [hovering, setHovering] = useState(false)

  const baseSrc = useMemo(() => {
    if (failed && file.type === 'image') return file.media_url
    if (file.has_thumb) return file.thumb_url
    if (file.type === 'image') return file.media_url
    return null
  }, [failed, file])

  const animated =
    animateOnHover &&
    hoverCapable &&
    hovering &&
    file.type === 'video' &&
    file.has_animated &&
    Boolean(file.animated_url)

  useEffect(() => {
    if (!animated) setHovering(false)
  }, [animated])

  if (!baseSrc) {
    return (
      <div
        className={cn(
          'flex h-full w-full items-center justify-center bg-surface-3 text-fg-subtle',
          className,
        )}
      >
        <Film aria-hidden="true" className="size-6 opacity-60" />
      </div>
    )
  }

  if (animated && file.animated_url) {
    return (
      <img
        src={file.animated_url}
        alt={file.name}
        className={cn('h-full w-full object-cover', className)}
        draggable={false}
        onMouseLeave={() => setHovering(false)}
      />
    )
  }

  return (
    <img
      src={baseSrc}
      alt={file.name}
      loading="lazy"
      decoding="async"
      draggable={false}
      className={cn('h-full w-full object-cover', className)}
      onMouseEnter={() => hoverCapable && setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      onError={() => setFailed(true)}
    />
  )
}

interface PlaceholderProps {
  className?: string
}

/** Explicit "no preview" block for a file with no poster and no usable image. */
export function MediaPlaceholder({ className }: PlaceholderProps) {
  return (
    <div
      className={cn(
        'flex h-full w-full flex-col items-center justify-center gap-1.5 bg-surface-3 text-fg-subtle',
        className,
      )}
    >
      <ImageOff aria-hidden="true" className="size-5" />
      <span className="text-[10px] tracking-wide uppercase">No preview</span>
    </div>
  )
}
