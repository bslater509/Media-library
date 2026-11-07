import { useEffect, useMemo, useState } from 'react'
import { FolderX, Loader2, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import type { EmptyFolder } from '../lib/api'
import {
  useDeleteEmptyFolders,
  useEmptyFolders,
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
  Spinner,
} from '../components/ui'

export function EmptyFoldersPage() {
  const candidates = useEmptyFolders()
  const remove = useDeleteEmptyFolders()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirmOpen, setConfirmOpen] = useState(false)

  const folders: EmptyFolder[] = candidates.data?.folders ?? []

  useEffect(() => {
    setSelected((current) => {
      if (current.size === 0) return current
      const available = new Set(folders.map((folder) => folder.path))
      const next = new Set([...current].filter((path) => available.has(path)))
      return next.size === current.size ? current : next
    })
  }, [candidates.data])

  const allSelected = folders.length > 0 && selected.size === folders.length
  const targets = useMemo(
    () => (selected.size ? [...selected] : folders.map((folder) => folder.path)),
    [folders, selected],
  )

  const toggle = (path: string) => {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  const submit = () => {
    remove.mutate(targets, {
      onSuccess: (result) => {
        toast.success(
          `${result.deleted.length} empty folder${result.deleted.length === 1 ? '' : 's'} removed`,
        )
        if (result.failed.length) {
          toast.error(`${result.failed.length} folder(s) could not be removed`)
        }
        setSelected(new Set())
        setConfirmOpen(false)
      },
      onError: (error) =>
        toast.error(error instanceof Error ? error.message : 'Cleanup failed'),
    })
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-4 sm:px-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="flex items-center gap-2 text-base font-semibold tracking-tight">
            <FolderX aria-hidden="true" className="size-4 text-fg-subtle" />
            Empty folders
          </h1>
          <p className="text-xs text-fg-muted">
            Leaf folders with no media and no subfolders. Only OS junk
            (<code>.DS_Store</code>, <code>Thumbs.db</code>) is removed with them.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline">{folders.length} candidates</Badge>
          <Button
            variant="danger"
            size="sm"
            disabled={!targets.length || remove.isPending}
            onClick={() => setConfirmOpen(true)}
          >
            <Trash2 /> Delete {targets.length || ''}
          </Button>
        </div>
      </div>

      {candidates.isLoading ? (
        <div className="flex items-center gap-2 py-8 text-sm text-fg-subtle">
          <Spinner /> Scanning the index…
        </div>
      ) : folders.length === 0 ? (
        <EmptyState
          icon={<FolderX />}
          title="No empty folders"
          description="Every leaf folder in the index still holds media."
        />
      ) : (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-2">
              <CardTitle>Select folders to remove</CardTitle>
              <button
                type="button"
                className="text-[11px] text-fg-subtle hover:text-fg"
                onClick={() =>
                  setSelected(
                    allSelected ? new Set() : new Set(folders.map((f) => f.path)),
                  )
                }
              >
                {allSelected ? 'Deselect all' : 'Select all'}
              </button>
            </div>
          </CardHeader>
          <CardBody className="flex flex-col gap-1">
            {folders.map((folder) => (
              <label
                key={folder.path}
                className="flex cursor-pointer items-center gap-2 rounded-sm px-1.5 py-1.5 text-xs hover:bg-surface-2"
              >
                <input
                  type="checkbox"
                  aria-label={`Select ${folder.path}`}
                  checked={selected.has(folder.path)}
                  onChange={() => toggle(folder.path)}
                  className="size-3.5 accent-[var(--accent)]"
                />
                <span className="min-w-0 flex-1 truncate font-mono text-fg-muted">
                  {folder.path}
                </span>
              </label>
            ))}
          </CardBody>
        </Card>
      )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent aria-describedby={undefined} className="max-w-md">
          <DialogHeader>
            <DialogTitle>Remove {targets.length} empty folder{targets.length === 1 ? '' : 's'}?</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <p className="text-sm text-fg-muted">
              Folders are removed from disk and the index. A folder that gained
              a file since the scan is left untouched and reported.
            </p>
          </DialogBody>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={submit} disabled={remove.isPending}>
              {remove.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Trash2 />
              )}
              Remove folders
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
