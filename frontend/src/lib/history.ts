/**
 * Watch-history recording for the fullscreen viewer.
 *
 * `createViewRecorder` is the framework-free state machine (unit tested);
 * `useViewRecorder` wires it to the viewer's own `<video>` element. Recording
 * is fire-and-forget: a deleted file (404) or a read-only index (503) degrades
 * silently and the viewer keeps working.
 *
 * Cadence and play counting, matching the backend rollup (a high-water mark
 * for progress; `play_count` only moves on a bumping record):
 * - an image records `0` seconds as soon as the viewer shows it, bumping —
 *   that open counts as the play,
 * - a video checks in ~10 s after playback starts (with the position at that
 *   moment, forwarded on every `timeupdate`) with `bump: true`, so this one
 *   record counts the play,
 * - every later record of the same session — a repeat check-in after a
 *   pause, or the closing record with the final position — goes out with
 *   `bump: false`, so a single viewing never counts twice,
 * - unless the viewer closes before any check-in fired: the early-close
 *   record then bumps so the play still counts.
 */
import { useEffect, type RefObject } from 'react'
import { useRecordView } from './queries'
import type { FileEntry } from './api'

export type RecordFn = (path: string, seconds: number, bump: boolean) => void

export interface ViewRecorder {
  /** The viewer opened on this file. */
  opened: () => void
  /** The video started playing; schedules the ~10 s check-in. */
  playing: (positionSeconds: number) => void
  /** The playback position changed. */
  position: (seconds: number) => void
  /** The viewer closed or moved to another file. */
  closing: () => void
}

export function createViewRecorder(
  path: string,
  isVideo: boolean,
  options: {
    record: RecordFn
    /** Delay of the mid-playback check-in. */
    checkInMs?: number
  },
): ViewRecorder {
  const checkInMs = options.checkInMs ?? 10_000
  let position = 0
  let checkIn: ReturnType<typeof setTimeout> | null = null
  let closeRecorded = false
  // The session's first record is the one that counts the play; follow-ups
  // stay non-bumping. Flipped by whichever record comes first.
  let playCounted = false

  return {
    opened: () => {
      // Images have no playback to summarise: one record with no seconds.
      // It is also the session's bump, so the open counts as a play.
      if (!isVideo) {
        playCounted = true
        options.record(path, 0, true)
      }
    },
    playing: (seconds) => {
      position = seconds
      if (checkIn === null) {
        checkIn = setTimeout(() => {
          checkIn = null
          const bump = !playCounted
          playCounted = true
          options.record(path, position, bump)
        }, checkInMs)
      }
    },
    position: (seconds) => {
      position = seconds
    },
    closing: () => {
      if (checkIn !== null) {
        clearTimeout(checkIn)
        checkIn = null
      }
      // The closing record is the final position; it must fire at most once
      // per opened file (the placeholder close in React calls the cleanup
      // again in StrictMode-like double effects). Closed before the check-in
      // fired: no bumped record went out yet, so this one bumps instead —
      // the play still counts even though nothing was watched.
      if (closeRecorded || !isVideo) return
      closeRecorded = true
      const bump = !playCounted
      playCounted = true
      options.record(path, position, bump)
    },
  }
}

/**
 * Records watch history while the lightbox shows `file`.
 *
 * Listens only to the element behind `videoRef` — the viewer's own media
 * element (it carries `key={file.id}`, so it is remounted per file) — never
 * to arbitrary `<video>` elements on the page.
 */
export function useViewRecorder(
  file: FileEntry | null,
  videoRef: RefObject<HTMLVideoElement | null>,
) {
  const recordView = useRecordView()
  const fileId = file?.id ?? null

  useEffect(() => {
    if (!file || fileId === null) return
    const isVideo = file.type === 'video'
    const recorder = createViewRecorder(file.path, isVideo, {
      record: (path, seconds, bump) => {
        // Fire-and-forget: react-query stores the error state, nobody throws.
        recordView.mutate({ path, seconds, bump }, { onError: () => {} })
      },
    })
    recorder.opened()

    const element = videoRef.current
    if (!element) {
      // An image has no media element to observe: open and close it.
      return () => recorder.closing()
    }
    const target: HTMLVideoElement = element
    const onTimeUpdate = () => recorder.position(target.currentTime)
    const onPlay = () => recorder.playing(target.currentTime)
    target.addEventListener('timeupdate', onTimeUpdate)
    target.addEventListener('play', onPlay)
    // Autoplay can start before these listeners attach; the check-in
    // timer then arms even though no `play` event was ever seen.
    if (!target.paused) onPlay()
    return () => {
      target.removeEventListener('timeupdate', onTimeUpdate)
      target.removeEventListener('play', onPlay)
      // Records the final position when the viewer closes — or when the
      // user steps to the next file (the <video> is remounted for it).
      recorder.closing()
    }
    // file.id covers path changes; the object identity changes on refetches,
    // which must not re-arm (and re-record) the recorder mid-viewing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileId, videoRef, recordView.mutate])
}
