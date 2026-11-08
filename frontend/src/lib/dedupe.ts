import type { FileEntry } from './api'

/** Default keep choice for a duplicate group: the largest file, first on a tie. */
export function defaultKeepPath(files: FileEntry[]): string {
  let best: FileEntry | undefined
  for (const file of files) {
    if (!best || file.size > best.size) best = file
  }
  return best?.path ?? ''
}
