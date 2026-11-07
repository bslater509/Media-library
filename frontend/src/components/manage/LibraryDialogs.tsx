import { useEffect, useState } from 'react'
import { FolderPlus, Pencil, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import type { FileEntry } from '../../lib/api'
import {
  useCreateFolder,
  useDeleteFiles,
  useRenameFile,
  useRenameFolder,
} from '../../lib/queries'
import {
  Button,
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
} from '../ui'

interface RenameDialogProps {
  file: FileEntry | null
  onClose: () => void
}

/** Rename one file in place; the server keeps its index row and poster. */
export function RenameDialog({ file, onClose }: RenameDialogProps) {
  const [name, setName] = useState('')
  const rename = useRenameFile()

  useEffect(() => {
    setName(file?.name ?? '')
  }, [file])

  const submit = async () => {
    if (!file) return
    const clean = name.trim()
    if (!clean || clean === file.name) {
      onClose()
      return
    }
    try {
      await rename.mutateAsync({ path: file.path, name: clean })
      toast.success('Renamed')
      onClose()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Rename failed')
    }
  }

  return (
    <Dialog open={file !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent aria-describedby={undefined} className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Pencil aria-hidden="true" className="size-4 text-fg-subtle" />
            Rename file
          </DialogTitle>
        </DialogHeader>
        <DialogBody>
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            onFocus={(event) => {
              const dot = event.currentTarget.value.lastIndexOf('.')
              event.currentTarget.setSelectionRange(
                0,
                dot > 0 ? dot : event.currentTarget.value.length,
              )
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void submit()
            }}
            aria-label="New filename"
            autoFocus
          />
          <p className="mt-2 text-xs text-fg-subtle">
            The extension is editable. A name that already exists is refused.
          </p>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={rename.isPending}>
            {rename.isPending ? 'Renaming…' : 'Rename'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface DeleteDialogProps {
  files: FileEntry[]
  onClose: () => void
}

/** Confirm deleting one or more files. */
export function DeleteDialog({ files, onClose }: DeleteDialogProps) {
  const remove = useDeleteFiles()
  const open = files.length > 0

  const submit = async () => {
    if (!files.length) return
    try {
      const result = await remove.mutateAsync(files.map((file) => file.path))
      if (result.failed.length) {
        toast.error(
          `${result.deleted.length} deleted, ${result.failed.length} failed`,
        )
      } else {
        toast.success(
          `${result.deleted.length} ${result.deleted.length === 1 ? 'file' : 'files'} deleted`,
        )
      }
      onClose()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Delete failed')
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent aria-describedby={undefined} className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Trash2 aria-hidden="true" className="size-4 text-danger" />
            Delete {files.length === 1 ? 'file' : `${files.length} files`}?
          </DialogTitle>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-2">
          <p className="text-sm text-fg-muted">
            This removes them from the library folder and the index. It cannot be
            undone from the preview.
          </p>
          <ul className="max-h-40 overflow-y-auto rounded-sm border border-border bg-surface-2 px-3 py-2 text-xs text-fg-muted">
            {files.slice(0, 8).map((file) => (
              <li key={file.path} className="truncate py-0.5 font-mono">
                {file.name}
              </li>
            ))}
            {files.length > 8 ? (
              <li className="py-0.5 text-fg-subtle">and {files.length - 8} more…</li>
            ) : null}
          </ul>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" onClick={() => void submit()} disabled={remove.isPending}>
            {remove.isPending ? 'Deleting…' : 'Delete'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface NewFolderDialogProps {
  parent: string | null
  open: boolean
  onClose: () => void
}

/** Create a real subfolder in the folder currently being browsed. */
export function NewFolderDialog({ parent, open, onClose }: NewFolderDialogProps) {
  const [name, setName] = useState('')
  const create = useCreateFolder()

  useEffect(() => {
    if (open) setName('')
  }, [open])

  const submit = async () => {
    const clean = name.trim()
    if (!parent || !clean) return
    try {
      await create.mutateAsync({ parent, name: clean })
      toast.success('Folder created')
      onClose()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not create folder')
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent aria-describedby={undefined} className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FolderPlus aria-hidden="true" className="size-4 text-fg-subtle" />
            New folder
          </DialogTitle>
        </DialogHeader>
        <DialogBody>
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Folder name"
            aria-label="Folder name"
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
          <Button onClick={() => void submit()} disabled={create.isPending || !name.trim()}>
            {create.isPending ? 'Creating…' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}


interface RenameFolderDialogProps {
  folder: { path: string; name: string } | null
  onClose: () => void
  onRenamed: (newPath: string) => void
}

/** Rename the current folder (or ZIP archive) in place. */
export function RenameFolderDialog({
  folder,
  onClose,
  onRenamed,
}: RenameFolderDialogProps) {
  const [name, setName] = useState('')
  const rename = useRenameFolder()

  useEffect(() => {
    setName(folder?.name ?? '')
  }, [folder])

  const submit = async () => {
    if (!folder) return
    const clean = name.trim()
    if (!clean || clean === folder.name) {
      onClose()
      return
    }
    try {
      const result = await rename.mutateAsync({ path: folder.path, name: clean })
      toast.success('Folder renamed')
      onRenamed(result.path)
      onClose()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Rename failed')
    }
  }

  return (
    <Dialog
      open={folder !== null}
      onOpenChange={(open) => (open ? undefined : onClose())}
    >
      <DialogContent aria-describedby={undefined} className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Pencil aria-hidden="true" className="size-4 text-fg-subtle" />
            Rename folder
          </DialogTitle>
        </DialogHeader>
        <DialogBody>
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void submit()
            }}
            aria-label="New folder name"
            autoFocus
          />
          <p className="mt-2 text-xs text-fg-subtle">
            Every indexed file, favorite and cached poster under it follows. A
            name that already exists is refused.
          </p>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={rename.isPending}>
            {rename.isPending ? 'Renaming…' : 'Rename'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
