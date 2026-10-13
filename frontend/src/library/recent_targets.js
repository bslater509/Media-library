// Recent download-target folders, persisted client-side.
//
// The /downloads page shows quick "recent" chips of the last folders the user
// actually queued downloads into, then lets them browse anywhere else. Kept
// DOM-free (pure localStorage) so Vitest can unit-test it directly and the
// library bundle could reuse it later.

const STORAGE_KEY = 'lib_dl_recent_targets';
const MAX_RECENT = 8;

/** Read the saved recent target list (newest first). Never throws. */
export function readRecentTargets() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(item => typeof item === 'string' && item).slice(0, MAX_RECENT);
  } catch (e) {
    return [];
  }
}

/**
 * Record a download into `target` and return the new list (newest first).
 * Re-queuing into an already-recent folder moves it to the front instead of
 * duplicating it.
 */
export function rememberRecentTarget(target) {
  if (!target) return readRecentTargets();
  const rest = readRecentTargets().filter(item => item !== target);
  const updated = [target, ...rest].slice(0, MAX_RECENT);
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
  } catch (e) {
    /* storage full / private mode: chips just won't persist */
  }
  return updated;
}