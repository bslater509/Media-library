import { useMemo, useState } from 'react'
import { Pencil } from 'lucide-react'
import { toast } from 'sonner'
import type { FileEntry } from '../../lib/api'
import { bulkName, type RenameMode } from '../../lib/bulk_rename'
import { useBulkRename } from '../../lib/queries'
import {
  Button,
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Segmented,
} from '../ui'

interface BulkRenameDialogProps {
  files: FileEntry[]
  onClose: () => void
}

/** Rename a batch of files with a prefix, suffix or find-and-replace. */
export function BulkRenameDialog({ files, onClose }: BulkRenameDialogProps) {
  const open = files.length > 0
  const [mode, setMode] = useState<RenameMode>('prefix')
  const [value, setValue] = useState('')
  const [find, setFind] = useState('')
  const [replace, setReplace] = useState('')
  const rename = useBulkRename()

  const renames = useMemo(
    () =>
      files
        .map((file) => ({
          path: file.path,
          name: bulkName(file.name, mode, value, find, replace),
          original: file.name,
        }))
        .filter((item) => item.name !== item.original),
    [files, mode, value, find, replace],
  )

  const targets = useMemo(
    () => new Set(renames.map((item) => item.name.toLowerCase())),
    [renames],
  )
  const duplicate = targets.size !== renames.length
  const active = mode === 'replace' ? find.length > 0 : value.length > 0
  const ready = active && renames.length > 0 && !duplicate && !rename.isPending

  const submit = async () => {
    try {
      const result = await rename.mutateAsync(
        renames.map(({ path, name }) => ({ path, name })),
      )
      if (result.failed.length) {
        toast.error(
          `${result.renamed.length} renamed, ${result.failed.length} failed: ${result.failed[0].error}`,
        )
      } else {
        toast.success(
          `${result.renamed.length} ${result.renamed.length === 1 ? 'file' : 'files'} renamed`,
        )
      }
      if (!result.failed.length) onClose()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Rename failed')
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent aria-describedby={undefined} className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Pencil aria-hidden="true" className="size-4 text-fg-subtle" />
            Modify {files.length} names
          </DialogTitle>
        </DialogHeader>

        <DialogBody className="flex flex-col gap-3">
          <Segmented<RenameMode>
            aria-label="Rename mode"
            value={mode}
            onChange={setMode}
            options={[
              { value: 'prefix', label: 'Add prefix' },
              { value: 'suffix', label: 'Add suffix' },
              { value: 'replace', label: 'Find & replace' },
            ]}
          />

          {mode === 'replace' ? (
            <div className="grid grid-cols-2 gap-2">
              <label className="flex flex-col gap-1 text-xs text-fg-muted">
                Find
                <Input value={find} onChange={(event) => setFind(event.target.value)} />
              </label>
              <label className="flex flex-col gap-1 text-xs text-fg-muted">
                Replace with
                <Input
                  value={replace}
                  onChange={(event) => setReplace(event.target.value)}
                />
              </label>
            </div>
          ) : (
            <label className="flex flex-col gap-1 text-xs text-fg-muted">
              {mode === 'prefix' ? 'Prefix' : 'Suffix'}
              <Input
                value={value}
                autoFocus
                onChange={(event) => setValue(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && ready) void submit()
                }}
              />
            </label>
          )}

          <div className="rounded-md border border-border bg-surface-2 px-3 py-2">
            <p className="pb-1 text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
              Preview
            </p>
            <ul className="flex max-h-40 flex-col gap-1 overflow-y-auto">
              {renames.slice(0, 12).map((item) => (
                <li key={item.path} className="truncate text-xs text-fg-muted">
                  <span className="text-fg-subtle">{item.original}</span>
                  <span aria-hidden="true" className="px-1.5">
                    →
                  </span>
                  <span className="text-fg">{item.name}</span>
                </li>
              ))}
              {renames.length === 0 ? (
                <li className="text-xs text-fg-subtle">
                  No names change yet.
                </li>
              ) : null}
              {renames.length > 12 ? (
                <li className="text-[11px] text-fg-subtle">
                  and {renames.length - 12} more
                </li>
              ) : null}
            </ul>
          </div>
          {duplicate ? (
            <p className="text-xs text-danger" role="alert">
              Two files would get the same name. Adjust the pattern.
            </p>
          ) : null}
        </DialogBody>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={!ready}>
            {rename.isPending ? 'Renaming…' : `Rename ${renames.length}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
