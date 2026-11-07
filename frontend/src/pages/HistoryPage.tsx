import { useState } from 'react'
import { Link } from 'react-router'
import { Clapperboard, Eye, History as HistoryIcon } from 'lucide-react'
import type { FileEntry } from '../lib/api'
import {
  useWatchHistoryRecent,
  useWatchHistoryResume,
  useWatchHistoryUnseen,
} from '../lib/queries'
import { TILE_ROW_HEIGHT, useDensityStore } from '../lib/stores'
import { formatRelativeTime } from '../lib/format'
import { EmptyState, Segmented, Spinner } from '../components/ui'
import { JustifiedGrid } from '../components/media/JustifiedGrid'
import { MediaLightbox } from '../components/media/MediaLightbox'
import { ContinueCard } from '../components/media/ContinueRail'

type TabKey = 'resume' | 'recent' | 'unwatched'
type TypeFilter = 'all' | 'video' | 'image'

const TABS: Array<{ value: TabKey; label: string }> = [
  { value: 'resume', label: 'Continue' },
  { value: 'recent', label: 'Recently viewed' },
  { value: 'unwatched', label: 'Never watched' },
]

export function HistoryPage() {
  const [tab, setTab] = useState<TabKey>('resume')
  const [type, setType] = useState<TypeFilter>('all')
  const scale = useDensityStore((state) => state.scale)
  const rowHeight = TILE_ROW_HEIGHT[scale]

  const resume = useWatchHistoryResume(200)
  const recent = useWatchHistoryRecent(type, 200)
  const unseen = useWatchHistoryUnseen(type, 200)

  // The viewer is shared across tabs; a resume position only travels with
  // opens from the Continue tab. Switching tab/filter closes the viewer
  // first so it never re-keys onto a foreign list.
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)
  const [startAt, setStartAt] = useState<number | null>(null)

  const changeTab = (next: TabKey) => {
    setTab(next)
    setLightboxIndex(null)
    setStartAt(null)
  }
  const changeType = (next: TypeFilter) => {
    setType(next)
    setLightboxIndex(null)
    setStartAt(null)
  }

  const resumeFiles = resume.data?.files ?? []
  const recentFiles = recent.data?.files ?? []
  const unseenFiles = unseen.data?.files ?? []
  const activeQuery = tab === 'resume' ? resume : tab === 'recent' ? recent : unseen
  const files =
    tab === 'resume' ? resumeFiles : tab === 'recent' ? recentFiles : unseenFiles
  const total = activeQuery.data?.total

  const openResume = (file: FileEntry, index: number) => {
    setStartAt(file.seconds_watched)
    setLightboxIndex(index)
  }
  const openPlain = (index: number) => {
    setStartAt(null)
    setLightboxIndex(index)
  }

  const recentHint = (file: FileEntry) => {
    const when = formatRelativeTime(file.last_viewed_at)
    if (when === null && file.play_count === null) return null
    const plays = file.play_count === null ? null : `${file.play_count.toLocaleString()}×`
    return [plays, when].filter(Boolean).join(' · ') || null
  }

  return (
    <div className="flex flex-col gap-4 px-3 py-4 sm:px-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-base font-semibold tracking-tight">View history</h1>
          <p className="text-xs text-fg-muted">
            {total === undefined
              ? 'What the library has been watching'
              : `${total.toLocaleString()} ${
                  tab === 'resume'
                    ? 'started but not finished'
                    : tab === 'recent'
                      ? 'files with a recorded view'
                      : 'files never opened'
                }`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {tab !== 'resume' ? (
            <Segmented<TypeFilter>
              aria-label="Filter media"
              size="sm"
              value={type}
              onChange={changeType}
              options={[
                { value: 'all', label: 'All' },
                { value: 'video', label: 'Videos' },
                { value: 'image', label: 'Images' },
              ]}
            />
          ) : null}
          <Segmented<TabKey>
            aria-label="History view"
            size="sm"
            value={tab}
            onChange={changeTab}
            options={TABS}
          />
        </div>
      </div>

      {activeQuery.isLoading ? (
        <div className="flex items-center gap-2 py-10 text-sm text-fg-subtle">
          <Spinner /> Loading…
        </div>
      ) : files.length === 0 ? (
        <EmptyState
          icon={
            tab === 'resume' ? (
              <Clapperboard />
            ) : tab === 'recent' ? (
              <HistoryIcon />
            ) : (
              <Eye />
            )
          }
          title={
            tab === 'resume'
              ? 'Nothing in progress'
              : tab === 'recent'
                ? 'No views recorded yet'
                : 'Everything has been watched'
          }
          description={
            tab === 'resume'
              ? 'Play a video in the viewer and it will appear here until it is finished.'
              : tab === 'recent'
                ? 'Open any file in the fullscreen viewer and the visit is recorded here.'
                : 'Files appear here once they are indexed and stay until they are viewed.'
          }
          action={
            <Link to="/library" className="text-sm text-accent hover:underline">
              Browse the library
            </Link>
          }
        />
      ) : tab === 'resume' ? (
        <div className="flex gap-3 overflow-x-auto pb-1">
          {resumeFiles.map((file, index) => (
            <ContinueCard key={file.path} file={file} onOpen={(entry) => openResume(entry, index)} />
          ))}
        </div>
      ) : (
        <JustifiedGrid
          files={files}
          targetRowHeight={rowHeight}
          onOpen={openPlain}
          hintFor={tab === 'recent' ? recentHint : undefined}
        />
      )}

      {lightboxIndex !== null ? (
        <MediaLightbox
          files={files}
          index={lightboxIndex}
          onIndexChange={setLightboxIndex}
          startAt={startAt}
          onClose={() => {
            setLightboxIndex(null)
            setStartAt(null)
          }}
        />
      ) : null}
    </div>
  )
}
