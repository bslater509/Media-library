import { createBrowserRouter, Navigate, useRouteError } from 'react-router'
import { AlertTriangle } from 'lucide-react'
import { AppShell } from '../components/shell/AppShell'
import { EmptyState } from '../components/ui'
import { FavoritesPage } from '../pages/FavoritesPage'
import { LibraryPage } from '../pages/LibraryPage'
import { NotFoundPage, RoadmapPage } from '../pages/RoadmapPage'
import { SearchPage } from '../pages/SearchPage'
import { SettingsPage } from '../pages/SettingsPage'
import { SorterPage } from '../pages/SorterPage'

function RouteError() {
  const error = useRouteError()
  const message =
    error instanceof Error
      ? error.message
      : typeof error === 'string'
        ? error
        : 'The page failed to render.'

  return (
    <div className="px-4 py-16">
      <EmptyState
        icon={<AlertTriangle />}
        title="Something went wrong"
        description={message}
        action={
          <button
            type="button"
            className="text-sm text-accent hover:underline"
            onClick={() => window.location.assign('/v2/library')}
          >
            Back to the library
          </button>
        }
      />
    </div>
  )
}

export const router = createBrowserRouter(
  [
    {
      path: '/',
      element: <AppShell />,
      errorElement: <RouteError />,
      children: [
        { index: true, element: <Navigate to="/library" replace /> },
        { path: 'library', element: <LibraryPage /> },
        { path: 'search', element: <SearchPage /> },
        { path: 'favorites', element: <FavoritesPage /> },
        { path: 'sorter', element: <SorterPage /> },
        {
          path: 'downloads',
          element: (
            <RoadmapPage
              title="Downloads"
              description="Queue several URLs or playlists at once and watch them land: best-quality video with an automatic gallery-dl fallback for image boards and file hosts."
              available={[
                'Batch queueing with per-URL validation',
                'yt-dlp with gallery-dl fallback',
                'Live speed, ETA and per-job cancel',
                'Retry, paginated history and clear',
              ]}
              v1Path="/downloads"
            />
          ),
        },
        {
          path: 'analytics',
          element: (
            <RoadmapPage
              title="Analytics"
              description="An index-only overview of the whole collection: storage by folder, media mix, largest files, reclaimable space and library health — computed from SQLite, never from a filesystem walk."
              available={[
                'Storage treemap and largest-folder ranking',
                'Media mix and top extensions',
                'Reclaimable space and health checks',
                'Largest-file previews',
              ]}
              v1Path="/analytics"
            />
          ),
        },
        { path: 'settings', element: <SettingsPage /> },
        { path: '*', element: <NotFoundPage /> },
      ],
    },
  ],
  { basename: '/v2' },
)
