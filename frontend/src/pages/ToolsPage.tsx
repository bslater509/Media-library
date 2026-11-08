import type { ReactNode } from 'react'
import { Link } from 'react-router'
import {
  Archive,
  Clapperboard,
  Copy,
  DatabaseBackup,
  FolderX,
  Gauge,
  HardDrive,
  ImageDown,
  Loader2,
  RefreshCw,
  Wrench,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  useAnimatedStatus,
  useDedupeStatus,
  useGenerateAnimatedBatch,
  useIndexStatus,
  useIndexSync,
} from '../lib/queries'
import { formatDuration } from '../lib/format'
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
} from '../components/ui'

interface Tool {
  title: string
  description: string
  icon: ReactNode
  to?: string
  href?: string
  cta: string
  badge?: string
  badgeVariant?: 'accent' | 'outline' | 'warning'
}

interface MetricProps {
  label: string
  value: string
}

function Metric({ label, value }: MetricProps) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] tracking-wide text-fg-subtle uppercase">
        {label}
      </dt>
      <dd className="truncate font-mono text-xs text-fg">{value}</dd>
    </div>
  )
}

function IndexCard() {
  const index = useIndexStatus()
  const sync = useIndexSync()
  const data = index.data
  const crawling = data?.status === 'crawling'
  const done = (data?.scanned_folders ?? 0) + (data?.skipped_folders ?? 0)
  const percent = data?.total_folders
    ? Math.min(100, Math.round((done / data.total_folders) * 100))
    : 0
  const elapsed = formatDuration(data?.elapsed_seconds)

  const start = (full: boolean) =>
    sync.mutate(full, {
      onSuccess: () =>
        toast.success(full ? 'Full rescan started' : 'Scan for changes started'),
      onError: (error) =>
        toast.error(
          error instanceof Error ? error.message : 'Could not start the scan',
        ),
    })

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2">
            <HardDrive aria-hidden="true" className="size-4 text-fg-subtle" />
            Library index
          </CardTitle>
          <Badge
            variant={
              !data
                ? 'outline'
                : data.status === 'error'
                  ? 'danger'
                  : crawling
                    ? 'accent'
                    : 'success'
            }
          >
            {data ? data.status : '—'}
          </Badge>
        </div>
      </CardHeader>
      <CardBody className="flex flex-col gap-3">
        <p className="text-xs leading-5 text-fg-muted">
          Files added, changed or deleted outside the app are found by walking
          the tree and comparing folder mtimes against the index.{' '}
          <span className="font-medium text-fg">Scan for changes</span> skips
          folders whose mtime is unchanged;{' '}
          <span className="font-medium text-fg">Full rescan</span> re-reads every
          folder and is the recovery option when the index looks stale.
        </p>

        {crawling ? (
          <div className="flex flex-col gap-1.5">
            <div className="h-1 w-full overflow-hidden rounded-full bg-surface-2">
              <div
                className="h-full rounded-full bg-accent transition-[width] duration-500"
                style={{ width: `${Math.max(percent, 4)}%` }}
              />
            </div>
            <div className="flex items-center justify-between gap-2 text-[10px] text-fg-subtle">
              <span className="truncate font-mono">
                {data.current_folder || 'reading the library root…'}
              </span>
              <span className="shrink-0 font-mono">
                {elapsed ?? '0:00'} · {percent}%
              </span>
            </div>
          </div>
        ) : null}

        {data ? (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
            <Metric
              label="Folders"
              value={
                data.total_folders
                  ? `${data.scanned_folders.toLocaleString()} scanned · ${data.skipped_folders.toLocaleString()} skipped · ${data.total_folders.toLocaleString()} seen`
                  : `${data.scanned_folders.toLocaleString()} scanned`
              }
            />
            <Metric label="Files seen" value={data.files_seen.toLocaleString()} />
            <Metric
              label="Added / changed / removed"
              value={`${data.files_added.toLocaleString()} / ${data.files_updated.toLocaleString()} / ${data.files_removed.toLocaleString()}`}
            />
            <Metric
              label="Archives rebuilt"
              value={data.archives_indexed.toLocaleString()}
            />
            <Metric
              label={crawling ? 'Elapsed' : 'Last run'}
              value={
                crawling
                  ? (elapsed ?? '0:00')
                  : (formatDuration(data.duration_seconds) ?? '—')
              }
            />
            <Metric
              label="Last sync"
              value={
                data.last_synced
                  ? new Date(data.last_synced * 1000).toLocaleString()
                  : '—'
              }
            />
          </dl>
        ) : (
          <p className="text-xs text-fg-subtle">Reading crawler state…</p>
        )}

        {data?.stale_folders ? (
          <p className="text-[11px] leading-5 text-warning">
            {data.stale_folders.toLocaleString()} indexed folders look missing;
            pruning was aborted to protect against a flaky mount.
          </p>
        ) : null}
        {data?.error ? (
          <p className="text-[11px] leading-5 text-danger">{data.error}</p>
        ) : null}

        {data?.owned ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={crawling || sync.isPending}
              onClick={() => start(false)}
            >
              {sync.isPending && sync.variables === false ? (
                <Loader2 className="animate-spin" />
              ) : (
                <RefreshCw className={crawling ? 'animate-spin' : ''} />
              )}
              Scan for changes
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={crawling || sync.isPending}
              onClick={() => start(true)}
            >
              {sync.isPending && sync.variables === true ? (
                <Loader2 className="animate-spin" />
              ) : (
                <HardDrive />
              )}
              Full rescan
            </Button>
          </div>
        ) : (
          <p className="text-[11px] leading-5 text-fg-subtle">
            The crawler is not running. Set{' '}
            <span className="font-mono">OWN_WORKERS=true</span> to let it scan the
            library in the background.
          </p>
        )}
      </CardBody>
    </Card>
  )
}

export function ToolsPage() {
  const status = useDedupeStatus()
  const animated = useAnimatedStatus()
  const generateAll = useGenerateAnimatedBatch()
  const pendingPreviews = animated.data?.pending ?? 0

  const tools: Tool[] = [
    {
      title: 'Duplicates',
      description:
        'Find identical files by content and near-identical images/videos by visual hash, then keep one and remove the rest.',
      icon: <Copy />,
      to: '/tools/dedupe',
      cta: 'Review duplicates',
      badge: status.data
        ? `${status.data.content.candidate_files.toLocaleString()} candidates`
        : undefined,
      badgeVariant: 'accent',
    },
    {
      title: 'Empty folders',
      description:
        'Prune leaf folders with no media left behind by moves and deletions.',
      icon: <FolderX />,
      to: '/tools/empty',
      cta: 'Review empty folders',
    },
    {
      title: 'WebP optimizer',
      description:
        'Convert loose images to WebP atomically, with a running space-saved total. Files inside archives are excluded.',
      icon: <ImageDown />,
      to: '/tools/images',
      cta: 'Optimize images',
    },
    {
      title: 'ZIP archives',
      description:
        'Unpack archives (one or all, as a batch) and browse them as virtual folders in the Library.',
      icon: <Archive />,
      to: '/tools/zip',
      cta: 'Unpack archives',
    },
    {
      title: 'Library backup',
      description:
        'Snapshot the index and cached posters into the library folder, verify the copy, download it, or restore it with the service stopped.',
      icon: <DatabaseBackup />,
      to: '/tools/backup',
      cta: 'Manage backups',
    },
    {
      title: 'Analytics',
      description:
        'Storage, media mix, quality and reclaimable space, computed from the index alone.',
      icon: <Gauge />,
      to: '/analytics',
      cta: 'Open analytics',
    },
  ]

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 px-3 py-4 sm:px-5">
      <div>
        <h1 className="flex items-center gap-2 text-base font-semibold tracking-tight">
          <Wrench aria-hidden="true" className="size-4 text-fg-subtle" />
          Tools
        </h1>
        <p className="text-xs text-fg-muted">
          Maintenance that changes the library. Everything here runs against the
          shared index; snapshot restore runs with the service stopped.
        </p>
      </div>

      <IndexCard />

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle className="flex items-center gap-2">
              <Clapperboard aria-hidden="true" className="size-4 text-fg-subtle" />
              Animated previews
            </CardTitle>
            <Badge variant={pendingPreviews ? 'accent' : 'success'}>
              {animated.data
                ? `${animated.data.ready.toLocaleString()} / ${animated.data.total.toLocaleString()}`
                : '—'}
            </Badge>
          </div>
        </CardHeader>
        <CardBody className="flex flex-col gap-3">
          <p className="text-xs leading-5 text-fg-muted">
            Looping 480px WebP previews for video tiles, generated on demand from
            the viewer or queued here for every video that does not have one.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              size="sm"
              disabled={!pendingPreviews || generateAll.isPending}
              onClick={() =>
                generateAll.mutate(
                  { all_videos: true },
                  {
                    onSuccess: (result) =>
                      result.added
                        ? toast.success(
                            `${result.added.toLocaleString()} preview${result.added === 1 ? '' : 's'} queued`,
                          )
                        : toast.info('Every video already has a preview'),
                    onError: (error) =>
                      toast.error(
                        error instanceof Error
                          ? error.message
                          : 'Could not queue previews',
                      ),
                  },
                )
              }
            >
              {generateAll.isPending ? (
                <Loader2 className="animate-spin" />
              ) : (
                <Clapperboard />
              )}
              {pendingPreviews
                ? `Generate ${pendingPreviews.toLocaleString()} previews`
                : 'All videos have previews'}
            </Button>
            {animated.data?.active ? (
              <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-fg-subtle">
                working on {animated.data.active}
              </span>
            ) : null}
          </div>
        </CardBody>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2">
        {tools.map((tool) => (
          <Card key={tool.title}>
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="flex items-center gap-2">
                  <span className="text-fg-subtle [&_svg]:size-4">{tool.icon}</span>
                  {tool.title}
                </CardTitle>
                {tool.badge ? (
                  <Badge variant={tool.badgeVariant ?? 'outline'}>{tool.badge}</Badge>
                ) : null}
              </div>
            </CardHeader>
            <CardBody className="flex flex-col gap-3">
              <p className="text-xs leading-5 text-fg-muted">{tool.description}</p>
              {tool.to ? (
                <Link
                  to={tool.to}
                  className="w-fit text-sm font-medium text-accent hover:underline"
                >
                  {tool.cta} →
                </Link>
              ) : (
                <a
                  href={tool.href}
                  className="w-fit text-sm font-medium text-accent hover:underline"
                >
                  {tool.cta} →
                </a>
              )}
            </CardBody>
          </Card>
        ))}
      </div>
    </div>
  )
}
