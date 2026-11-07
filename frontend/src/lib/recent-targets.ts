/** Recent download targets, persisted like v1's chips (max 8). */

const KEY = 'v2_dl_recent_targets'
const LIMIT = 8

export function readRecentTargets(): string[] {
  try {
    const raw = window.localStorage.getItem(KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((value): value is string => typeof value === 'string').slice(0, LIMIT)
  } catch {
    return []
  }
}

export function rememberTarget(path: string): string[] {
  const current = readRecentTargets().filter((item) => item !== path)
  const next = [path, ...current].slice(0, LIMIT)
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    /* storage can be unavailable; the chip list simply does not persist */
  }
  return next
}

export function targetName(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts[parts.length - 1] ?? path
}
