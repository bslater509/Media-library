import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import {
  Archive,
  ArchiveRestore,
  ArrowRight,
  Loader2,
  FolderArchive,
  Pause,
  Play,
  X,
} from 'lucide-react'
import { toast } from 'sonner'
import type { ZipArrayItem, ZipCandidate } from '../lib/api'
import {
  useZipBatch,
  useZipBatchCancel,
  useZipBatchPause,
  useZipBatchResume,
  useZipBatchStart,
  useZipCompress,
  useZipOverview,
  useZipUnpack,
} from '../lib/queries'
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Progress,
  Spinner,
} from '../components/ui'

type Pending =
  | { kind: 'compress'; item: ZipCandidate }
  | { kind: 'unpack'; item: ZipArrayItem }

export function ZipPage() {
  const overview = useZipOverview()
  const compress = useZipCompress()
  const unpack = useZipUnpack()
  const [pending, setPending] = useState<Pending | null>(null)
  const [confirmBatch, setConfirmBatch] = useState<'compress' | 'unpack' | null>(null)
  const batch = useZipBatch()
  const startBatch = useZipBatchStart()
  const pauseBatch = useZipBatchPause()
  const resumeBatch = useZipBatchResume()
  const cancelBatch = useZipBatchCancel()
  const batchStatus = batch.data
  const batchActive = ['running', 'paused'].includes(batchStatus?.status ?? '')
  const lastBatchStatus = useRef('')

  const busy = compress.isPending || unpack.isPending
  const batchBusy =
    startBatch.isPending || pauseBatch.isPending || resumeBatch.isPending || cancelBatch.isPending

  useEffect(() => {
    const status = batchStatus?.status ?? ''
    if (lastBatchStatus.current !== status && status === 'completed') {
      void overview.refetch()
    }
    lastBatchStatus.current = status
  }, [batchStatus?.status, overview])

  const beginBatch = () => {
    if (!confirmBatch) return
    startBatch.mutate(confirmBatch, {
      onSuccess: () => {
        toast.success(
          confirmBatch === 'compress'
            ? 'Batch compression started'
            : 'Batch unpack started',
        )
        setConfirmBatch(null)
      },
      onError: (error) =>
        toast.error(error instanceof Error ? error.message : 'Could not start the batch'),
    })
  }
  const folders = overview.data?.folders ?? []
  const archives = overview.data?.archives ?? []

  const submit = () => {
    if (!pending) return
    if (pending.kind === 'compress') {
      compress.mutate(pending.item.path, {
        onSuccess: (result) => {
          toast.success(
            `${result.members} file${result.members === 1 ? '' : 's'} archived into ${result.size_formatted}`,
          )
          setPending(null)
        },
        onError: (error) =>
          toast.error(error instanceof Error ? error.message : 'Compression failed'),
      })
    } else {
      unpack.mutate(pending.item.path, {
        onSuccess: (result) => {
          toast.success(
            `${result.files} file${result.files === 1 ? '' : 's'} restored`,
          )
          setPending(null)
        },
        onError: (error) =>
          toast.error(error instanceof Error ? error.message : 'Unpack failed'),
      })
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 px-3 py-4 sm:px-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="flex items-center gap-2 text-base font-semibold tracking-tight">
            <Archive aria-hidden="true" className="size-4 text-fg-subtle" />
            ZIP archives
          </h1>
          <p className="text-xs text-fg-muted">
            Compress leaf image folders or unpack an archive back into a folder.
            Archives also open as virtual folders in the Library.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline">
            cache {overview.data?.cache.size_formatted ?? '—'}
          </Badge>
          <Link
            to="/library"
            className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
          >
            Browse archives <ArrowRight className="size-3" />
          </Link>
        </div>
      </div>

      {batchStatus && batchStatus.status !== 'idle' ? (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <CardTitle className="flex items-center gap-2">
                {batchStatus.status === 'running' ? (
                  <Loader2 aria-hidden="true" className="size-4 animate-spin text-accent" />
                ) : (
                  <Archive aria-hidden="true" className="size-4 text-fg-subtle" />
                )}
                Batch {batchStatus.mode === 'compress' ? 'compression' : 'unpacking'}
                <Badge
                  variant={
                    batchStatus.status === 'completed'
                      ? 'success'
                      : batchStatus.status === 'cancelled'
                        ? 'warning'
                        : 'accent'
                  }
                >
                  {batchStatus.status}
                </Badge>
              </CardTitle>
              <span className="font-mono text-[11px] text-fg-subtle tabular-nums">
                {batchStatus.processed} / {batchStatus.total} ·{' '}
                {batchStatus.succeeded} ok
                {batchStatus.failed ? ` · ${batchStatus.failed} failed` : ''}
              </span>
            </div>
          </CardHeader>
          <CardBody className="flex flex-col gap-2">
            <Progress value={batchStatus.percent} />
            {batchStatus.current_path ? (
              <p className="truncate font-mono text-[10px] text-fg-subtle">
                {batchStatus.current_path}
              </p>
            ) : null}
            {batchStatus.errors.length ? (
              <ul className="max-h-24 overflow-y-auto text-[10px] text-danger">
                {batchStatus.errors.slice(0, 4).map((error) => (
                  <li key={error.path} className="truncate">
                    {error.path}: {error.error}
                  </li>
                ))}
              </ul>
            ) : null}
            {batchActive ? (
              <div className="flex items-center gap-2">
                {batchStatus.status === 'paused' ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={batchBusy}
                    onClick={() => resumeBatch.mutate(undefined, { onError: (e) => toast.error(e instanceof Error ? e.message : 'Resume failed') })}
                  >
                    <Play /> Resume
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={batchBusy}
                    onClick={() => pauseBatch.mutate(undefined, { onError: (e) => toast.error(e instanceof Error ? e.message : 'Pause failed') })}
                  >
                    <Pause /> Pause
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="danger"
                  disabled={batchBusy}
                  onClick={() =>
                    cancelBatch.mutate(undefined, {
                      onSuccess: () => toast.info('Batch cancelled'),
                      onError: (e) =>
                        toast.error(e instanceof Error ? e.message : 'Cancel failed'),
                    })
                  }
                >
                  <X /> Cancel
                </Button>
              </div>
            ) : null}
          </CardBody>
        </Card>
      ) : null}

      {overview.isLoading ? (
        <div className="flex items-center gap-2 py-8 text-sm text-fg-subtle">
          <Spinner /> Reading the index…
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <CardTitle className="flex items-center gap-2">
                    <FolderArchive aria-hidden="true" className="size-4 text-fg-subtle" />
                    Folders ready to archive
                  </CardTitle>
                  <Badge variant="outline">{folders.length}</Badge>
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={!folders.length || batchActive}
                  onClick={() => setConfirmBatch('compress')}
                >
                  <Archive /> Compress all
                </Button>
              </div>
            </CardHeader>
            <CardBody className="flex max-h-[28rem] flex-col gap-1.5 overflow-y-auto">
              {folders.length ? (
                folders.map((folder) => (
                  <div
                    key={folder.path}
                    className="flex items-center gap-2 rounded-sm border border-border px-2 py-1.5"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-xs text-fg">{folder.name}</p>
                      <p className="truncate font-mono text-[10px] text-fg-subtle">
                        {folder.path}
                      </p>
                    </div>
                    <span className="shrink-0 text-[10px] text-fg-subtle">
                      {folder.file_count} files · {folder.size_formatted}
                    </span>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => setPending({ kind: 'compress', item: folder })}
                    >
                      <Archive /> Compress
                    </Button>
                  </div>
                ))
              ) : (
                <EmptyState
                  icon={<FolderArchive />}
                  title="No eligible folders"
                  description="A folder needs at least two images, no videos and no nested archives."
                />
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <CardTitle className="flex items-center gap-2">
                    <ArchiveRestore aria-hidden="true" className="size-4 text-fg-subtle" />
                    Archives
                  </CardTitle>
                  <Badge variant="outline">{archives.length}</Badge>
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={!archives.length || batchActive}
                  onClick={() => setConfirmBatch('unpack')}
                >
                  <ArchiveRestore /> Unpack all
                </Button>
              </div>
            </CardHeader>
            <CardBody className="flex max-h-[28rem] flex-col gap-1.5 overflow-y-auto">
              {archives.length ? (
                archives.map((archive) => (
                  <div
                    key={archive.path}
                    className="flex items-center gap-2 rounded-sm border border-border px-2 py-1.5"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-xs text-fg">{archive.name}</p>
                      <p className="truncate font-mono text-[10px] text-fg-subtle">
                        {archive.path}
                      </p>
                    </div>
                    <span className="shrink-0 text-[10px] text-fg-subtle">
                      {archive.file_count} files · {archive.size_formatted}
                    </span>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => setPending({ kind: 'unpack', item: archive })}
                    >
                      <ArchiveRestore /> Unpack
                    </Button>
                  </div>
                ))
              ) : (
                <EmptyState
                  icon={<ArchiveRestore />}
                  title="No archives indexed"
                  description="Compressed folders appear here and in the Library."
                />
              )}
            </CardBody>
          </Card>
        </div>
      )}

      <Dialog
        open={pending !== null}
        onOpenChange={(open) => (open ? undefined : setPending(null))}
      >
        <DialogContent aria-describedby={undefined} className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {pending?.kind === 'compress'
                ? `Compress “${pending.item.name}”?`
                : `Unpack “${pending?.item.name}”?`}
            </DialogTitle>
          </DialogHeader>
          <DialogBody>
            {pending?.kind === 'compress' ? (
              <p className="text-sm leading-6 text-fg-muted">
                The folder is replaced by{' '}
                <span className="font-mono text-xs">{pending.item.path}.zip</span> and
                its files are indexed as archive members. The original folder is
                removed.
              </p>
            ) : (
              <p className="text-sm leading-6 text-fg-muted">
                The archive is extracted into a sibling folder and then deleted.
                Existing names are never overwritten.
              </p>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPending(null)} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={busy}>
              {busy ? (
                <Loader2 className="size-4 animate-spin" />
              ) : pending?.kind === 'compress' ? (
                <Archive />
              ) : (
                <ArchiveRestore />
              )}
              {pending?.kind === 'compress' ? 'Compress folder' : 'Unpack archive'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={confirmBatch !== null}
        onOpenChange={(open) => (open ? undefined : setConfirmBatch(null))}
      >
        <DialogContent aria-describedby={undefined} className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {confirmBatch === 'compress'
                ? `Compress all ${folders.length} folders?`
                : `Unpack all ${archives.length} archives?`}
            </DialogTitle>
          </DialogHeader>
          <DialogBody>
            <p className="text-sm leading-6 text-fg-muted">
              {confirmBatch === 'compress'
                ? 'Every eligible leaf image folder is replaced by a ZIP archive, one at a time. Folders containing videos or nested archives are skipped by the server.'
                : 'Every indexed archive is extracted into a sibling folder and then deleted, one at a time. Existing names are never overwritten.'}
            </p>
            <p className="mt-2 text-[11px] leading-5 text-warning">
              This runs in the background and can be paused or cancelled between
              items. It cannot undo folders already processed.
            </p>
          </DialogBody>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setConfirmBatch(null)}
              disabled={startBatch.isPending}
            >
              Cancel
            </Button>
            <Button onClick={beginBatch} disabled={startBatch.isPending}>
              {startBatch.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Archive />
              )}
              Start batch
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
