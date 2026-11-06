import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Check,
  Clapperboard,
  FolderInput,
  FolderPlus,
  Loader2,
  Pencil,
  RefreshCw,
  Search,
  SkipForward,
  Trash2,
  Undo2,
  Volume2,
  VolumeX,
} from 'lucide-react'
import { toast } from 'sonner'
import { api, type SorterDestination, type SorterItem } from '../lib/api'
import { cn } from '../lib/cn'
import {
  useSorterConfig,
  useSorterCreateFolder,
  useSorterDelete,
  useSorterDestinations,
  useSorterMove,
  useSorterQueue,
  useSorterRename,
  useSorterUndo,
} from '../lib/queries'
import {
  Badge,
  Button,
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  IconButton,
  Input,
  Progress,
  Spinner,
} from '../components/ui'

const MUTED_KEY = 'v2_sorter_muted'

export function SorterPage() {
  const config = useSorterConfig()
  const queue = useSorterQueue()
  const move = useSorterMove()
  const undo = useSorterUndo()
  const rename = useSorterRename()
  const remove = useSorterDelete()
  const createFolder = useSorterCreateFolder()

  const items = queue.data?.items ?? []
  const [skipped, setSkipped] = useState<Set<string>>(new Set())
  const [search, setSearch] = useState('')
  const searchRef = useRef<HTMLInputElement | null>(null)
  const destinations = useSorterDestinations(search)
  const [renameItem, setRenameItem] = useState<SorterItem | null>(null)
  const [deleteItem, setDeleteItem] = useState<SorterItem | null>(null)
  const [newFolderOpen, setNewFolderOpen] = useState(false)
  const [sessionMoved, setSessionMoved] = useState(0)
  const [muted, setMuted] = useState(() => {
    try {
      return window.localStorage.getItem(MUTED_KEY) !== '0'
    } catch {
      return true
    }
  })

  const current = useMemo(
    () => items.find((item) => !skipped.has(item.path)) ?? items[0],
    [items, skipped],
  )
  const recents = config.data?.recent_destinations ?? []
  const root = config.data?.library_root ?? queue.data?.source_dir ?? ''

  // Drop skip markers for files that no longer exist.
  useEffect(() => {
    setSkipped((previous) => {
      if (previous.size === 0) return previous
      const paths = new Set(items.map((item) => item.path))
      const next = new Set([...previous].filter((path) => paths.has(path)))
      return next.size === previous.size ? previous : next
    })
  }, [items])

  useEffect(() => {
    try {
      window.localStorage.setItem(MUTED_KEY, muted ? '1' : '0')
    } catch {
      /* ignore */
    }
  }, [muted])

  const doMove = useCallback(
    (destination: string) => {
      if (!current || move.isPending) return
      const name = destination.split('/').filter(Boolean).pop() ?? destination
      move.mutate(
        { path: current.path, destination },
        {
          onSuccess: () => {
            setSearch('')
            setSkipped(new Set())
            setSessionMoved((value) => value + 1)
            toast.success(`Moved to ${name}`)
          },
          onError: (error) =>
            toast.error(error instanceof Error ? error.message : 'Move failed'),
        },
      )
    },
    [current, move],
  )

  const doUndo = useCallback(() => {
    if (undo.isPending || (queue.data?.undo_count ?? 0) === 0) return
    undo.mutate(undefined, {
      onSuccess: () => {
        setSessionMoved((value) => Math.max(0, value - 1))
        toast.success('Undone')
      },
      onError: (error) =>
        toast.error(error instanceof Error ? error.message : 'Undo failed'),
    })
  }, [queue.data?.undo_count, undo])

  const skip = useCallback(() => {
    if (!current) return
    setSkipped((previous) => {
      const next = new Set(previous)
      next.add(current.path)
      if (next.size >= items.length) next.clear()
      return next
    })
  }, [current, items.length])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const typing =
        target?.tagName === 'INPUT' ||
        target?.tagName === 'TEXTAREA' ||
        target?.isContentEditable
      if (typing) {
        if (event.key === 'Escape') (target as HTMLElement).blur()
        return
      }
      if (event.metaKey || event.ctrlKey) {
        if (event.key.toLowerCase() === 'z') {
          event.preventDefault()
          doUndo()
        }
        return
      }
      if (event.key === '/') {
        event.preventDefault()
        searchRef.current?.focus()
        return
      }
      if (event.key === 'Escape') {
        setSearch('')
        return
      }
      if (event.key === 'u' || event.key === 'U') {
        doUndo()
        return
      }
      if ((event.key === 's' || event.key === 'ArrowRight') && !event.shiftKey) {
        event.preventDefault()
        skip()
        return
      }
      if ((event.key === 'r' || event.key === 'R') && current) {
        setRenameItem(current)
        return
      }
      if ((event.key === 'd' || event.key === 'D') && current) {
        setDeleteItem(current)
        return
      }
      if (event.key === 'Enter') {
        const top = destinations.data?.[0]
        if (top) doMove(top.path)
        return
      }
      if (/^[1-6]$/.test(event.key)) {
        const destination = recents[Number(event.key) - 1]
        if (destination) doMove(destination.path)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [current, destinations.data, doMove, doUndo, recents, skip])

  if (config.isLoading || queue.isLoading) {
    return (
      <div className="flex items-center gap-2 px-4 py-16 text-sm text-fg-subtle">
        <Spinner /> Loading the sorter…
      </div>
    )
  }

  if (!config.data?.source_available) {
    return (
      <div className="px-3 py-10 sm:px-5">
        <EmptyState
          icon={<Clapperboard />}
          title="Incoming folder is not available"
          description={
            config.data?.source_dir
              ? `${config.data.source_dir} is missing or unreachable. Set INCOMING_DIR (or V2_SOURCE_DIR) and restart.`
              : 'Set INCOMING_DIR (or V2_SOURCE_DIR) to the folder the sorter should review.'
          }
        />
      </div>
    )
  }

  if (!current) {
    return (
      <div className="px-3 py-10 sm:px-5">
        <EmptyState
          icon={<Check />}
          title="Queue is empty"
          description={`Nothing waiting in ${config.data.source_dir}. New files appear here when you rescan.`}
          action={
            <Button
              variant="secondary"
              onClick={() =>
                void queue.refetch().then(() => toast.success('Queue refreshed'))
              }
            >
              <RefreshCw /> Rescan
            </Button>
          }
        />
      </div>
    )
  }

  const position = items.findIndex((item) => item.path === current.path) + 1
  const videoSrc = current.playable ? current.stream_url : current.transcode_url
  const poster = api.sorterThumbnail(current.path)

  return (
    <div className="flex min-h-[calc(100dvh-3.5rem)] flex-col md:h-[calc(100dvh-3.5rem)] md:min-h-0 md:flex-row">
      <section className="relative flex min-h-[45dvh] flex-1 flex-col bg-black">
        <div className="flex items-center gap-2 px-3 py-2">
          <span className="flex min-w-0 flex-1 items-center gap-2">
            <span className="truncate text-sm font-medium text-white">
              {current.name}
            </span>
            {current.needs_transcode ? (
              <Badge variant="warning" className="shrink-0">
                converting
              </Badge>
            ) : null}
          </span>
          <span className="shrink-0 font-mono text-[11px] text-white/55 tabular-nums">
            {position} / {items.length}
          </span>
          <IconButton
            aria-label={muted ? 'Unmute' : 'Mute'}
            title={muted ? 'Unmute (M)' : 'Mute (M)'}
            size={32}
            className="text-white/75 hover:bg-white/10 hover:text-white"
            onClick={() => setMuted((value) => !value)}
          >
            {muted ? <VolumeX /> : <Volume2 />}
          </IconButton>
        </div>

        <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden">
          <video
            key={current.path}
            src={videoSrc ?? undefined}
            poster={poster}
            controls
            autoPlay
            muted={muted}
            playsInline
            className="max-h-full max-w-full"
          />
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-[11px] text-white/60">
          <span className="font-mono uppercase">{current.ext.replace('.', '')}</span>
          <span>{current.size_formatted}</span>
          <span className="truncate font-mono text-white/40">{current.path}</span>
        </div>

        <div className="flex items-center gap-1.5 border-t border-white/10 bg-black/60 px-3 py-2">
          <Button
            size="sm"
            variant="secondary"
            className="border-white/15 bg-white/10 text-white hover:bg-white/20"
            onClick={skip}
          >
            <SkipForward /> Skip
          </Button>
          <IconButton
            aria-label="Undo last move"
            title="Undo (U)"
            size={32}
            className="text-white/75 hover:bg-white/10 hover:text-white"
            onClick={doUndo}
            disabled={undo.isPending || (queue.data?.undo_count ?? 0) === 0}
          >
            <Undo2 />
          </IconButton>
          <span className="text-[11px] text-white/45 tabular-nums">
            {queue.data?.undo_count ?? 0} undo
          </span>
          <div className="ml-auto flex items-center gap-1.5">
            <IconButton
              aria-label="Rename current file"
              title="Rename (R)"
              size={32}
              className="text-white/75 hover:bg-white/10 hover:text-white"
              onClick={() => setRenameItem(current)}
            >
              <Pencil />
            </IconButton>
            <IconButton
              aria-label="Delete current file"
              title="Delete (D)"
              size={32}
              variant="danger"
              className="text-danger/90 hover:bg-danger/20"
              onClick={() => setDeleteItem(current)}
            >
              <Trash2 />
            </IconButton>
          </div>
        </div>

        <Progress
          value={items.length ? ((items.length - position + 1) / items.length) * 100 : 0}
          className="h-1 rounded-none bg-white/10"
        />
      </section>

      <aside className="flex w-full shrink-0 flex-col gap-3 border-t border-border bg-surface p-3 md:w-80 md:border-t-0 md:border-l">
        <div className="flex items-center justify-between gap-2">
          <div>
            <p className="text-sm font-semibold">Destinations</p>
            <p className="text-[11px] text-fg-subtle">
              {sessionMoved} moved this session · {items.length} left
            </p>
          </div>
          <IconButton
            aria-label="Rescan incoming folder"
            title="Rescan"
            onClick={() => void queue.refetch()}
          >
            <RefreshCw className={cn(queue.isFetching && 'animate-spin')} />
          </IconButton>
        </div>

        <div className="relative">
          <Search
            aria-hidden="true"
            className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-fg-subtle"
          />
          <Input
            ref={searchRef}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search folders…  /"
            aria-label="Search destination folders"
            className="h-9 pl-8"
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                const top = destinations.data?.[0]
                if (top) doMove(top.path)
              }
            }}
          />
        </div>

        {search.trim() ? (
          <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
            {destinations.isFetching && !destinations.data ? (
              <p className="px-1 py-2 text-xs text-fg-subtle">Searching…</p>
            ) : null}
            {destinations.data?.length ? (
              destinations.data.map((destination, index) => (
                <button
                  key={destination.path}
                  type="button"
                  onClick={() => doMove(destination.path)}
                  className={cn(
                    'flex items-center gap-2 rounded-sm px-2 py-2 text-left text-sm',
                    index === 0
                      ? 'bg-accent-soft text-accent'
                      : 'text-fg-muted hover:bg-surface-2 hover:text-fg',
                  )}
                >
                  <FolderInput aria-hidden="true" className="size-4 shrink-0" />
                  <span className="min-w-0 flex-1 truncate">{destination.name}</span>
                  {index === 0 ? (
                    <kbd className="shrink-0 rounded-sm border border-accent/30 px-1 font-mono text-[10px]">
                      ↵
                    </kbd>
                  ) : (
                    <span className="shrink-0 text-[10px] text-fg-subtle tabular-nums">
                      {destination.file_count ?? 0}
                    </span>
                  )}
                </button>
              ))
            ) : (
              <p className="px-1 py-2 text-xs text-fg-subtle">No matching folder.</p>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {recents.length ? (
              <div className="grid grid-cols-1 gap-1.5">
                {recents.map((destination, index) => (
                  <button
                    key={destination.path}
                    type="button"
                    onClick={() => doMove(destination.path)}
                    className="flex items-center gap-2 rounded-sm border border-border px-2 py-2 text-left text-sm text-fg-muted hover:border-border-strong hover:bg-surface-2 hover:text-fg"
                  >
                    <kbd className="flex size-5 shrink-0 items-center justify-center rounded-sm bg-surface-3 font-mono text-[10px] text-fg-muted">
                      {index + 1}
                    </kbd>
                    <span className="min-w-0 flex-1 truncate">{destination.name}</span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="rounded-sm border border-dashed border-border px-3 py-3 text-xs leading-5 text-fg-subtle">
                Your six most recent destinations appear here, numbered 1–6. Search
                for a folder above, or create one below.
              </p>
            )}
          </div>
        )}

        <div className="mt-auto flex flex-col gap-2">
          <Button variant="secondary" onClick={() => setNewFolderOpen(true)}>
            <FolderPlus /> Create &amp; move
          </Button>
          <p className="text-center text-[10px] leading-4 text-fg-subtle">
            1–6 move · / search · ↵ first result · U undo · R rename · D delete
          </p>
        </div>
      </aside>

      <SorterRenameDialog
        item={renameItem}
        onClose={() => setRenameItem(null)}
        onRename={async (item, name) => {
          await rename.mutateAsync({ path: item.path, name })
          toast.success('Renamed')
          setRenameItem(null)
        }}
      />

      <Dialog
        open={deleteItem !== null}
        onOpenChange={(open) => (open ? undefined : setDeleteItem(null))}
      >
        <DialogContent aria-describedby={undefined} className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete this file?</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <p className="truncate font-mono text-xs text-fg-muted">
              {deleteItem?.name}
            </p>
            <p className="mt-2 text-sm text-fg-muted">
              It is removed from the incoming folder permanently.
            </p>
          </DialogBody>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleteItem(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              disabled={remove.isPending}
              onClick={() => {
                if (!deleteItem) return
                remove.mutate(
                  { path: deleteItem.path },
                  {
                    onSuccess: () => {
                      toast.success('Deleted')
                      setDeleteItem(null)
                    },
                    onError: (error) =>
                      toast.error(
                        error instanceof Error ? error.message : 'Delete failed',
                      ),
                  },
                )
              }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={newFolderOpen}
        onOpenChange={(open) => (open ? undefined : setNewFolderOpen(false))}
      >
        <DialogContent aria-describedby={undefined} className="max-w-md">
          <DialogHeader>
            <DialogTitle>Create a folder &amp; move here</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <NewFolderForm
              busy={createFolder.isPending || move.isPending}
              onSubmit={async (name) => {
                try {
                  const created = await createFolder.mutateAsync({
                    parent: root,
                    name,
                  })
                  if (current) {
                    await move.mutateAsync({
                      path: current.path,
                      destination: created.path,
                    })
                    setSessionMoved((value) => value + 1)
                  }
                  toast.success(`Created ${name} and moved the file`)
                  setNewFolderOpen(false)
                } catch (error) {
                  toast.error(
                    error instanceof Error ? error.message : 'Could not create folder',
                  )
                }
              }}
            />
          </DialogBody>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function SorterRenameDialog({
  item,
  onClose,
  onRename,
}: {
  item: SorterItem | null
  onClose: () => void
  onRename: (item: SorterItem, name: string) => Promise<void>
}) {
  const [name, setName] = useState('')

  useEffect(() => {
    setName(item?.name ?? '')
  }, [item])

  const submit = async () => {
    if (!item) return
    const clean = name.trim()
    if (!clean || clean === item.name) {
      onClose()
      return
    }
    try {
      await onRename(item, clean)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Rename failed')
    }
  }

  return (
    <Dialog open={item !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent aria-describedby={undefined} className="max-w-md">
        <DialogHeader>
          <DialogTitle>Rename before sorting</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            aria-label="New filename"
            autoFocus
            onKeyDown={(event) => {
              if (event.key === 'Enter') void submit()
            }}
          />
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()}>Rename</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function NewFolderForm({
  busy,
  onSubmit,
}: {
  busy: boolean
  onSubmit: (name: string) => Promise<void>
}) {
  const [name, setName] = useState('')
  return (
    <div className="flex flex-col gap-3">
      <Input
        value={name}
        onChange={(event) => setName(event.target.value)}
        placeholder="Folder name"
        aria-label="Folder name"
        autoFocus
        onKeyDown={(event) => {
          if (event.key === 'Enter' && name.trim()) void onSubmit(name.trim())
        }}
      />
      <p className="text-xs leading-5 text-fg-subtle">
        The folder is created under the library root and the current clip moves into
        it.
      </p>
      <div className="flex justify-end gap-2">
        <Button
          disabled={busy || !name.trim()}
          onClick={() => void onSubmit(name.trim())}
        >
          {busy ? <Loader2 className="size-4 animate-spin" /> : null}
          Create &amp; move
        </Button>
      </div>
    </div>
  )
}

/** Shared with the destination list for tests. */
export function destinationLabel(destination: SorterDestination): string {
  return destination.name
}
