import { lazy, Suspense } from 'react'
import { createBrowserRouter, Navigate, useRouteError } from 'react-router'
import { AlertTriangle } from 'lucide-react'
import { AppShell } from '../components/shell/AppShell'
import { EmptyState } from '../components/ui'
import { DownloadsPage } from '../pages/DownloadsPage'
import { FavoritesPage } from '../pages/FavoritesPage'
import { LibraryPage } from '../pages/LibraryPage'
import { NotFoundPage, PageLoading } from '../pages/RoadmapPage'
import { SearchPage } from '../pages/SearchPage'
import { SettingsPage } from '../pages/SettingsPage'
import { SorterPage } from '../pages/SorterPage'

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
        { path: 'downloads', element: <DownloadsPage /> },
        {
          path: 'analytics',
          element: (
            <Suspense fallback={<PageLoading />}>
              <AnalyticsPage />
            </Suspense>
          ),
        },
        { path: 'settings', element: <SettingsPage /> },
        { path: '*', element: <NotFoundPage /> },
      ],
    },
  ],
  { basename: '/v2' },
)
