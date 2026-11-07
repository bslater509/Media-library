import { useEffect, useState } from 'react'
import { ChevronLeft, Folder, FolderOpen, Loader2 } from 'lucide-react'
import { cn } from '../../lib/cn'
import { useFolderChildren } from '../../lib/queries'
import {
  Button,
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui'

interface FolderPickerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  /** Folder the picker opens on; defaults to the library root. */
  initialPath?: string | null
  /** Label for the confirm button. */
  confirmLabel?: string
  busy?: boolean
  onPick: (path: string) => void
}

/** Drill-down folder chooser used by Move. Index-only, so it stays instant. */
export function FolderPicker({
  open,
  onOpenChange,
  title,
  initialPath = null,
  confirmLabel = 'Move here',
  busy = false,
  onPick,
}: FolderPickerProps) {
  const [current, setCurrent] = useState<string | null>(initialPath)
  const children = useFolderChildren(current)
  const data = children.data

  // Reset the drill-down each time the dialog is reopened.
  useEffect(() => {
    if (open) setCurrent(initialPath)
  }, [open, initialPath])

  const parent =
    data && data.path !== data.root
      ? data.path.slice(0, data.path.lastIndexOf('/')) || data.root
      : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined} className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        <DialogBody className="flex flex-col gap-3">
          <div className="flex items-center gap-1.5">
            {parent ? (
              <Button
                variant="ghost"
                size="icon"
                aria-label="Up one folder"
                onClick={() => setCurrent(parent)}
              >
                <ChevronLeft />
              </Button>
            ) : null}
            <p className="min-w-0 flex-1 truncate rounded-sm border border-border bg-surface-2 px-2.5 py-1.5 font-mono text-[11px] text-fg-muted">
              {data?.path ?? current ?? 'Library'}
            </p>
          </div>

          <div className="h-64 overflow-y-auto rounded-md border border-border">
            {children.isLoading ? (
              <div className="flex items-center gap-2 px-3 py-6 text-sm text-fg-subtle">
                <Loader2 className="size-4 animate-spin" /> Loading folders…
              </div>
            ) : children.data?.folders.length ? (
              children.data.folders.map((folder) => (
                <button
                  key={folder.path}
                  type="button"
                  onClick={() => setCurrent(folder.path)}
                  className={cn(
                    'flex w-full items-center gap-2 border-b border-border px-3 py-2 text-left text-sm last:border-b-0',
                    'text-fg-muted hover:bg-surface-2 hover:text-fg',
                  )}
                >
                  <Folder aria-hidden="true" className="size-4 shrink-0 opacity-70" />
                  <span className="min-w-0 flex-1 truncate">{folder.name}</span>
                  <span className="shrink-0 text-[10px] text-fg-subtle tabular-nums">
                    {folder.subfolder_count > 0 ? `${folder.subfolder_count} folders` : ''}
                  </span>
                </button>
              ))
            ) : (
              <p className="px-3 py-6 text-center text-sm text-fg-subtle">
                No subfolders here.
              </p>
            )}
          </div>
        </DialogBody>

        <DialogFooter className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 text-xs text-fg-subtle">
            <FolderOpen aria-hidden="true" className="size-3.5" />
            Moves into the folder shown above
          </span>
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              disabled={!data || busy}
              onClick={() => data && onPick(data.path)}
            >
              {busy ? <Loader2 className="size-4 animate-spin" /> : null}
              {confirmLabel}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
