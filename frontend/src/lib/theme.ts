export type ThemeMode = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'v2_theme'
export const THEME_MODES: readonly ThemeMode[] = ['light', 'dark', 'system']

const DARK_QUERY = '(prefers-color-scheme: dark)'

function isThemeMode(value: unknown): value is ThemeMode {
  return typeof value === 'string' && (THEME_MODES as readonly string[]).includes(value)
}

/** The persisted preference, defaulting to "system". */
export function getStoredTheme(): ThemeMode {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
    return isThemeMode(stored) ? stored : 'system'
  } catch {
    return 'system'
  }
}

/** What the operating system currently prefers. */
export function getSystemTheme(): ResolvedTheme {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'light'
  return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light'
}

/** Fold "system" down to the theme that actually paints. */
export function resolveTheme(mode: ThemeMode): ResolvedTheme {
  return mode === 'system' ? getSystemTheme() : mode
}

/**
 * Apply a mode to <html>: `.dark` drives the token overrides, `data-theme`
 * records the raw preference for anything that needs to distinguish it.
 * Returns the theme that ended up active.
 */
export function applyTheme(mode: ThemeMode): ResolvedTheme {
  const resolved = resolveTheme(mode)
  const root = document.documentElement
  root.classList.toggle('dark', resolved === 'dark')
  root.dataset.theme = mode
  root.style.colorScheme = resolved
  return resolved
}

/** Persist a preference and apply it immediately. */
export function setStoredTheme(mode: ThemeMode): ResolvedTheme {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, mode)
  } catch {
    /* storage can be unavailable (private mode); still apply for this session */
  }
  return applyTheme(mode)
}

/** Subscribe to OS light/dark flips (a stable snapshot subscription). */
export function subscribeSystemTheme(onChange: () => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return () => {}
  }
  const query = window.matchMedia(DARK_QUERY)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}

/**
 * Re-apply the theme when the OS flips between light and dark. Only matters
 * while the preference is "system"; when `mode` is omitted the stored
 * preference is re-read on every change, so the listener never goes stale.
 * Returns an unsubscribe function.
 */
export function watchSystemTheme(mode?: ThemeMode): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return () => {}
  }

  const query = window.matchMedia(DARK_QUERY)
  const onChange = () => {
    const active = mode ?? getStoredTheme()
    if (active === 'system') applyTheme('system')
  }

  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}
