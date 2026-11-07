import { useState } from 'react'
import {
  CheckCircle2,
  DatabaseBackup,
  Download,
  HardDriveDownload,
  Loader2,
  RotateCcw,
  ShieldCheck,
} from 'lucide-react'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'
import {
  useBackupRestore,
  useBackupSnapshot,
  useBackupStatus,
  useBackupVerify,
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

export function BackupsPage() {
  const status = useBackupStatus()
  const create = useBackupSnapshot()
  const verify = useBackupVerify()
  const restore = useBackupRestore()
  const client = useQueryClient()
  const [confirmRestore, setConfirmRestore] = useState(false)

  const data = status.data

  const snapshot = () => {
    create.mutate(undefined, {
      onSuccess: (result) => {
        toast.success(
          `Snapshot created: ${result.files.toLocaleString()} files, ${result.thumbnails.toLocaleString()} posters`,
        )
      },
      onError: (error) =>
        toast.error(error instanceof Error ? error.message : 'Snapshot failed'),
    })
  }

  const check = () => {
    verify.mutate(undefined, {
      onSuccess: (result) => {
        if (result.snapshot_integrity === 'ok' && result.live_integrity === 'ok') {
          toast.success(
            `Snapshot verified: ${result.files.toLocaleString()} files, ${result.thumbnails.toLocaleString()} posters`,
          )
        } else {
          toast.error('Integrity check reported a problem')
        }
      },
      onError: (error) =>
        toast.error(error instanceof Error ? error.message : 'Verify failed'),
    })
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-4 sm:px-5">
      <div>
        <h1 className="flex items-center gap-2 text-base font-semibold tracking-tight">
          <DatabaseBackup aria-hidden="true" className="size-4 text-fg-subtle" />
          Library backup
        </h1>
        <p className="text-xs text-fg-muted">
          A consistent copy of the SQLite index plus the cached poster frames,
          written into the library folder. Restoring is a service-stopped
          operation.
        </p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle>Latest snapshot</CardTitle>
            {data?.exists ? (
              <Badge variant="success">
                {data.created_at_str ?? 'available'}
              </Badge>
            ) : (
              <Badge variant="warning">none yet</Badge>
            )}
          </div>
        </CardHeader>
        <CardBody className="flex flex-col gap-3">
          {status.isLoading ? (
            <div className="flex items-center gap-2 py-4 text-sm text-fg-subtle">
              <Spinner /> Checking for a snapshot…
            </div>
          ) : data?.exists ? (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Files" value={(data.files ?? 0).toLocaleString()} />
                <Stat label="Folders" value={(data.folders ?? 0).toLocaleString()} />
                <Stat
                  label="Posters"
                  value={(data.thumbnails ?? 0).toLocaleString()}
                />
                <Stat label="Version" value={data.version ?? '—'} />
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-fg-subtle">
                <span className="font-mono">db {data.db_size_formatted}</span>
                <span className="font-mono">thumbs {data.tar_size_formatted}</span>
                <span className="truncate font-mono">{data.directory}</span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="secondary" size="sm" onClick={check} disabled={verify.isPending}>
                  {verify.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <ShieldCheck />
                  )}
                  Verify snapshot
                </Button>
                <a
                  href="/api/backups/download?kind=db"
                  className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-border px-3 text-[13px] text-fg-muted hover:bg-surface-2 hover:text-fg"
                >
                  <Download className="size-4" /> Database
                </a>
                <a
                  href="/api/backups/download?kind=thumbs"
                  className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-border px-3 text-[13px] text-fg-muted hover:bg-surface-2 hover:text-fg"
                >
                  <Download className="size-4" /> Posters
                </a>
              </div>
            </>
          ) : (
            <p className="py-2 text-xs text-fg-subtle">
              No v2 snapshot yet. The first one copies the index and every cached
              poster; it can take a while on a large library.
            </p>
          )}
          <div className="flex items-center justify-between gap-2 border-t border-border pt-3">
            <p className="text-[11px] leading-4 text-fg-subtle">
              Written to <span className="font-mono">.media_library_backup/v2/</span>,
              next to the current app's snapshot, without touching it.
            </p>
            <Button onClick={snapshot} disabled={create.isPending}>
              {create.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <HardDriveDownload />
              )}
              {create.isPending ? 'Creating snapshot…' : 'Create snapshot'}
            </Button>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <RotateCcw aria-hidden="true" className="size-4 text-fg-subtle" />
            Restore
          </CardTitle>
        </CardHeader>
        <CardBody className="flex flex-col gap-3">
          {data?.allow_live_restore ? (
            <>
              <p className="text-xs leading-5 text-warning">
                Live restore is enabled. It replaces the database and posters in
                place; restart the service afterwards.
              </p>
              <Button
                variant="danger"
                className="w-fit"
                onClick={() => setConfirmRestore(true)}
              >
                <RotateCcw /> Restore snapshot
              </Button>
            </>
          ) : (
            <>
              <p className="text-xs leading-5 text-fg-muted">
                Live restore is disabled by <code>V2_ALLOW_LIVE_RESTORE</code>.
                Replacing the database under running writers can corrupt it, so use
                the stopped-service command below.
              </p>
              <div className="rounded-md border border-border bg-surface-2 px-3 py-2">
                <p className="text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
                  Recovery command (service stopped)
                </p>
                <code className="whitespace-pre-wrap font-mono text-[11px] leading-5 text-fg-muted">
                  systemctl stop media-library{'\n'}
                  ./venv/bin/python -m media_library_v2.restore --yes{'\n'}
                  systemctl start media-library
                </code>
              </div>
            </>
          )}
        </CardBody>
      </Card>

      <Dialog open={confirmRestore} onOpenChange={setConfirmRestore}>
        <DialogContent aria-describedby={undefined} className="max-w-md">
          <DialogHeader>
            <DialogTitle>Restore the snapshot now?</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <p className="text-sm leading-6 text-fg-muted">
              The current database and cached posters are replaced with the
              snapshot. Restart the service immediately afterwards.
            </p>
          </DialogBody>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmRestore(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              disabled={restore.isPending}
              onClick={() =>
                restore.mutate(undefined, {
                  onSuccess: (result) => {
                    toast.success(
                      `Restored ${result.files.toLocaleString()} files and ${result.thumbnails.toLocaleString()} posters — restart the service`,
                    )
                    setConfirmRestore(false)
                    void client.invalidateQueries()
                  },
                  onError: (error) => {
                    toast.error(
                      error instanceof Error ? error.message : 'Restore failed',
                    )
                    setConfirmRestore(false)
                  },
                })
              }
            >
              {restore.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <RotateCcw />
              )}
              Restore
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {!data?.exists && !status.isLoading ? (
        <EmptyState
          icon={<CheckCircle2 />}
          title="Backups run on demand in v2"
          description="The owned runtime also snapshots automatically on an interval and at shutdown; this page creates, verifies or restores one by hand."
        />
      ) : null}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-surface-2 px-3 py-2">
      <p className="text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
        {label}
      </p>
      <p className="font-mono text-sm font-semibold tabular-nums">{value}</p>
    </div>
  )
}
