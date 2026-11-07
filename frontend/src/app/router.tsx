import { lazy, Suspense } from 'react'
import { createBrowserRouter, Navigate, useRouteError } from 'react-router'
import { AlertTriangle } from 'lucide-react'
import { AppShell } from '../components/shell/AppShell'
import { EmptyState } from '../components/ui'
import { BackupsPage } from '../pages/BackupsPage'
import { DedupePage } from '../pages/DedupePage'
import { DownloadsPage } from '../pages/DownloadsPage'
import { EmptyFoldersPage } from '../pages/EmptyFoldersPage'
import { ImagesPage } from '../pages/ImagesPage'
import { FavoritesPage } from '../pages/FavoritesPage'
import { LibraryPage } from '../pages/LibraryPage'
import { NotFoundPage, PageLoading } from '../pages/RoadmapPage'
import { SearchPage } from '../pages/SearchPage'
import { SettingsPage } from '../pages/SettingsPage'
import { SorterPage } from '../pages/SorterPage'
import { ToolsPage } from '../pages/ToolsPage'
import { ZipPage } from '../pages/ZipPage'

const AnalyticsPage = lazy(() =>
  import('../pages/AnalyticsPage').then((module) => ({ default: module.AnalyticsPage })),
)

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
            onClick={() => window.location.assign('/library')}
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
        { path: 'downloads', element: <DownloadsPage /> },
        {
          path: 'analytics',
          element: (
            <Suspense fallback={<PageLoading />}>
              <AnalyticsPage />
            </Suspense>
          ),
        },
        { path: 'tools', element: <ToolsPage /> },
        { path: 'tools/dedupe', element: <DedupePage /> },
        { path: 'tools/images', element: <ImagesPage /> },
        { path: 'tools/empty', element: <EmptyFoldersPage /> },
        { path: 'tools/zip', element: <ZipPage /> },
        { path: 'tools/backup', element: <BackupsPage /> },
        { path: 'settings', element: <SettingsPage /> },
        { path: '*', element: <NotFoundPage /> },
      ],
    },
  ],
)
