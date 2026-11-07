/** Small shared formatting helpers (no React, no DOM). */

export function formatDuration(seconds: number | null | undefined): string | null {
  if (!seconds || seconds <= 0 || !Number.isFinite(seconds)) return null
  const total = Math.round(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
  }
  return `${minutes}:${String(secs).padStart(2, '0')}`
}

/** Short resolution label from probed dimensions (longest edge for video). */
export function resolutionLabel(
  width: number | null | undefined,
  height: number | null | undefined,
): string | null {
  if (!width || !height) return null
  const longest = Math.max(width, height)
  const shortest = Math.min(width, height)
  if (longest >= 3800 || shortest >= 2100) return '4K'
  if (longest >= 2500 || shortest >= 1400) return '1440p'
  if (longest >= 1900 || shortest >= 1000) return '1080p'
  if (longest >= 1200 || shortest >= 700) return '720p'
  if (longest >= 600) return 'SD'
  return `${width}×${height}`
}

/** Images read better in megapixels than in a video resolution tier. */
export function imageLabel(
  width: number | null | undefined,
  height: number | null | undefined,
): string | null {
  if (!width || !height) return null
  const megapixels = (width * height) / 1_000_000
  if (megapixels < 0.1) return `${width}×${height}`
  const rounded = megapixels >= 10 ? Math.round(megapixels) : Number(megapixels.toFixed(1))
  return `${rounded} MP`
}

export function codecLabel(codec: string | null | undefined): string | null {
  if (!codec) return null
  const normalised = codec.toLowerCase()
  if (normalised === 'h264' || normalised === 'avc1') return 'H.264'
  if (normalised === 'hevc' || normalised === 'h265') return 'H.265'
  if (normalised === 'vp9') return 'VP9'
  if (normalised === 'av1') return 'AV1'
  if (normalised === 'mpeg4') return 'MPEG-4'
  return codec.toUpperCase()
}

export function isHevc(codec: string | null | undefined): boolean {
  if (!codec) return false
  const normalised = codec.toLowerCase()
  return normalised === 'hevc' || normalised === 'h265'
}

export function formatClock(timestamp: number | null | undefined): string | null {
  if (!timestamp) return null
  return new Date(timestamp * 1000).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

/** Seconds-epoch → "3 hours ago" (Intl.RelativeTimeFormat; null when unknown). */
export function formatRelativeTime(timestamp: number | null | undefined): string | null {
  if (!timestamp || timestamp <= 0) return null
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  const deltaSeconds = (timestamp * 1000 - Date.now()) / 1000
  const minutes = deltaSeconds / 60
  if (Math.abs(minutes) < 1) return formatter.format(Math.round(deltaSeconds), 'second')
  if (Math.abs(minutes) < 60) return formatter.format(Math.round(minutes), 'minute')
  const hours = minutes / 60
  if (Math.abs(hours) < 24) return formatter.format(Math.round(hours), 'hour')
  const days = hours / 24
  if (Math.abs(days) < 7) return formatter.format(Math.round(days), 'day')
  if (Math.abs(days) < 31) return formatter.format(Math.round(days / 7), 'week')
  if (Math.abs(days) < 365) return formatter.format(Math.round(days / 30.44), 'month')
  return formatter.format(Math.round(days / 365.25), 'year')
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/** Human-readable byte count: "483 B", "12.4 MB". */
export function formatBytes(value: number): string {
  if (!value) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unit]}`
}
