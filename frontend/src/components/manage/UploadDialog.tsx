import { useEffect, useRef, useState } from 'react'
import { Check, FileUp, Loader2, UploadCloud, X } from 'lucide-react'
import { toast } from 'sonner'
import { formatBytes } from '../../lib/format'
import { api, type ConflictPolicy } from '../../lib/api'
import { cn } from '../../lib/cn'
import { makeUploadId, uploadFile, type UploadProgress } from '../../lib/upload'
import {
  Badge,
  Button,
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  IconButton,
  Progress,
  Select,
} from '../ui'

interface UploadDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  folder: string
  onCompleted: () => void
}

/** Chunked, resumable uploads into the folder currently being browsed. */
export function UploadDialog({
  open,
  onOpenChange,
  folder,
  onCompleted,
}: UploadDialogProps) {
  const [items, setItems] = useState<UploadProgress[]>([])
  const [conflict, setConflict] = useState<ConflictPolicy>('auto_rename')
  const [busy, setBusy] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Leaving the dialog (unmount, navigation) cancels an in-flight upload
  // instead of letting the chunk loop finish invisibly.
  useEffect(() => () => abortRef.current?.abort(), [])

  const upsert = (progress: UploadProgress) => {
    setItems((current) => {
      const index = current.findIndex((item) => item.uploadId === progress.uploadId)
      if (index === -1) return [...current, progress]
      const next = current.slice()
      next[index] = progress
      return next
    })
  }

  /** Mark a file failed under its real upload id, so rows never collide. */
  const markFailed = (file: File, message: string) => {
    const uploadId = makeUploadId(file)
    setItems((current) => {
      const index = current.findIndex(
        (item) => item.uploadId === uploadId || item.file === file,
      )
      if (index === -1) {
        return [
          ...current,
          { file, uploadId, sent: 0, total: file.size, state: 'error', error: message },
        ]
      }
      const next = current.slice()
      next[index] = { ...next[index], uploadId, state: 'error', error: message }
      return next
    })
  }

  const start = async (files: File[]) => {
    if (!files.length || busy) return
    if (files.length > 40) {
      toast.error('Choose at most 40 files at a time')
      return
    }
    setBusy(true)
    setItems([])
    const controller = new AbortController()
    abortRef.current = controller
    let uploaded = 0
    let skipped = 0
    let failed = 0

    for (const file of files) {
      if (controller.signal.aborted) break
      try {
        const result = await uploadFile(
          file,
          api,
          { folder, conflict, signal: controller.signal },
          { onProgress: upsert },
        )
        if (result.skipped) skipped += 1
        else uploaded += 1
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          markFailed(file, 'Cancelled')
        } else {
          failed += 1
          markFailed(
            file,
            error instanceof Error ? error.message : 'Upload failed',
          )
        }
      }
    }

    abortRef.current = null
    setBusy(false)
    if (uploaded || skipped) {
      toast.success(
        `${uploaded} uploaded${skipped ? `, ${skipped} skipped` : ''}${
          failed ? `, ${failed} failed` : ''
        }`,
      )
      onCompleted()
    } else if (failed) {
      toast.error('No files were uploaded')
    }
  }

  const cancelAll = () => {
    abortRef.current?.abort()
  }

  return (
    <Dialog open={open} onOpenChange={(next) => (busy ? undefined : onOpenChange(next))}>
      <DialogContent aria-describedby={undefined} className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Upload to this folder</DialogTitle>
        </DialogHeader>

        <DialogBody className="flex flex-col gap-4">
          <p className="truncate rounded-sm border border-border bg-surface-2 px-2.5 py-1.5 font-mono text-[11px] text-fg-muted">
            {folder}
          </p>

          <div
            onDragOver={(event) => {
              event.preventDefault()
              setDragOver(true)
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(event) => {
              event.preventDefault()
              setDragOver(false)
              void start(Array.from(event.dataTransfer.files))
            }}
            className={cn(
              'flex flex-col items-center justify-center gap-2 rounded-md border border-dashed px-4 py-8 text-center',
              dragOver ? 'border-accent bg-accent-soft' : 'border-border-strong bg-surface-2',
            )}
          >
            <UploadCloud aria-hidden="true" className="size-6 text-fg-subtle" />
            <p className="text-sm text-fg-muted">Drop files here, or</p>
            <Button
              variant="secondary"
              size="sm"
              disabled={busy}
              onClick={() => inputRef.current?.click()}
            >
              <FileUp /> Choose files
            </Button>
            <input
              ref={inputRef}
              type="file"
              multiple
              hidden
              onChange={(event) => {
                const files = Array.from(event.target.files ?? [])
                event.target.value = ''
                void start(files)
              }}
            />
          </div>

          <div className="flex items-center justify-between gap-2 text-xs text-fg-muted">
            <span>If a name already exists</span>
            <Select
              aria-label="Conflict policy"
              value={conflict}
              disabled={busy}
              onChange={(event) => setConflict(event.target.value as ConflictPolicy)}
              className="h-8 w-44 text-xs"
            >
              <option value="auto_rename">Keep both</option>
              <option value="skip">Skip the file</option>
              <option value="error">Stop with an error</option>
            </Select>
          </div>

          {items.length ? (
            <ul className="flex max-h-56 flex-col gap-2 overflow-y-auto">
              {items.map((item, index) => {
                const percent = item.total
                  ? Math.round((item.sent / item.total) * 100)
                  : 0
                return (
                  <li
                    key={`${item.uploadId}-${item.file.name}-${index}`}
                    className="flex flex-col gap-1 rounded-sm border border-border px-2.5 py-2"
                  >
                    <div className="flex items-center gap-2 text-xs">
                      <span className="min-w-0 flex-1 truncate text-fg-muted">
                        {item.file.name}
                      </span>
                      <span className="shrink-0 text-fg-subtle tabular-nums">
                        {formatBytes(item.sent)} / {formatBytes(item.total)}
                      </span>
                      {item.state === 'done' ? (
                        <Check aria-label="Uploaded" className="size-3.5 text-success" />
                      ) : item.state === 'error' ? (
                        <X aria-label="Failed" className="size-3.5 text-danger" />
                      ) : item.state === 'skipped' ? (
                        <Badge variant="warning">skipped</Badge>
                      ) : (
                        <Loader2 className="size-3.5 animate-spin text-fg-subtle" />
                      )}
                    </div>
                    <Progress value={percent} />
                    {item.error ? (
                      <p className="text-[11px] text-danger">{item.error}</p>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          ) : null}
        </DialogBody>

        <DialogFooter className="flex items-center justify-between gap-2">
          <span className="text-[11px] text-fg-subtle">
            Chunked and resumable — re-choosing the same file continues it.
          </span>
          <div className="flex items-center gap-2">
            {busy ? (
              <Button variant="ghost" onClick={cancelAll}>
                Cancel
              </Button>
            ) : null}
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => onOpenChange(false)}
            >
              Close
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
