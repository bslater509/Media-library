/** First-run tour helpers: persistence plus the capability map it renders. */

export const ONBOARDING_KEY = 'v2_onboarding_done'

export function hasSeenOnboarding(): boolean {
  try {
    return window.localStorage.getItem(ONBOARDING_KEY) === '1'
  } catch {
    return false
  }
}

export function markOnboardingSeen(): void {
  try {
    window.localStorage.setItem(ONBOARDING_KEY, '1')
  } catch {
    /* storage may be unavailable; the tour simply shows again */
  }
}

export interface Capability {
  key: string
  label: string
  available: boolean
}

/** Rows that are not feature flags: still owned by the current app. */
const PENDING: Capability[] = [
  {
    key: 'workers',
    label: 'Background probes, thumbnail sweeps and auto-transcoding',
    available: false,
  },

]

export function buildCapabilities(
  features: Record<string, boolean> | undefined,
): { available: Capability[]; pending: Capability[] } {
  const flags = features ?? {}
  const available: Capability[] = [
    { key: 'browse', label: 'Browse, gallery and lightbox', available: !!flags.browse },
    { key: 'search', label: 'Search names and folders', available: !!flags.search },
    { key: 'favorites', label: 'Favorites', available: !!flags.favorites },
    { key: 'uploads', label: 'Resumable uploads', available: !!flags.uploads },
    { key: 'manage', label: 'Rename, move, delete, folders', available: !!flags.manage },
    { key: 'sorter', label: 'Keyboard video sorter', available: !!flags.sorter },
    { key: 'downloads', label: 'URL / playlist downloads', available: !!flags.downloads },
    { key: 'dedupe', label: 'Duplicate detection', available: !!flags.dedupe },
    { key: 'webp', label: 'WebP optimizer', available: !!flags.webp },
    { key: 'zip', label: 'ZIP browse, compress, unpack', available: !!flags.zip },
    { key: 'analytics', label: 'Analytics dashboard', available: !!flags.analytics },
    { key: 'backups', label: 'Library snapshots', available: !!flags.backups },
    { key: 'x265', label: 'In-place x265 / HEVC conversion', available: !!flags.x265 },
  ].filter((row) => row.available)
  return { available, pending: PENDING }
}
