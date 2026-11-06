import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import {
  AlertTriangle,
  Archive,
  ArchiveRestore,
  ChevronLeft,
  Folder,
  FolderOpen,
  FolderPlus,
  ImageOff,
  ListChecks,
  Loader2,
  RefreshCw,
  Star,
  UploadCloud,
  ZoomIn,
} from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import type { FileEntry, FolderEntry } from '../lib/api'
import { cn } from '../lib/cn'
import {
  invalidateLibrary,
  useFavoriteBatch,
  useInfiniteBrowse,
  useInfo,
  useMoveFiles,
  useToggleFavorite,
  useWebpOptimize,
} from '../lib/queries'
import { TILE_ROW_HEIGHT, useDensityStore, type TileScale } from '../lib/stores'
import { Badge, Button, EmptyState, IconButton, Segmented, Select, Skeleton } from '../components/ui'
import { api } from '../lib/api'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui'
import { JustifiedGrid } from '../components/media/JustifiedGrid'
import { MediaLightbox } from '../components/media/MediaLightbox'
import type { FileAction } from '../components/media/MediaTile'
import { DeleteDialog, NewFolderDialog, RenameDialog } from '../components/manage/LibraryDialogs'
import { BulkRenameDialog } from '../components/manage/BulkRenameDialog'
import { FolderPicker } from '../components/manage/FolderPicker'
import { SelectionBar } from '../components/manage/SelectionBar'
import { UploadDialog } from '../components/manage/UploadDialog'

export function LibraryPage() {
  const [params, setParams] = useSearchParams()
  const requestedPath = params.get('path')
  const openPath = params.get('open')
  const typeFilter = params.get('type') ?? 'all'
  const favoritesOnly = params.get('fav') === '1'
  const sort = params.get('sort') ?? 'name_asc'
  const info = useInfo()
  const scale = useDensityStore((state) => state.scale)
  const setScale = useDensityStore((state) => state.setScale)
  const rowHeight = TILE_ROW_HEIGHT[scale]

  const query = useInfiniteBrowse(requestedPath, {
    type: typeFilter,
    favorite: favoritesOnly,
    sort,
  })
  const first = query.data?.pages[0]

  const files = useMemo(
    () => query.data?.pages.flatMap((page) => page.files) ?? [],
    [query.data],
  )

  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)
  const [renameTarget, setRenameTarget] = useState<FileEntry | null>(null)
  const [deleteTargets, setDeleteTargets] = useState<FileEntry[]>([])
  const [moveTargets, setMoveTargets] = useState<FileEntry[]>([])
  const [moveOpen, setMoveOpen] = useState(false)
  const [newFolderOpen, setNewFolderOpen] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [zipPending, setZipPending] = useState<'compress' | 'unpack' | null>(null)
  const [zipBusy, setZipBusy] = useState(false)
  const [selectionMode, setSelectionMode] = useState(false)
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set())
  const [bulkRenameOpen, setBulkRenameOpen] = useState(false)
  const selectionAnchor = useRef<number | null>(null)
  const toggleFavorite = useToggleFavorite()
  const moveFiles = useMoveFiles()
  const favoriteBatch = useFavoriteBatch()
  const webpOptimize = useWebpOptimize()
  const queryClient = useQueryClient()

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
    const next = new URLSearchParams(params)
    if (path) next.set('path', path)
    else next.delete('path')
    next.delete('open')
    setParams(next)
    window.scrollTo({ top: 0 })
  }

  const patchParams = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(patch)) {
      if (value === null) next.delete(key)
      else next.set(key, value)
    }
    next.delete('open')
    setParams(next)
  }

  const filtered = typeFilter !== 'all' || favoritesOnly || sort !== 'name_asc'
  const clearFilters = () => patchParams({ type: null, fav: null, sort: null })

  const selectedFiles = useMemo(
    () => files.filter((file) => selectedPaths.has(file.path)),
    [files, selectedPaths],
  )
  const allSelected = files.length > 0 && selectedFiles.length === files.length
  const allFavorite =
    selectedFiles.length > 0 && selectedFiles.every((file) => file.is_favorite)
  const selectedImages = selectedFiles.filter((file) => file.type === 'image')

  const clearSelection = () => {
    setSelectedPaths(new Set())
    setSelectionMode(false)
    selectionAnchor.current = null
  }

  const selectAll = () => {
    setSelectedPaths(new Set(files.map((file) => file.path)))
    selectionAnchor.current = null
  }

  const selectFile = (
    file: FileEntry,
    index: number,
    event: React.MouseEvent<HTMLElement>,
  ) => {
    setSelectionMode(true)
    if (event.shiftKey && selectionAnchor.current !== null) {
      const start = Math.min(selectionAnchor.current, index)
      const end = Math.max(selectionAnchor.current, index)
      setSelectedPaths((previous) => {
        const next = new Set(previous)
        for (let cursor = start; cursor <= end; cursor += 1) {
          const item = files[cursor]
          if (item) next.add(item.path)
        }
        return next
      })
      return
    }
    selectionAnchor.current = index
    setSelectedPaths((previous) => {
      const next = new Set(previous)
      if (next.has(file.path)) next.delete(file.path)
      else next.add(file.path)
      return next
    })
  }

  // Leaving a folder ends the selection; filtering drops rows that are no
  // longer visible so a bulk action never touches an unseen file.
  useEffect(() => {
    clearSelection()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [first?.path])

  useEffect(() => {
    setSelectedPaths((previous) => {
      if (!previous.size) return previous
      const available = new Set(files.map((file) => file.path))
      const next = new Set([...previous].filter((path) => available.has(path)))
      return next.size === previous.size ? previous : next
    })
  }, [files])

  useEffect(() => {
    if (!selectionMode) return
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const typing =
        target?.tagName === 'INPUT' ||
        target?.tagName === 'TEXTAREA' ||
        target?.tagName === 'SELECT' ||
        target?.isContentEditable
      if (typing) return
      if (event.key === 'Escape') {
        clearSelection()
        return
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'a') {
        event.preventDefault()
        selectAll()
        return
      }
      if ((event.key === 'Delete' || event.key === 'Backspace') && selectedFiles.length) {
        event.preventDefault()
        setDeleteTargets(selectedFiles)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectionMode, files, selectedFiles])

  const runFavoriteBatch = () => {
    if (!selectedFiles.length) return
    favoriteBatch.mutate(
      {
        paths: selectedFiles.map((file) => file.path),
        action: allFavorite ? 'remove' : 'add',
      },
      {
        onSuccess: (result) => {
          toast.success(
            allFavorite
              ? `Removed ${result.updated} from favorites`
              : `Added ${result.updated} to favorites`,
          )
        },
        onError: (error) =>
          toast.error(
            error instanceof Error ? error.message : 'Could not update favorites',
          ),
      },
    )
  }

  const runWebpBatch = () => {
    if (!selectedImages.length) return
    webpOptimize.mutate(
      {
        limit: selectedImages.length,
        paths: selectedImages.map((file) => file.path),
      },
      {
        onSuccess: (result) => {
          if (result.failed) {
            toast.error(
              `${result.converted} converted, ${result.failed} failed (${result.saved_formatted} saved)`,
            )
          } else {
            toast.success(
              `${result.converted} converted to WebP, ${result.saved_formatted} saved`,
            )
          }
          clearSelection()
        },
        onError: (error) =>
          toast.error(
            error instanceof Error ? error.message : 'WebP conversion failed',
          ),
      },
    )
  }

  const handleAction = (action: FileAction, file: FileEntry) => {
    if (action === 'favorite' || action === 'unfavorite') {
      toggleFavorite.mutate(file.path, {
        onSuccess: (result) =>
          toast.success(
            result.is_favorite ? 'Added to favorites' : 'Removed from favorites',
          ),
        onError: (error) =>
          toast.error(error instanceof Error ? error.message : 'Could not update favorite'),
      })
      return
    }
    if (action === 'rename') setRenameTarget(file)
    if (action === 'move') {
      setMoveTargets([file])
      setMoveOpen(true)
    }
    if (action === 'delete') setDeleteTargets([file])
  }

  const requestCompress = async () => {
    if (!currentPath) return
    try {
      const check = await api.zipCheck(currentPath)
      if (!check.eligible) {
        toast.error(check.reason || 'This folder cannot be archived')
        return
      }
      setZipPending('compress')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not check the folder')
    }
  }

  const runZipAction = async () => {
    if (!zipPending || !currentPath) return
    setZipBusy(true)
    try {
      if (zipPending === 'compress') {
        const result = await api.zipCompress(currentPath)
        toast.success(
          `${result.members} file${result.members === 1 ? '' : 's'} archived (${result.size_formatted})`,
        )
        setZipPending(null)
        if (parent) navigateTo(parent)
        else void query.refetch()
      } else {
        const result = await api.zipUnpack(currentPath)
        toast.success(
          `${result.files} file${result.files === 1 ? '' : 's'} restored`,
        )
        setZipPending(null)
        navigateTo(result.folder)
      }
      await invalidateLibrary(queryClient)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'ZIP operation failed')
    } finally {
      setZipBusy(false)
    }
  }

  const confirmMove = async (destination: string) => {
    try {
      const result = await moveFiles.mutateAsync({
        paths: moveTargets.map((file) => file.path),
        destination,
        conflict: 'auto_rename',
      })
      if (result.failed.length) {
        toast.error(`${result.moved.length} moved, ${result.failed.length} failed`)
      } else if (result.moved.length === 0) {
        toast.info('Nothing to move')
      } else {
        toast.success(
          `${result.moved.length} ${result.moved.length === 1 ? 'file' : 'files'} moved`,
        )
      }
      setMoveOpen(false)
      setMoveTargets([])
      clearSelection()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Move failed')
    }
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
              aria-label={selectionMode ? 'Exit selection mode' : 'Select media'}
              title={selectionMode ? 'Exit selection mode' : 'Select media'}
              variant={selectionMode ? 'secondary' : 'ghost'}
              onClick={() => {
                if (selectionMode) clearSelection()
                else setSelectionMode(true)
              }}
              disabled={!files.length}
            >
              <ListChecks />
            </IconButton>
            <IconButton
              aria-label="New folder"
              title="New folder"
              onClick={() => setNewFolderOpen(true)}
              disabled={!currentPath || first?.is_zip}
            >
              <FolderPlus />
            </IconButton>
            {first?.is_zip ? (
              <IconButton
                aria-label="Unpack this archive"
                title="Unpack this archive"
                onClick={() => setZipPending('unpack')}
              >
                <ArchiveRestore />
              </IconButton>
            ) : (
              <IconButton
                aria-label="Compress this folder to ZIP"
                title="Compress this folder to ZIP"
                onClick={() => void requestCompress()}
                disabled={!currentPath}
              >
                <Archive />
              </IconButton>
            )}
            <Button
              size="sm"
              className="hidden sm:inline-flex"
              onClick={() => setUploadOpen(true)}
              disabled={!currentPath || first?.is_zip}
            >
              <UploadCloud /> Upload
            </Button>
            <IconButton
              aria-label="Upload files"
              title="Upload files"
              className="sm:hidden"
              onClick={() => setUploadOpen(true)}
              disabled={!currentPath || first?.is_zip}
            >
              <UploadCloud />
            </IconButton>
            <IconButton
              aria-label="Refresh folder"
              title="Refresh"
              onClick={() => void query.refetch()}
            >
              <RefreshCw className={cn(query.isFetching && 'animate-spin')} />
            </IconButton>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 px-3 pb-2.5 sm:px-5">
          <Segmented
            aria-label="Filter media"
            size="sm"
            value={typeFilter}
            onChange={(value) =>
              patchParams({ type: value === 'all' ? null : value })
            }
            options={[
              { value: 'all', label: 'All' },
              { value: 'video', label: 'Videos' },
              { value: 'image', label: 'Images' },
            ]}
          />
          <button
            type="button"
            aria-pressed={favoritesOnly}
            onClick={() => patchParams({ fav: favoritesOnly ? null : '1' })}
            className={cn(
              'inline-flex h-7 items-center gap-1.5 rounded-sm border px-2.5 text-xs font-medium transition-colors',
              favoritesOnly
                ? 'border-transparent bg-amber-500/15 text-amber-600 dark:text-amber-300'
                : 'border-border text-fg-muted hover:bg-surface-2 hover:text-fg',
            )}
          >
            <Star
              aria-hidden="true"
              className={cn('size-3.5', favoritesOnly && 'fill-current')}
            />
            Favorites
          </button>

          <div className="ml-auto flex items-center gap-2">
            {filtered ? (
              <button
                type="button"
                onClick={clearFilters}
                className="text-xs text-fg-subtle hover:text-fg"
              >
                Clear
              </button>
            ) : null}
            <Select
              aria-label="Sort media"
              value={sort}
              onChange={(event) => patchParams({ sort: event.target.value })}
              className="h-8 w-36 text-xs"
            >
              <option value="name_asc">Name A–Z</option>
              <option value="name_desc">Name Z–A</option>
              <option value="date_desc">Newest first</option>
              <option value="date_asc">Oldest first</option>
              <option value="size_desc">Largest first</option>
              <option value="size_asc">Smallest first</option>
            </Select>
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
                    onAction={handleAction}
                    selectedIds={selectedPaths}
                    selectionMode={selectionMode}
                    onSelect={selectFile}
                  />
                </>
              ) : null}

              {!query.isLoading && files.length === 0 ? (
                <EmptyState
                  icon={filtered ? <Star /> : <ImageOff />}
                  title={
                    filtered
                      ? 'No media matches these filters'
                      : (first?.folders.length ?? 0) > 0
                        ? 'No media in this folder'
                        : 'Nothing here yet'
                  }
                  description={
                    filtered
                      ? 'Clear the filters to see everything in this folder.'
                      : (first?.folders.length ?? 0) > 0
                        ? 'Open one of the folders above.'
                        : 'This folder has no media and no subfolders in the index.'
                  }
                  action={
                    filtered ? (
                      <Button variant="secondary" onClick={clearFilters}>
                        Clear filters
                      </Button>
                    ) : undefined
                  }
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

      {selectedPaths.size > 0 ? (
        <SelectionBar
          count={selectedFiles.length}
          total={files.length}
          allSelected={allSelected}
          allFavorite={allFavorite}
          imageCount={selectedImages.length}
          busy={favoriteBatch.isPending || webpOptimize.isPending}
          onSelectAll={selectAll}
          onClear={clearSelection}
          onFavorite={runFavoriteBatch}
          onMove={() => {
            setMoveTargets(selectedFiles)
            setMoveOpen(true)
          }}
          onRename={() => setBulkRenameOpen(true)}
          onWebp={runWebpBatch}
          onDelete={() => setDeleteTargets(selectedFiles)}
        />
      ) : null}

      {bulkRenameOpen ? (
        <BulkRenameDialog
          files={selectedFiles}
          onClose={() => {
            setBulkRenameOpen(false)
            clearSelection()
          }}
        />
      ) : null}

      {lightboxIndex !== null ? (
        <MediaLightbox
          files={files}
          index={lightboxIndex}
          onIndexChange={setLightboxIndex}
          onClose={() => setLightboxIndex(null)}
          onAction={(action, file) => {
            if (action !== 'favorite' && action !== 'unfavorite') {
              setLightboxIndex(null)
            }
            handleAction(action, file)
          }}
        />
      ) : null}

      <RenameDialog file={renameTarget} onClose={() => setRenameTarget(null)} />
      <DeleteDialog files={deleteTargets} onClose={() => {
          setDeleteTargets([])
          clearSelection()
        }} />
      <NewFolderDialog
        parent={currentPath || null}
        open={newFolderOpen}
        onClose={() => setNewFolderOpen(false)}
      />
      <FolderPicker
        open={moveOpen}
        onOpenChange={(open) => {
          setMoveOpen(open)
          if (!open) setMoveTargets([])
        }}
        title={
          moveTargets.length === 1
            ? `Move “${moveTargets[0]?.name ?? ''}”`
            : `Move ${moveTargets.length} files`
        }
        initialPath={info.data?.library_root ?? null}
        busy={moveFiles.isPending}
        onPick={(destination) => void confirmMove(destination)}
      />
      <Dialog
        open={zipPending !== null}
        onOpenChange={(open) => (open ? undefined : setZipPending(null))}
      >
        <DialogContent aria-describedby={undefined} className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {zipPending === 'compress'
                ? 'Compress this folder?'
                : 'Unpack this archive?'}
            </DialogTitle>
          </DialogHeader>
          <DialogBody>
            <p className="text-sm leading-6 text-fg-muted">
              {zipPending === 'compress'
                ? 'The folder is replaced by a ZIP archive and its files become archive members. The original folder is removed.'
                : 'The archive is extracted into a sibling folder and the ZIP is deleted. Existing names are never overwritten.'}
            </p>
            <p className="mt-2 truncate font-mono text-[11px] text-fg-subtle">
              {currentPath}
            </p>
          </DialogBody>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setZipPending(null)}
              disabled={zipBusy}
            >
              Cancel
            </Button>
            <Button onClick={() => void runZipAction()} disabled={zipBusy}>
              {zipBusy ? (
                <Loader2 className="size-4 animate-spin" />
              ) : zipPending === 'compress' ? (
                <Archive />
              ) : (
                <ArchiveRestore />
              )}
              {zipPending === 'compress' ? 'Compress' : 'Unpack'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <UploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        folder={currentPath}
        onCompleted={() => {
          void invalidateLibrary(queryClient)
          setUploadOpen(false)
        }}
      />
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
