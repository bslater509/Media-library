import { useState } from 'react'
import { Link } from 'react-router'
import {
  Archive,
  ArchiveRestore,
  ArrowRight,
  Loader2,
  FolderArchive,
} from 'lucide-react'
import { toast } from 'sonner'
import type { ZipArrayItem, ZipCandidate } from '../lib/api'
import { useZipCompress, useZipOverview, useZipUnpack } from '../lib/queries'
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

  const busy = compress.isPending || unpack.isPending
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

      {overview.isLoading ? (
        <div className="flex items-center gap-2 py-8 text-sm text-fg-subtle">
          <Spinner /> Reading the index…
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="flex items-center gap-2">
                  <FolderArchive aria-hidden="true" className="size-4 text-fg-subtle" />
                  Folders ready to archive
                </CardTitle>
                <Badge variant="outline">{folders.length}</Badge>
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
                <CardTitle className="flex items-center gap-2">
                  <ArchiveRestore aria-hidden="true" className="size-4 text-fg-subtle" />
                  Archives
                </CardTitle>
                <Badge variant="outline">{archives.length}</Badge>
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
    </div>
  )
}
