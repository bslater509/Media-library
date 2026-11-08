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
import { type ZipArrayItem } from '../lib/api'
import {
  useZipBatch,
  useZipBatchCancel,
  useZipBatchPause,
  useZipBatchResume,
  useZipBatchStart,
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

export function ZipPage() {
  const overview = useZipOverview()
  const unpack = useZipUnpack()
  const [pending, setPending] = useState<ZipArrayItem | null>(null)
  const [confirmBatch, setConfirmBatch] = useState(false)
  const batch = useZipBatch()
  const startBatch = useZipBatchStart()
  const pauseBatch = useZipBatchPause()
  const resumeBatch = useZipBatchResume()
  const cancelBatch = useZipBatchCancel()
  const batchStatus = batch.data
  const batchActive = ['running', 'paused'].includes(batchStatus?.status ?? '')
  const lastBatchStatus = useRef('')

  const busy = unpack.isPending
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
    startBatch.mutate('unpack', {
      onSuccess: () => {
        toast.success('Batch unpack started')
        setConfirmBatch(false)
      },
      onError: (error) =>
        toast.error(error instanceof Error ? error.message : 'Could not start the batch'),
    })
  }
  const archives = overview.data?.archives ?? []

  const submit = () => {
    if (!pending) return
    unpack.mutate(pending.path, {
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

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 px-3 py-4 sm:px-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="flex items-center gap-2 text-base font-semibold tracking-tight">
            <Archive aria-hidden="true" className="size-4 text-fg-subtle" />
            ZIP archives
          </h1>
          <p className="text-xs text-fg-muted">
            Unpack an archive back into a folder. Archives also open as virtual
            folders in the Library.
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
                Batch unpacking
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
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <CardTitle className="flex items-center gap-2">
                  <FolderArchive aria-hidden="true" className="size-4 text-fg-subtle" />
                  Archives
                </CardTitle>
                <Badge variant="outline">{archives.length}</Badge>
              </div>
              <Button
                size="sm"
                variant="secondary"
                disabled={!archives.length || batchActive}
                onClick={() => setConfirmBatch(true)}
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
                    onClick={() => setPending(archive)}
                  >
                    <ArchiveRestore /> Unpack
                  </Button>
                </div>
              ))
            ) : (
              <EmptyState
                icon={<ArchiveRestore />}
                title="No archives indexed"
                description="ZIP files in the library appear here and open as virtual folders."
              />
            )}
          </CardBody>
        </Card>
      )}

      <Dialog
        open={pending !== null}
        onOpenChange={(open) => (open ? undefined : setPending(null))}
      >
        <DialogContent aria-describedby={undefined} className="max-w-md">
          <DialogHeader>
            <DialogTitle>Unpack “{pending?.name}”?</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <p className="text-sm leading-6 text-fg-muted">
              The archive is extracted into a sibling folder and then deleted.
              Existing names are never overwritten.
            </p>
            <p className="mt-2 truncate font-mono text-[11px] text-fg-subtle">
              {pending?.path}
            </p>
          </DialogBody>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPending(null)} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={busy}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : <ArchiveRestore />}
              Unpack archive
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={confirmBatch}
        onOpenChange={(open) => (open ? undefined : setConfirmBatch(false))}
      >
        <DialogContent aria-describedby={undefined} className="max-w-md">
          <DialogHeader>
            <DialogTitle>Unpack all {archives.length} archives?</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <p className="text-sm leading-6 text-fg-muted">
              Every indexed archive is extracted into a sibling folder and then
              deleted, one at a time. Existing names are never overwritten.
            </p>
            <p className="mt-2 text-[11px] leading-5 text-warning">
              This runs in the background and can be paused or cancelled between
              items. It cannot undo folders already processed.
            </p>
          </DialogBody>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setConfirmBatch(false)}
              disabled={startBatch.isPending}
            >
              Cancel
            </Button>
            <Button onClick={beginBatch} disabled={startBatch.isPending}>
              {startBatch.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <ArchiveRestore />
              )}
              Start batch
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
