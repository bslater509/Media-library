import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'
import { formatBytes } from '../lib/format'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock,
  Download,
  FolderOpen,
  Loader2,
  RefreshCw,
  RotateCcw,
  Trash2,
  X,
  XCircle,
} from 'lucide-react'
import { toast } from 'sonner'
import type { DownloadFileProgress, DownloadJob } from '../lib/api'
import { cn } from '../lib/cn'
import {
  useDownloadCancel,
  useDownloadClear,
  useDownloadHistory,
  useDownloadQueue,
  useDownloadRetry,
  useDownloadStatus,
  useInfo,
} from '../lib/queries'
import { readRecentTargets, rememberTarget, targetName } from '../lib/recent-targets'
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  IconButton,
  Progress,
  Spinner,
  Textarea,
} from '../components/ui'
import { FolderPicker } from '../components/manage/FolderPicker'

function splitUrls(raw: string): string[] {
  return raw
    .split(/\s+/)
    .map((value) => value.trim())
    .filter(Boolean)
}

const STATUS_STYLES: Record<string, string> = {
  queued: 'text-fg-subtle',
  downloading: 'text-accent',
  completed: 'text-success',
  failed: 'text-danger',
  cancelled: 'text-warning',
}

export function DownloadsPage() {
  const info = useInfo()
  const status = useDownloadStatus()
  const [historyLimit, setHistoryLimit] = useState(50)
  const history = useDownloadHistory(historyLimit)
  const queue = useDownloadQueue()
  const cancel = useDownloadCancel()
  const retry = useDownloadRetry()
  const clear = useDownloadClear()

  const [params] = useSearchParams()
  const [urls, setUrls] = useState('')
  const [target, setTarget] = useState<string | null>(params.get('folder'))
  const [recent, setRecent] = useState<string[]>(() => readRecentTargets())
  const [pickerOpen, setPickerOpen] = useState(false)

  useEffect(() => {
    const folder = params.get('folder')
    if (folder) setTarget(folder)
  }, [params])

  const root = info.data?.library_root ?? ''
  const effectiveTarget = target ?? root
  const tools = status.data?.tools
  const noDownloader = tools ? !tools.ytdlp && !tools.gallery_dl : false
  const parsed = useMemo(() => splitUrls(urls), [urls])
  const active = status.data?.active ?? []
  const queuedJobs = status.data?.queued ?? []

  const enqueue = async () => {
    if (!parsed.length) {
      toast.error('Paste at least one URL')
      return
    }
    if (!effectiveTarget) {
      toast.error('Choose a destination folder')
      return
    }
    try {
      const result = await queue.mutateAsync({
        urls: parsed,
        destination: effectiveTarget,
      })
      setUrls('')
      setRecent(rememberTarget(effectiveTarget))
      toast.success(
        `${result.jobs.length} queued${result.rejected.length ? `, ${result.rejected.length} skipped` : ''}`,
      )
      if (result.rejected.length) {
        toast.info(result.rejected.map((item) => item.url).join(', '))
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not queue the download')
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-3 py-4 sm:px-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-base font-semibold tracking-tight">Downloads</h1>
          <p className="text-xs text-fg-muted">
            Queue URLs or playlists; finished files land in the folder you choose.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <ToolBadge label="yt-dlp" ok={tools?.ytdlp} />
          <ToolBadge label="gallery-dl" ok={tools?.gallery_dl} />
          {status.data ? (
            <Badge variant="outline">
              {status.data.active_count + status.data.queued_count} in queue
            </Badge>
          ) : null}
        </div>
      </div>

      {noDownloader ? (
        <div className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-fg-muted">
          <AlertTriangle aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-warning" />
          Neither yt-dlp nor gallery-dl is installed, so downloads cannot start. Install
          one into the app virtualenv and restart.
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
        <Card className="h-fit">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Download aria-hidden="true" className="size-4 text-fg-subtle" />
              New downloads
            </CardTitle>
          </CardHeader>
          <CardBody className="flex flex-col gap-3">
            <Textarea
              value={urls}
              onChange={(event) => setUrls(event.target.value)}
              placeholder={'One URL per line\nhttps://…\nhttps://…'}
              aria-label="Download URLs"
              rows={5}
              className="font-mono text-xs"
            />
            <div className="flex items-center justify-between gap-2 text-[11px] text-fg-subtle">
              <span>{parsed.length} URL{parsed.length === 1 ? '' : 's'} ready</span>
              <button
                type="button"
                onClick={() => setUrls('')}
                className="hover:text-fg disabled:opacity-40"
                disabled={!urls}
              >
                Clear
              </button>
            </div>

            <div>
              <p className="pb-1.5 text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
                Destination
              </p>
              <button
                type="button"
                onClick={() => setPickerOpen(true)}
                className="flex w-full items-center gap-2 rounded-md border border-border bg-surface-2 px-2.5 py-2 text-left text-xs text-fg-muted hover:border-border-strong hover:text-fg"
              >
                <FolderOpen aria-hidden="true" className="size-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate font-mono">
                  {effectiveTarget || 'Choose a folder…'}
                </span>
              </button>
            </div>

            {recent.length ? (
              <div className="flex flex-wrap gap-1.5">
                {recent.map((path) => (
                  <button
                    key={path}
                    type="button"
                    onClick={() => setTarget(path)}
                    title={path}
                    className={cn(
                      'rounded-full border px-2.5 py-1 text-[11px]',
                      path === effectiveTarget
                        ? 'border-accent bg-accent-soft text-accent'
                        : 'border-border text-fg-muted hover:bg-surface-2 hover:text-fg',
                    )}
                  >
                    {targetName(path)}
                  </button>
                ))}
              </div>
            ) : null}

            <Button
              onClick={() => void enqueue()}
              disabled={queue.isPending || !parsed.length || noDownloader}
            >
              {queue.isPending ? <Loader2 className="size-4 animate-spin" /> : <Download />}
              Queue downloads
            </Button>
            <p className="text-[11px] leading-5 text-fg-subtle">
              Live streams and upcoming premieres are skipped. Sites yt-dlp cannot
              handle fall back to gallery-dl automatically.
            </p>
          </CardBody>
        </Card>

        <div className="flex min-w-0 flex-col gap-4">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <CardTitle>Active &amp; queued</CardTitle>
                <span className="flex items-center gap-1.5 text-[11px] text-fg-subtle">
                  {status.isFetching ? <Spinner size="sm" /> : null}
                  {active.length + queuedJobs.length} job
                  {active.length + queuedJobs.length === 1 ? '' : 's'}
                </span>
              </div>
            </CardHeader>
            <CardBody className="flex flex-col gap-2">
              {active.length + queuedJobs.length === 0 ? (
                <p className="py-3 text-center text-xs text-fg-subtle">
                  Nothing downloading right now.
                </p>
              ) : (
                [...active, ...queuedJobs].map((job) => (
                  <JobRow
                    key={job.id}
                    job={job}
                    onCancel={() =>
                      cancel.mutate(job.id, {
                        onError: (error) =>
                          toast.error(
                            error instanceof Error ? error.message : 'Cancel failed',
                          ),
                      })
                    }
                  />
                ))
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <CardTitle>Finished</CardTitle>
                <div className="flex items-center gap-1">
                <IconButton
                  aria-label="Refresh downloads"
                  title="Refresh"
                  onClick={() => {
                    void status.refetch()
                    void history.refetch()
                  }}
                >
                  <RefreshCw className={cn(status.isFetching && 'animate-spin')} />
                </IconButton>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={clear.isPending || !history.data?.total}
                  onClick={() =>
                    clear.mutate(undefined, {
                      onSuccess: (result) =>
                        toast.success(
                          `Cleared ${result.cleared} finished download${result.cleared === 1 ? '' : 's'}`,
                        ),
                      onError: (error) =>
                        toast.error(
                          error instanceof Error
                            ? error.message
                            : 'Could not clear history',
                        ),
                    })
                  }
                  >
                    <Trash2 /> Clear
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardBody className="flex flex-col gap-2">
              {history.isLoading ? (
                <div className="flex items-center gap-2 py-4 text-xs text-fg-subtle">
                  <Spinner /> Loading history…
                </div>
              ) : history.data?.items.length ? (
                <>
                  {history.data.items.map((job) => (
                    <FinishedRow
                      key={job.id}
                      job={job}
                      onRetry={() =>
                        retry.mutate(job.id, {
                          onSuccess: () => toast.success('Queued again'),
                          onError: (error) =>
                            toast.error(
                              error instanceof Error ? error.message : 'Retry failed',
                            ),
                        })
                      }
                    />
                  ))}
                  {history.data.total > history.data.items.length ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="self-center"
                      onClick={() => setHistoryLimit((value) => Math.min(500, value + 50))}
                    >
                      Load older downloads
                    </Button>
                  ) : null}
                </>
              ) : (
                <EmptyState
                  icon={<Download />}
                  title="No downloads yet"
                  description="Finished and failed jobs appear here, newest first."
                />
              )}
            </CardBody>
          </Card>
        </div>
      </div>

      <FolderPicker
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        title="Choose a download folder"
        initialPath={effectiveTarget || null}
        confirmLabel="Use this folder"
        onPick={(path) => {
          setTarget(path)
          setPickerOpen(false)
        }}
      />
    </div>
  )
}

function ToolBadge({ label, ok }: { label: string; ok?: boolean }) {
  return (
    <Badge variant={ok ? 'success' : 'warning'}>
      {label} {ok ? 'ready' : 'missing'}
    </Badge>
  )
}

function JobRow({ job, onCancel }: { job: DownloadJob; onCancel: () => void }) {
  const playlist =
    job.playlist_index !== null && job.playlist_count !== null
      ? `${job.playlist_index}/${job.playlist_count}`
      : null
  const files = job.file_progress ?? []
  const [filesExpanded, setFilesExpanded] = useState<boolean | null>(null)
  const multiFile = files.length > 1
  const expanded = filesExpanded ?? multiFile
  const doneCount = files.filter((row) => row.done).length
  const current = files.findIndex((row) => !row.done)
  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-border px-2.5 py-2">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            'flex size-4 shrink-0 items-center justify-center',
            STATUS_STYLES[job.status],
          )}
        >
          {job.status === 'downloading' ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Clock className="size-3.5" />
          )}
        </span>
        <span className="min-w-0 flex-1 truncate text-sm text-fg">
          {job.title || job.url}
        </span>
        {playlist ? <Badge variant="outline">{playlist}</Badge> : null}
        <Badge variant="outline">{job.downloader || 'queued'}</Badge>
        <IconButton
          aria-label="Cancel download"
          title="Cancel"
          size={28}
          onClick={onCancel}
        >
          <X />
        </IconButton>
      </div>
      <Progress value={job.percent} />
      {multiFile ? (
        <button
          type="button"
          onClick={() => setFilesExpanded(!expanded)}
          className="flex items-center gap-1 self-start text-[10px] font-semibold tracking-wider text-fg-subtle uppercase hover:text-fg"
          aria-expanded={expanded}
        >
          <ChevronDown
            aria-hidden="true"
            className={cn('size-3 transition-transform', expanded && 'rotate-180')}
          />
          {doneCount} / {files.length} files
        </button>
      ) : null}
      {multiFile && expanded ? (
        <FileProgressList rows={files} current={current} />
      ) : null}
      <div className="flex items-center justify-between gap-2 text-[10px] text-fg-subtle">
        <span className="truncate font-mono">{job.url}</span>
        <span className="shrink-0 tabular-nums">
          {formatBytes(job.downloaded_bytes)}
          {job.total_bytes ? ` / ${formatBytes(job.total_bytes)}` : ''}
          {job.speed ? ` · ${job.speed}` : ''}
          {job.eta ? ` · ETA ${job.eta}s` : ''}
        </span>
      </div>
    </div>
  )
}

function FileProgressList({
  rows,
  current,
}: {
  rows: DownloadFileProgress[]
  current: number
}) {
  return (
    <ul className="flex max-h-44 flex-col gap-1 overflow-y-auto rounded-md bg-surface-2/60 px-2 py-1.5">
      {rows.map((row, index) => (
        <li key={`${row.name}-${index}`} className="flex items-center gap-2 text-[11px]">
          <span
            className={cn(
              'flex size-3.5 shrink-0 items-center justify-center',
              row.done ? 'text-success' : index === current ? 'text-accent' : 'text-fg-subtle',
            )}
          >
            {row.done ? (
              <CheckCircle2 className="size-3" />
            ) : index === current ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <Clock className="size-3" />
            )}
          </span>
          <span
            className={cn(
              'min-w-0 flex-1 truncate font-mono',
              row.done ? 'text-fg-muted' : 'text-fg-subtle',
            )}
            title={row.name}
          >
            {row.name}
          </span>
          <span className="shrink-0 tabular-nums text-fg-subtle">
            {row.total_bytes
              ? `${formatBytes(row.downloaded_bytes ?? 0)} / ${formatBytes(row.total_bytes)}`
              : row.downloaded_bytes
                ? formatBytes(row.downloaded_bytes)
                : row.done
                  ? 'done'
                  : 'queued'}
          </span>
        </li>
      ))}
    </ul>
  )
}

function FinishedRow({ job, onRetry }: { job: DownloadJob; onRetry: () => void }) {
  const meta = useMemo(() => {
    if (job.status === 'completed') {
      const rows = job.file_progress ?? []
      const landed = rows.filter((row) => row.done).length
      return {
        summary: rows.length
          ? `${landed} of ${rows.length} files · ${job.downloader}`
          : `${job.files.length} file${job.files.length === 1 ? '' : 's'} · ${job.downloader}`,
        list: rows.length
          ? rows.map((row) => ({
              label: row.name,
              title: row.name,
              done: row.done,
            }))
          : job.files.map((path) => ({
              label: path.split('/').pop() || path,
              title: path,
              done: true,
            })),
      }
    }
    const rows = job.file_progress ?? []
    const landed = rows.filter((row) => row.done).length
    if (rows.length) {
      return {
        summary:
          landed === rows.length
            ? `${landed} file${landed === 1 ? '' : 's'} · ${job.downloader}`
            : `Stopped after ${landed} of ${rows.length} files · ${job.downloader}`,
        status: job.error || job.status,
        // the whole planned queue: what landed, what is still missing
        list: rows.map((row) => ({
          label: row.name,
          title: row.name,
          done: row.done,
        })),
      }
    }
    return null
  }, [job])

  const retryable = job.status === 'failed' || job.status === 'cancelled'
  const [expanded, setExpanded] = useState(false)
  return (
    <div className="flex items-start gap-2 rounded-md border border-border px-2.5 py-2">
      <span className={cn('mt-0.5 shrink-0', STATUS_STYLES[job.status])}>
        {job.status === 'completed' ? (
          <CheckCircle2 className="size-4" />
        ) : job.status === 'cancelled' ? (
          <X className="size-4" />
        ) : (
          <XCircle className="size-4" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-fg">{job.title || job.url}</p>
        {meta ? (
          <>
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              className="flex items-center gap-1 py-0.5 text-[11px] text-fg-muted hover:text-fg"
              aria-expanded={expanded}
            >
              <ChevronDown
                aria-hidden="true"
                className={cn('size-3 shrink-0 transition-transform', expanded && 'rotate-180')}
              />
              {meta.summary}
            </button>
            {expanded && meta.list.length ? (
              <ul className="flex max-h-44 flex-col gap-0.5 overflow-y-auto rounded-md bg-surface-2/60 px-2 py-1.5">
                {meta.list.map((row) => (
                  <li
                    key={row.title}
                    className="flex items-center gap-2 text-[11px]"
                  >
                    {row.done ? (
                      <CheckCircle2
                        aria-hidden="true"
                        className="size-3 shrink-0 text-success"
                      />
                    ) : (
                      <Clock
                        aria-hidden="true"
                        className="size-3 shrink-0 text-fg-subtle"
                      />
                    )}
                    <span
                      className="min-w-0 flex-1 truncate font-mono text-fg-muted"
                      title={row.title}
                    >
                      {row.label}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        ) : (
          <p className="truncate text-[11px] text-fg-subtle">
            {job.error || job.status}
          </p>
        )}
        <p className="truncate font-mono text-[10px] text-fg-subtle">{job.destination}</p>
      </div>
      {retryable ? (
        <IconButton
          aria-label="Retry download"
          title="Retry"
          size={28}
          onClick={onRetry}
        >
          <RotateCcw />
        </IconButton>
      ) : null}
    </div>
  )
}
