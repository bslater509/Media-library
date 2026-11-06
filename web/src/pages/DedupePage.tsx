import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import {
  AlertTriangle,
  Check,
  Copy,
  Eye,
  EyeOff,
  Hash,
  Loader2,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'
import { api, type DedupeGroup, type FileEntry } from '../lib/api'
import { cn } from '../lib/cn'
import {
  useDedupeGroups,
  useDedupeIgnored,
  useDedupeIgnore,
  useDedupeResolve,
  useDedupeStatus,
  useDedupeUnignore,
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
  IconButton,
  Spinner,
} from '../components/ui'
import { resolutionLabel } from '../lib/format'

type Mode = 'content' | 'visual'

function formatBytes(value: number): string {
  if (!value) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unit]}`
}

export function DedupePage() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const [mode, setMode] = useState<Mode>('content')
  const [limit, setLimit] = useState(25)
  const [hashing, setHashing] = useState(false)
  const [hashingProgress, setHashingProgress] = useState('')
  const [keepChoice, setKeepChoice] = useState<Record<string, string>>({})
  const [confirm, setConfirm] = useState<{ group: DedupeGroup; keep: string } | null>(
    null,
  )
  const [ignoredLimit, setIgnoredLimit] = useState(50)
  const [showIgnored, setShowIgnored] = useState(false)

  const status = useDedupeStatus()
  const groups = useDedupeGroups(mode, limit)
  const ignored = useDedupeIgnored(ignoredLimit)
  const resolve = useDedupeResolve()
  const ignore = useDedupeIgnore()
  const unignore = useDedupeUnignore()

  const data = groups.data
  const activeStatus = mode === 'content' ? status.data?.content : status.data?.visual
  const unhashed = activeStatus?.unhashed ?? 0

  const openFile = (file: FileEntry) => {
    navigate(
      `/library?path=${encodeURIComponent(file.folder_path)}&open=${encodeURIComponent(file.path)}`,
    )
  }

  const runHashing = async () => {
    setHashing(true)
    try {
      let guard = 0
      let total = 0
      while (guard < 400) {
        guard += 1
        const result = await api.dedupeHash(mode, 50)
        total += result.hashed
        setHashingProgress(`${result.hashed} hashed · ${result.remaining} left`)
        if (result.remaining === 0 || result.hashed === 0) break
      }
      await client.invalidateQueries({ queryKey: ['dedupe'] })
      toast.success(`Hashed ${total} file${total === 1 ? '' : 's'}`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Hashing failed')
    } finally {
      setHashing(false)
      setHashingProgress('')
    }
  }

  const keepFor = (group: DedupeGroup): string =>
    keepChoice[group.id] ?? group.files[0]?.path ?? ''

  const ignoreGroup = async (group: DedupeGroup) => {
    try {
      for (let index = 0; index < group.files.length; index += 1) {
        for (let other = index + 1; other < group.files.length; other += 1) {
          await ignore.mutateAsync({
            path1: group.files[index].path,
            path2: group.files[other].path,
            mode,
          })
        }
      }
      toast.success('Marked as not duplicates')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not ignore the group')
    }
  }

  const deletePreview = useMemo(() => {
    if (!confirm) return []
    return confirm.group.files.filter((file) => file.path !== confirm.keep)
  }, [confirm])

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 px-3 py-4 sm:px-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="flex items-center gap-2 text-base font-semibold tracking-tight">
            <Copy aria-hidden="true" className="size-4 text-fg-subtle" />
            Duplicates
          </h1>
          <p className="text-xs text-fg-muted">
            Content groups are byte-identical. Visual groups are near-identical
            frames; deleting one of those is your call.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-md border border-border p-0.5">
            {(['content', 'visual'] as Mode[]).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => {
                  setMode(option)
                  setKeepChoice({})
                  setLimit(25)
                }}
                className={cn(
                  'rounded-sm px-2.5 py-1 text-xs font-medium capitalize transition-colors',
                  mode === option
                    ? 'bg-accent-soft text-accent'
                    : 'text-fg-muted hover:text-fg',
                )}
              >
                {option}
              </button>
            ))}
          </div>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => void runHashing()}
            disabled={hashing || unhashed === 0}
            title={
              unhashed === 0
                ? 'Everything in this mode is already hashed'
                : `Hash ${unhashed} remaining candidates`
            }
          >
            {hashing ? <Loader2 className="size-4 animate-spin" /> : <Hash />}
            {hashing ? hashingProgress || 'Hashing…' : `Hash ${unhashed || ''}`.trim()}
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[11px] text-fg-subtle">
        <Badge variant="outline">
          {mode === 'content'
            ? `${status.data?.content.candidate_files.toLocaleString() ?? 0} same-size candidates`
            : `${status.data?.visual.hashed.toLocaleString() ?? 0} visual hashes`}
        </Badge>
        <Badge variant={unhashed ? 'warning' : 'success'}>
          {unhashed ? `${unhashed.toLocaleString()} not hashed` : 'all hashed'}
        </Badge>
        <Badge variant="outline">
          {status.data?.ignored.toLocaleString() ?? 0} ignored pairs
        </Badge>
        {data ? (
          <span className="tabular-nums">
            {data.total_groups.toLocaleString()} groups ·{' '}
            {data.total_files.toLocaleString()} files
          </span>
        ) : null}
      </div>

      {unhashed > 0 ? (
        <div className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-fg-muted">
          <AlertTriangle aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-warning" />
          {mode === 'content'
            ? 'Same-size files must be hashed before duplicates can be grouped. Hashing reads 128 KB per file.'
            : 'Posters are hashed into a perceptual fingerprint; only files with a cached poster can be compared.'}{' '}
          The button above runs it in batches, off the request thread.
        </div>
      ) : null}

      {groups.isLoading ? (
        <div className="flex items-center gap-2 py-10 text-sm text-fg-subtle">
          <Spinner /> Scanning for duplicates…
        </div>
      ) : groups.isError ? (
        <EmptyState
          icon={<AlertTriangle />}
          title="Could not scan for duplicates"
          description={
            groups.error instanceof Error ? groups.error.message : 'The index did not answer.'
          }
        />
      ) : data && data.groups.length === 0 ? (
        <EmptyState
          icon={<Sparkles />}
          title="No duplicates found"
          description={
            unhashed
              ? 'Hash the remaining candidates and check again.'
              : 'Nothing in this mode looks duplicated. Nice library.'
          }
        />
      ) : (
        <div className="flex flex-col gap-3">
          {data?.groups.map((group) => {
            const keep = keepFor(group)
            return (
              <Card key={group.id}>
                <CardHeader>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <CardTitle className="flex items-center gap-2">
                      {group.match_type === 'content' ? (
                        <Copy aria-hidden="true" className="size-4 text-fg-subtle" />
                      ) : (
                        <Eye aria-hidden="true" className="size-4 text-fg-subtle" />
                      )}
                      {group.files.length} {group.match_type === 'content'
                        ? 'identical files'
                        : 'visually similar files'}
                      <Badge variant="outline">{group.size_formatted} each</Badge>
                    </CardTitle>
                    <span className="text-[11px] text-fg-subtle">
                      up to {formatBytes(group.reclaimable)} reclaimable
                    </span>
                  </div>
                </CardHeader>
                <CardBody className="flex flex-col gap-2">
                  {group.files.map((file) => {
                    const selected = file.path === keep
                    return (
                      <div
                        key={file.path}
                        className={cn(
                          'flex items-center gap-2 rounded-md border px-2 py-1.5',
                          selected
                            ? 'border-accent bg-accent-soft/40'
                            : 'border-border',
                        )}
                      >
                        <button
                          type="button"
                          role="radio"
                          aria-checked={selected}
                          aria-label={`Keep ${file.name}`}
                          onClick={() =>
                            setKeepChoice((current) => ({
                              ...current,
                              [group.id]: file.path,
                            }))
                          }
                          className={cn(
                            'flex size-4 shrink-0 items-center justify-center rounded-full border',
                            selected
                              ? 'border-accent bg-accent text-accent-fg'
                              : 'border-border-strong text-transparent',
                          )}
                        >
                          <Check className="size-3" />
                        </button>
                        <span className="size-10 shrink-0 overflow-hidden rounded-sm bg-surface-3">
                          {file.has_thumb ? (
                            <img
                              src={file.thumb_url}
                              alt=""
                              loading="lazy"
                              className="h-full w-full object-cover"
                            />
                          ) : null}
                        </span>
                        <button
                          type="button"
                          onClick={() => openFile(file)}
                          className="min-w-0 flex-1 text-left"
                        >
                          <span className="block truncate text-xs text-fg hover:text-accent">
                            {file.name}
                          </span>
                          <span className="block truncate text-[10px] text-fg-subtle">
                            {file.folder_path} ·{' '}
                            {resolutionLabel(file.width, file.height) ?? 'unprobed'}
                            {file.duration ? ` · ${Math.round(file.duration)}s` : ''}
                          </span>
                        </button>
                        <span className="shrink-0 font-mono text-[10px] text-fg-subtle tabular-nums">
                          {file.size_formatted}
                        </span>
                        {selected ? <Badge variant="accent">keep</Badge> : null}
                      </div>
                    )
                  })}
                  <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                    <button
                      type="button"
                      onClick={() => void ignoreGroup(group)}
                      disabled={ignore.isPending}
                      className="inline-flex items-center gap-1.5 text-[11px] text-fg-subtle hover:text-fg"
                    >
                      <EyeOff className="size-3.5" /> Not duplicates
                    </button>
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={() => setConfirm({ group, keep })}
                    >
                      <Trash2 /> Delete {group.files.length - 1}
                    </Button>
                  </div>
                </CardBody>
              </Card>
            )
          })}

          {data && data.total_groups > data.groups.length ? (
            <Button
              variant="ghost"
              size="sm"
              className="self-center"
              onClick={() => setLimit((value) => Math.min(100, value + 25))}
            >
              Load more groups
            </Button>
          ) : null}
        </div>
      )}

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle className="flex items-center gap-2">
              <EyeOff aria-hidden="true" className="size-4 text-fg-subtle" />
              Ignored pairs
              <Badge variant="outline">
                {ignored.data?.total.toLocaleString() ?? 0}
              </Badge>
            </CardTitle>
            <IconButton
              aria-label={showIgnored ? 'Hide ignored pairs' : 'Show ignored pairs'}
              title={showIgnored ? 'Hide' : 'Show'}
              onClick={() => setShowIgnored((value) => !value)}
            >
              {showIgnored ? <EyeOff /> : <Eye />}
            </IconButton>
          </div>
        </CardHeader>
        {showIgnored ? (
          <CardBody className="flex flex-col gap-2">
            {ignored.isLoading ? (
              <Spinner size="sm" />
            ) : ignored.data?.items.length ? (
              <>
                {ignored.data.items.map((pair) => (
                  <div
                    key={pair.id}
                    className="flex items-center gap-2 rounded-sm border border-border px-2 py-1.5 text-[11px]"
                  >
                    <Badge variant={pair.match_type === 'content' ? 'neutral' : 'outline'}>
                      {pair.match_type}
                    </Badge>
                    <span className="min-w-0 flex-1 truncate font-mono text-fg-subtle">
                      {pair.path1}
                    </span>
                    <span className="shrink-0 text-fg-subtle">↔</span>
                    <span className="min-w-0 flex-1 truncate font-mono text-fg-subtle">
                      {pair.path2}
                    </span>
                    <IconButton
                      aria-label="Unignore this pair"
                      title="Unignore"
                      size={28}
                      onClick={() =>
                        unignore.mutate(
                          { path1: pair.path1, path2: pair.path2 },
                          {
                            onSuccess: () => toast.success('Pair restored'),
                            onError: (error) =>
                              toast.error(
                                error instanceof Error
                                  ? error.message
                                  : 'Could not restore the pair',
                              ),
                          },
                        )
                      }
                    >
                      <X />
                    </IconButton>
                  </div>
                ))}
                {ignored.data.total > ignored.data.items.length ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="self-center"
                    onClick={() =>
                      setIgnoredLimit((value) => Math.min(200, value + 50))
                    }
                  >
                    Load more ignored pairs
                  </Button>
                ) : null}
              </>
            ) : (
              <p className="py-2 text-center text-xs text-fg-subtle">
                Nothing ignored. Pairs you mark as “not duplicates” appear here.
              </p>
            )}
          </CardBody>
        ) : null}
      </Card>

      <Dialog
        open={confirm !== null}
        onOpenChange={(open) => (open ? undefined : setConfirm(null))}
      >
        <DialogContent aria-describedby={undefined} className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Delete {deletePreview.length} duplicate{deletePreview.length === 1 ? '' : 's'}?</DialogTitle>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-2">
            <p className="text-sm text-fg-muted">
              Keeping{' '}
              <span className="font-medium text-fg">
                {confirm?.group.files.find((file) => file.path === confirm.keep)?.name}
              </span>
              . These files are removed from disk and the index:
            </p>
            <ul className="max-h-44 overflow-y-auto rounded-sm border border-border bg-surface-2 px-3 py-2 text-[11px] text-fg-muted">
              {deletePreview.map((file) => (
                <li key={file.path} className="truncate py-0.5 font-mono">
                  {file.path}
                </li>
              ))}
            </ul>
            {mode === 'content' ? (
              <p className="text-[11px] leading-5 text-fg-subtle">
                Each file is compared byte-for-byte with the keeper first. Anything
                that differs is left untouched.
              </p>
            ) : (
              <p className="text-[11px] leading-5 text-warning">
                Visual duplicates are not byte-identical. Check the previews before
                deleting.
              </p>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              disabled={resolve.isPending}
              onClick={() => {
                if (!confirm) return
                const deletes = deletePreview.map((file) => file.path)
                resolve.mutate(
                  { keep: confirm.keep, deletes, mode },
                  {
                    onSuccess: (result) => {
                      if (result.mismatched.length) {
                        toast.error(
                          `${result.mismatched.length} file(s) differed and were kept`,
                        )
                      }
                      toast.success(
                        `${result.deleted.length} duplicate${result.deleted.length === 1 ? '' : 's'} deleted`,
                      )
                      setConfirm(null)
                    },
                    onError: (error) =>
                      toast.error(
                        error instanceof Error ? error.message : 'Delete failed',
                      ),
                  },
                )
              }}
            >
              {resolve.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Trash2 />
              )}
              Delete duplicates
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
