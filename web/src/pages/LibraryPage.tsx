import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import {
  AlertTriangle,
  Archive,
  ChevronLeft,
  Folder,
  FolderOpen,
  ImageOff,
  Loader2,
  RefreshCw,
  ZoomIn,
} from 'lucide-react'
import type { FolderEntry } from '../lib/api'
import { cn } from '../lib/cn'
import { TILE_ROW_HEIGHT, useDensityStore, type TileScale } from '../lib/stores'
import { useInfiniteBrowse, useInfo } from '../lib/queries'
import { Badge, Button, EmptyState, IconButton, Segmented, Skeleton } from '../components/ui'
import { JustifiedGrid } from '../components/media/JustifiedGrid'
import { MediaLightbox } from '../components/media/MediaLightbox'

export function LibraryPage() {
  const [params, setParams] = useSearchParams()
  const requestedPath = params.get('path')
  const openPath = params.get('open')
  const info = useInfo()
  const scale = useDensityStore((state) => state.scale)
  const setScale = useDensityStore((state) => state.setScale)
  const rowHeight = TILE_ROW_HEIGHT[scale]

  const query = useInfiniteBrowse(requestedPath)
  const first = query.data?.pages[0]

  const files = useMemo(
    () => query.data?.pages.flatMap((page) => page.files) ?? [],
    [query.data],
  )

  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)

  // A palette result can deep-link straight into the viewer.
  useEffect(() => {
    if (!openPath || !first) return
    const index = files.findIndex((file) => file.path === openPath)
    if (index >= 0) {
      setLightboxIndex(index)
      const next = new URLSearchParams(params)
      next.delete('open')
      setParams(next, { replace: true })
    }
  }, [openPath, first, files, params, setParams])

  const sentinelRef = useRef<HTMLDivElement | null>(null)
  const hasNextPage = query.hasNextPage
  const isFetchingNextPage = query.isFetchingNextPage
  const fetchNextPage = query.fetchNextPage
  useEffect(() => {
    const element = sentinelRef.current
    if (!element) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) {
          void fetchNextPage()
        }
      },
      { rootMargin: '600px 0px' },
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  const navigateTo = (path: string | null) => {
    const next = new URLSearchParams()
    if (path) next.set('path', path)
    setParams(next)
    window.scrollTo({ top: 0 })
  }

  const currentPath = first?.path ?? requestedPath ?? info.data?.library_root ?? ''
  const root = first?.root ?? info.data?.library_root ?? ''
  const breadcrumbs = first?.breadcrumbs ?? []
  const parent =
    currentPath && currentPath !== root
      ? currentPath.slice(0, currentPath.lastIndexOf('/')) || root
      : null

  return (
    <div className="flex flex-col">
      <div className="sticky top-14 z-20 border-b border-border bg-bg/95">
        <div className="flex flex-wrap items-center gap-2 px-3 py-2.5 sm:px-5">
          {parent ? (
            <IconButton
              aria-label="Go up one folder"
              title="Up one folder"
              onClick={() => navigateTo(parent)}
            >
              <ChevronLeft />
            </IconButton>
          ) : null}

          <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
            <ol className="flex min-w-0 items-center gap-1 overflow-hidden text-sm">
              {breadcrumbs.map((crumb, index) => {
                const last = index === breadcrumbs.length - 1
                return (
                  <li key={crumb.path} className="flex min-w-0 items-center gap-1">
                    {index > 0 ? (
                      <span aria-hidden="true" className="text-fg-subtle">
                        /
                      </span>
                    ) : null}
                    {last ? (
                      <span className="truncate font-medium text-fg">{crumb.name}</span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => navigateTo(crumb.path)}
                        className="truncate text-fg-muted hover:text-fg hover:underline"
                      >
                        {crumb.name}
                      </button>
                    )}
                  </li>
                )
              })}
            </ol>
          </nav>

          <div className="flex shrink-0 items-center gap-1.5">
            {first ? (
              <span className="hidden text-xs text-fg-subtle tabular-nums sm:inline">
                {first.total.toLocaleString()} media · {first.folders.length} folders
              </span>
            ) : null}
            <Segmented<TileScale>
              aria-label="Tile size"
              size="sm"
              value={scale}
              onChange={setScale}
              options={[
                { value: 's', label: 'S', title: 'Small tiles' },
                { value: 'm', label: 'M', title: 'Medium tiles' },
                { value: 'l', label: 'L', title: 'Large tiles' },
              ]}
            />
            <IconButton
              aria-label="Refresh folder"
              title="Refresh"
              onClick={() => void query.refetch()}
            >
              <RefreshCw className={cn(query.isFetching && 'animate-spin')} />
            </IconButton>
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-6 px-3 py-4 sm:px-5">
        {query.isError ? (
          <EmptyState
            icon={<AlertTriangle />}
            title="Could not load this folder"
            description={
              query.error instanceof Error ? query.error.message : 'The index did not answer.'
            }
            action={
              <Button variant="secondary" onClick={() => void query.refetch()}>
                Try again
              </Button>
            }
          />
        ) : null}

        {first && !first.indexed ? (
          <div className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-fg-muted">
            <AlertTriangle aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-warning" />
            This folder is not in the index yet. It will appear after the next crawl in
            the current app.
          </div>
        ) : null}

        {query.isLoading ? (
          <LoadingFolder />
        ) : (
          <>
            {first && first.folders.length > 0 ? (
              <section aria-label="Folders">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6">
                  {first.folders.map((folder) => (
                    <FolderCard
                      key={folder.path}
                      folder={folder}
                      onOpen={() => navigateTo(folder.path)}
                    />
                  ))}
                </div>
              </section>
            ) : null}

            <section aria-label="Media" className="flex flex-col gap-3">
              {files.length > 0 ? (
                <>
                  <div className="flex items-baseline justify-between gap-2">
                    <h2 className="text-[13px] font-semibold tracking-wide text-fg-muted uppercase">
                      Media
                    </h2>
                    <span className="text-xs text-fg-subtle tabular-nums">
                      {files.length.toLocaleString()} of {first?.total.toLocaleString()}
                    </span>
                  </div>
                  <JustifiedGrid
                    files={files}
                    targetRowHeight={rowHeight}
                    onOpen={setLightboxIndex}
                  />
                </>
              ) : null}

              {!query.isLoading && files.length === 0 && (first?.folders.length ?? 0) === 0 ? (
                <EmptyState
                  icon={<ImageOff />}
                  title="Nothing here yet"
                  description="This folder has no media and no subfolders in the index."
                />
              ) : null}

              <div ref={sentinelRef} className="flex h-10 items-center justify-center">
                {query.isFetchingNextPage ? (
                  <Loader2 aria-label="Loading more" className="size-4 animate-spin text-fg-subtle" />
                ) : query.hasNextPage ? (
                  <button
                    type="button"
                    onClick={() => void query.fetchNextPage()}
                    className="text-xs text-fg-subtle hover:text-fg"
                  >
                    Load more
                  </button>
                ) : null}
              </div>
            </section>
          </>
        )}
      </div>

      {lightboxIndex !== null ? (
        <MediaLightbox
          files={files}
          index={lightboxIndex}
          onIndexChange={setLightboxIndex}
          onClose={() => setLightboxIndex(null)}
        />
      ) : null}
    </div>
  )
}

function FolderCard({ folder, onOpen }: { folder: FolderEntry; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        'group flex flex-col overflow-hidden rounded-md border border-border bg-surface text-left',
        'transition-colors hover:border-border-strong hover:shadow-e1',
        'outline-none focus-visible:ring-2 focus-visible:ring-accent-ring',
      )}
    >
      <div className="relative aspect-4/3 w-full overflow-hidden bg-surface-2">
        {folder.cover_thumb_url ? (
          <img
            src={folder.cover_thumb_url}
            alt=""
            loading="lazy"
            decoding="async"
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-fg-subtle">
            {folder.is_zip ? (
              <Archive aria-hidden="true" className="size-7" />
            ) : (
              <Folder aria-hidden="true" className="size-7" />
            )}
          </div>
        )}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-14 bg-gradient-to-t from-black/70 to-transparent" />
        <div className="absolute inset-x-2 bottom-2 flex items-center gap-1.5">
          {folder.is_zip ? (
            <Archive aria-hidden="true" className="size-3.5 shrink-0 text-white/80" />
          ) : (
            <FolderOpen aria-hidden="true" className="size-3.5 shrink-0 text-white/80" />
          )}
          <span className="truncate text-xs font-medium text-white">{folder.name}</span>
        </div>
      </div>
      <div className="flex items-center justify-between gap-2 px-2.5 py-2 text-[11px] text-fg-subtle">
        <span className="truncate tabular-nums">
          {folder.file_count.toLocaleString()} files
          {folder.subfolder_count > 0
            ? ` · ${folder.subfolder_count.toLocaleString()} folders`
            : ''}
        </span>
        <Badge variant="outline" className="shrink-0">
          {folder.size_formatted}
        </Badge>
      </div>
    </button>
  )
}

function LoadingFolder() {
  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6">
        {[0, 1, 2, 3, 4, 5, 6, 7].map((card) => (
          <Skeleton key={card} className="aspect-4/3 w-full rounded-md" />
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        {[240, 180, 320, 200, 280].map((height, index) => (
          <Skeleton
            key={index}
            className="rounded-md"
            style={{ height, width: Math.round(height * 1.4) }}
          />
        ))}
      </div>
    </div>
  )
}

/** Small helper the empty state links to. */
export function LibraryHint() {
  return (
    <Link to="/search" className="inline-flex items-center gap-1 text-accent hover:underline">
      <ZoomIn className="size-3.5" /> Search the whole library
    </Link>
  )
}
