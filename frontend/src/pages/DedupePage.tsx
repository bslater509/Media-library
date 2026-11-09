import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import {
  AlertTriangle,
  Check,
  Columns2,
  Copy,
  Eye,
  EyeOff,
  Film,
  Hash,
  Loader2,
  RefreshCw,
  ShieldCheck,
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
  useDedupeReset,
  useDedupeResolve,
  useDedupeStatus,
  useDedupeUnignore,
  useDedupeVerifyVisualStatus,
  useStartDedupeVerifyVisual,
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
  Progress,
  Spinner,
  Switch,
} from '../components/ui'
import { defaultKeepPath } from '../lib/dedupe'
import { resolutionLabel, formatBytes } from '../lib/format'

function verificationLabel(verification: DedupeGroup['verification']): string {
  if (!verification || !verification.available) return 'Not frame-checked'
  if (verification.confirmed === true) {
    return `5-frame match · ${verification.confirmed_pairs}/${verification.checked_pairs}`
  }
  if (verification.confirmed === false) {
    return `Frames differ · ${verification.confirmed_pairs}/${verification.checked_pairs}`
  }
  return 'Frame check inconclusive'
}

function verificationVariant(
  verification: DedupeGroup['verification'],
): 'success' | 'warning' | 'outline' {
  if (!verification || !verification.available) return 'outline'
  if (verification.confirmed === true) return 'success'
  if (verification.confirmed === false) return 'warning'
  return 'outline'
}

function playbackUrl(file: FileEntry): string {
  if (file.type !== 'video') return file.media_url
  return file.playable || !file.transcode_url ? file.media_url : file.transcode_url
}

export function DedupePage() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const [limit, setLimit] = useState(25)
  const [hashing, setHashing] = useState(false)
  const [hashingProgress, setHashingProgress] = useState('')
  const [keepChoice, setKeepChoice] = useState<Record<string, string>>({})
  const [confirm, setConfirm] = useState<{ group: DedupeGroup; keep: string } | null>(
    null,
  )
  const [confirmReset, setConfirmReset] = useState(false)
  const [compareId, setCompareId] = useState<string | null>(null)
  const [ignoredLimit, setIgnoredLimit] = useState(50)
  const [showIgnored, setShowIgnored] = useState(false)
  const [showRejected, setShowRejected] = useState(false)
  const [includeImages, setIncludeImages] = useState(false)

  const status = useDedupeStatus(includeImages)
  const groups = useDedupeGroups('all', limit, showRejected, includeImages)
  const ignored = useDedupeIgnored(ignoredLimit)
  const resolve = useDedupeResolve()
  const ignore = useDedupeIgnore()
  const unignore = useDedupeUnignore()
  const verifyStatus = useDedupeVerifyVisualStatus()
  const startChecks = useStartDedupeVerifyVisual()
  const reset = useDedupeReset()

  const data = groups.data
  const unhashed =
    (status.data?.content.unhashed ?? 0) + (status.data?.visual.unhashed ?? 0)
  const compareGroup = data?.groups.find((group) => group.id === compareId) ?? null
  const checkState = verifyStatus.data
  const checking = checkState?.running ?? false
  const checkedGroups = checkState?.completed_groups ?? 0
  const totalCheckGroups = checkState?.total_groups ?? 0
  const activeFiles = checkState?.active_files ?? 0
  const activeIndex = checkState?.active_index ?? 0
  const activeFrame = checkState?.active_frame ?? 0
  const activeFrames = checkState?.active_frames ?? 0
  const activeName = checkState?.active_file
    ? (checkState.active_file.split(/[\\/]/).pop() ?? '')
    : ''
  const activeFileFraction =
    activeFiles > 0
      ? Math.min(
          1,
          (Math.max(0, activeIndex - 1) +
            (activeFrames > 0 ? activeFrame / activeFrames : 0)) /
            activeFiles,
        )
      : 0
  const checkPercent =
    totalCheckGroups > 0
      ? Math.min(
          100,
          Math.round(
            ((checkedGroups + (checking ? activeFileFraction : 0)) /
              totalCheckGroups) *
              100,
          ),
        )
      : 0

  useEffect(() => {
    if (checkState?.status === 'done') {
      void client.invalidateQueries({ queryKey: ['dedupe', 'groups'] })
    }
  }, [checkState?.status, client])

  const openFile = (file: FileEntry) => {
    navigate(
      `/library?path=${encodeURIComponent(file.folder_path)}&open=${encodeURIComponent(file.path)}`,
    )
  }

  const runHashing = async () => {
    setHashing(true)
    try {
      let total = 0
      for (const mode of ['content', 'visual'] as const) {
        let guard = 0
        while (guard < 400) {
          guard += 1
          const result = await api.dedupeHash(mode, 50, includeImages)
          total += result.hashed
          setHashingProgress(
            `${mode}: ${result.hashed} hashed · ${result.remaining} left`,
          )
          if (result.remaining === 0 || result.hashed === 0) break
        }
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
    keepChoice[group.id] ?? defaultKeepPath(group.files)

  const setKeep = (group: DedupeGroup, path: string) =>
    setKeepChoice((current) => ({ ...current, [group.id]: path }))

  const groupIsChecking = (group: DedupeGroup) =>
    Boolean(
      checkState?.current.length &&
        group.files.some((file) => checkState.current.includes(file.path)),
    )

  const verifyGroup = async (group: DedupeGroup) => {
    if (group.match_type !== 'visual') return
    try {
      await startChecks.mutateAsync([group.files.map((file) => file.path)])
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : 'Could not check frames',
      )
    }
  }

  const openCompare = (group: DedupeGroup) => {
    setCompareId(group.id)
    if (
      !checking &&
      group.match_type === 'visual' &&
      !group.verification?.available &&
      group.files.some((file) => file.type === 'video')
    ) {
      void verifyGroup(group)
    }
  }

  const verifyVisible = async () => {
    const pending = (data?.groups ?? []).filter(
      (group) =>
        group.match_type === 'visual' &&
        !group.verification?.available &&
        group.files.some((file) => file.type === 'video'),
    )
    if (!pending.length) {
      toast.info('Every visual group on this page is already frame-checked')
      return
    }
    try {
      await startChecks.mutateAsync(
        pending.map((group) => group.files.map((file) => file.path)),
      )
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : 'Could not check frames',
      )
    }
  }

  const ignoreGroup = async (group: DedupeGroup) => {
    try {
      for (let index = 0; index < group.files.length; index += 1) {
        for (let other = index + 1; other < group.files.length; other += 1) {
          await ignore.mutateAsync({
            path1: group.files[index].path,
            path2: group.files[other].path,
            mode: group.match_type,
          })
        }
      }
      toast.success('Marked as not duplicates')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not ignore the group')
    }
  }

  const runReset = async () => {
    try {
      const result = await reset.mutateAsync()
      setConfirmReset(false)
      setKeepChoice({})
      setCompareId(null)
      toast.success(
        `Cleared ${result.ignored_cleared} ignored pair(s) and ${result.signatures_cleared} frame check(s)`,
      )
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not reset duplicates')
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
            Byte-identical and visually similar files in one list. Visual matches
            can be double-checked against five frames per video.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {checking ||
          data?.groups.some(
            (group) =>
              group.match_type === 'visual' && !group.verification?.available,
          ) ? (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => void verifyVisible()}
              disabled={checking || startChecks.isPending}
              title="Compare five sampled frames for each visual group on this page"
            >
              {checking ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <ShieldCheck />
              )}
              {checking
                ? `Checking ${checkedGroups}/${totalCheckGroups}`
                : 'Re-check 5 frames'}
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="secondary"
            onClick={() => void runHashing()}
            disabled={hashing || unhashed === 0}
            title={
              unhashed === 0
                ? 'Everything is already hashed'
                : `Hash ${unhashed} remaining candidates`
            }
          >
            {hashing ? <Loader2 className="size-4 animate-spin" /> : <Hash />}
            {hashing ? hashingProgress || 'Hashing…' : `Hash ${unhashed || ''}`.trim()}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setConfirmReset(true)}
            disabled={reset.isPending}
            title="Clear every ignored pair and cached frame check"
          >
            <RefreshCw /> Reset
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[11px] text-fg-subtle">
        <Badge variant="outline">
          {status.data?.content.candidate_files.toLocaleString() ?? 0} same-size
          candidates
        </Badge>
        <Badge variant="outline">
          {status.data?.visual.hashed.toLocaleString() ?? 0} visual hashes
        </Badge>
        <Badge variant={unhashed ? 'warning' : 'success'}>
          {unhashed ? `${unhashed.toLocaleString()} not hashed` : 'all hashed'}
        </Badge>
        <Badge variant="outline">
          {status.data?.ignored.toLocaleString() ?? 0} ignored pairs
        </Badge>
        <label
          htmlFor="dedupe-include-images"
          className="flex cursor-pointer items-center gap-1.5"
          title="Show image files in duplicate groups"
        >
          <Switch
            id="dedupe-include-images"
            checked={includeImages}
            onCheckedChange={setIncludeImages}
            aria-label="Show images"
          />
          Show images
        </label>
        {data ? (
          <span className="tabular-nums">
            {data.total_groups.toLocaleString()} groups ·{' '}
            {data.total_files.toLocaleString()} files
          </span>
        ) : null}
        {data?.rejected_groups ? (
          <button
            type="button"
            onClick={() => setShowRejected((value) => !value)}
            className="text-accent hover:underline"
          >
            {showRejected ? 'Hide' : 'Show'}{' '}
            {data.rejected_groups.toLocaleString()} frame mismatch
            {data.rejected_groups === 1 ? '' : 'es'}
          </button>
        ) : null}
      </div>

      {checkState && (checking || checkedGroups > 0) ? (
        <div className="flex flex-col gap-1.5 rounded-md border border-border bg-surface-2 px-3 py-2 text-xs text-fg-muted">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="flex items-center gap-2">
              {checking ? (
                <Loader2 className="size-3.5 animate-spin text-accent" />
              ) : (
                <ShieldCheck className="size-3.5 text-success" />
              )}
              {checking ? (
                <>
                  {totalCheckGroups > 0
                    ? `Auto-checking group ${Math.min(checkedGroups + 1, totalCheckGroups)} of ${totalCheckGroups}`
                    : 'Starting frame check…'}
                  {activeFiles > 0
                    ? ` · file ${Math.min(Math.max(1, activeIndex), activeFiles)}/${activeFiles}`
                    : ''}
                  {activeFiles > 0 && activeFrames > 0
                    ? ` · frame ${activeFrame}/${activeFrames}`
                    : ''}
                </>
              ) : (
                `Last 5-frame check: ${checkedGroups}/${totalCheckGroups} groups`
              )}
            </span>
            <span className="tabular-nums text-fg-subtle">
              {checkState.confirmed} confirmed · {checkState.rejected} differ ·{' '}
              {checkState.unknown} inconclusive
              {checkState.errors ? ` · ${checkState.errors} failed` : ''}
            </span>
          </div>
          {checking && activeName ? (
            <div className="flex items-center justify-between gap-2 text-[10px] text-fg-subtle">
              <span className="min-w-0 truncate font-mono">{activeName}</span>
              <span className="shrink-0">
                {checkState.stage === 'comparing' ? 'comparing frames' : 'extracting frames'}
              </span>
            </div>
          ) : null}
          {checking ? <Progress value={checkPercent} /> : null}
        </div>
      ) : null}

      {unhashed > 0 ? (
        <div className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-fg-muted">
          <AlertTriangle aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-warning" />
          Same-size files need a content hash and posters need a perceptual hash.
          Hashing reads 128 KB per file; the button runs it in batches, off the
          request thread.
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
              : 'Nothing looks duplicated. Nice library.'
          }
        />
      ) : (
        <div className="flex flex-col gap-3">
          {data?.groups.map((group) => {
            const keep = keepFor(group)
            const isVisual = group.match_type === 'visual'
            return (
              <Card key={group.id}>
                <CardHeader>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <CardTitle className="flex items-center gap-2">
                      {isVisual ? (
                        <Eye aria-hidden="true" className="size-4 text-fg-subtle" />
                      ) : (
                        <Copy aria-hidden="true" className="size-4 text-fg-subtle" />
                      )}
                      {group.files.length}{' '}
                      {isVisual ? 'visually similar files' : 'identical files'}
                      <Badge variant="outline">{group.size_formatted} each</Badge>
                      {isVisual ? (
                        <Badge variant={verificationVariant(group.verification)}>
                          {verificationLabel(group.verification)}
                        </Badge>
                      ) : null}
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
                          onClick={() => setKeep(group, file.path)}
                          className={cn(
                            'flex size-4 shrink-0 items-center justify-center rounded-full border',
                            selected
                              ? 'border-accent bg-accent text-accent-fg'
                              : 'border-border-strong text-transparent',
                          )}
                        >
                          <Check className="size-3" />
                        </button>
                        <button
                          type="button"
                          onClick={() => openCompare(group)}
                          title="Compare these files"
                          className="size-10 shrink-0 overflow-hidden rounded-sm bg-surface-3"
                        >
                          {file.has_thumb ? (
                            <img
                              src={file.thumb_url}
                              alt=""
                              loading="lazy"
                              className="h-full w-full object-cover"
                            />
                          ) : null}
                        </button>
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
                    <div className="flex flex-wrap items-center gap-3">
                      <button
                        type="button"
                        onClick={() => openCompare(group)}
                        className="inline-flex items-center gap-1.5 text-[11px] text-fg-subtle hover:text-fg"
                      >
                        <Columns2 className="size-3.5" /> Compare
                      </button>
                      {isVisual ? (
                        <button
                          type="button"
                          onClick={() => void verifyGroup(group)}
                          disabled={
                            checking ||
                            startChecks.isPending ||
                            !group.files.some((file) => file.type === 'video')
                          }
                          title={
                            group.files.some((file) => file.type === 'video')
                              ? 'Compare five sampled frames per video'
                              : 'Five-frame checks need at least one video'
                          }
                          className="inline-flex items-center gap-1.5 text-[11px] text-fg-subtle hover:text-fg disabled:opacity-50"
                        >
                          {groupIsChecking(group) ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            <ShieldCheck className="size-3.5" />
                          )}
                          Check 5 frames
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={() => void ignoreGroup(group)}
                        disabled={ignore.isPending}
                        className="inline-flex items-center gap-1.5 text-[11px] text-fg-subtle hover:text-fg"
                      >
                        <EyeOff className="size-3.5" /> Not duplicates
                      </button>
                    </div>
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
            <div className="flex items-center gap-1">
              {ignored.data?.total ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfirmReset(true)}
                  disabled={reset.isPending}
                >
                  <RefreshCw /> Clear all
                </Button>
              ) : null}
              <IconButton
                aria-label={showIgnored ? 'Hide ignored pairs' : 'Show ignored pairs'}
                title={showIgnored ? 'Hide' : 'Show'}
                onClick={() => setShowIgnored((value) => !value)}
              >
                {showIgnored ? <EyeOff /> : <Eye />}
              </IconButton>
            </div>
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
        open={compareGroup !== null}
        onOpenChange={(open) => (open ? undefined : setCompareId(null))}
      >
        {compareGroup ? (
          <DialogContent
            aria-describedby={undefined}
            className="max-w-6xl max-h-[90dvh] overflow-y-auto"
          >
            <DialogHeader>
              <DialogTitle className="flex flex-wrap items-center gap-2">
                <Columns2 aria-hidden="true" className="size-4 text-fg-subtle" />
                Compare {compareGroup.files.length} files
                {compareGroup.match_type === 'visual' ? (
                  <Badge variant={verificationVariant(compareGroup.verification)}>
                    {verificationLabel(compareGroup.verification)}
                  </Badge>
                ) : null}
              </DialogTitle>
            </DialogHeader>
            <DialogBody className="flex flex-col gap-3">
              <p className="text-xs text-fg-muted">
                Play the clips side by side, then choose the copy to keep. The
                highlighted file is kept; the others are deleted only after
                confirmation.
              </p>
              <div className="grid gap-3 md:grid-cols-2">
                {compareGroup.files.map((file) => {
                  const selected = file.path === keepFor(compareGroup)
                  return (
                    <div
                      key={file.path}
                      className={cn(
                        'flex flex-col gap-2 rounded-md border p-2',
                        selected
                          ? 'border-accent bg-accent-soft/30'
                          : 'border-border',
                      )}
                    >
                      <div className="aspect-video w-full overflow-hidden rounded-sm bg-black">
                        {file.type === 'video' ? (
                          <video
                            key={file.path}
                            controls
                            preload="metadata"
                            playsInline
                            poster={file.has_thumb ? file.thumb_url : undefined}
                            src={playbackUrl(file)}
                            className="h-full w-full object-contain"
                          />
                        ) : (
                          <img
                            src={file.media_url}
                            alt=""
                            loading="lazy"
                            className="h-full w-full object-contain"
                          />
                        )}
                      </div>
                      <div className="flex items-start gap-2">
                        <button
                          type="button"
                          role="radio"
                          aria-checked={selected}
                          aria-label={`Keep ${file.name}`}
                          onClick={() => setKeep(compareGroup, file.path)}
                          className={cn(
                            'mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border',
                            selected
                              ? 'border-accent bg-accent text-accent-fg'
                              : 'border-border-strong text-transparent',
                          )}
                        >
                          <Check className="size-3" />
                        </button>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-medium text-fg">
                            {file.name}
                          </p>
                          <p className="truncate text-[10px] text-fg-subtle">
                            {file.folder_path}
                          </p>
                          <p className="text-[10px] text-fg-subtle">
                            {resolutionLabel(file.width, file.height) ?? 'unprobed'} ·{' '}
                            {file.size_formatted}
                            {file.duration ? ` · ${Math.round(file.duration)}s` : ''}
                          </p>
                        </div>
                        {selected ? <Badge variant="accent">keep</Badge> : null}
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        {file.type === 'video' ? (
                          <Badge variant="outline">
                            <Film /> {file.video_codec ?? 'video'}
                          </Badge>
                        ) : null}
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => openFile(file)}
                        >
                          Open in library
                        </Button>
                      </div>
                    </div>
                  )
                })}
              </div>
              {compareGroup.match_type === 'visual' &&
              compareGroup.verification?.pairs.length ? (
                <div className="flex flex-col gap-1 rounded-sm border border-border bg-surface-2 px-3 py-2 text-[11px] text-fg-muted">
                  <span className="font-medium text-fg">Frame check</span>
                  {compareGroup.verification.pairs.map((pair) => (
                    <span key={pair.path} className="truncate">
                      {pair.matches}/{pair.compared} sampled frames match (
                      {pair.against.split('/').pop()})
                    </span>
                  ))}
                </div>
              ) : null}
            </DialogBody>
            <DialogFooter>
              {compareGroup.match_type === 'visual' ? (
                <Button
                  variant="secondary"
                  onClick={() => void verifyGroup(compareGroup)}
                  disabled={
                    checking ||
                    startChecks.isPending ||
                    !compareGroup.files.some((file) => file.type === 'video')
                  }
                >
                  {groupIsChecking(compareGroup) ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <ShieldCheck />
                  )}
                  Check 5 frames
                </Button>
              ) : null}
              <Button variant="ghost" onClick={() => setCompareId(null)}>
                Close
              </Button>
            </DialogFooter>
          </DialogContent>
        ) : null}
      </Dialog>

      <Dialog
        open={confirm !== null}
        onOpenChange={(open) => (open ? undefined : setConfirm(null))}
      >
        <DialogContent aria-describedby={undefined} className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              Delete {deletePreview.length} duplicate
              {deletePreview.length === 1 ? '' : 's'}?
            </DialogTitle>
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
            {confirm?.group.match_type === 'content' ? (
              <p className="text-[11px] leading-5 text-fg-subtle">
                Files in a content group are trusted from the hash match and are
                deleted immediately — verify a pair explicitly if you want a
                byte comparison first.
              </p>
            ) : (
              <p className="text-[11px] leading-5 text-warning">
                Visual duplicates are not byte-identical. Check the frame
                comparison and play the clips before deleting.
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
                  {
                    keep: confirm.keep,
                    deletes,
                    mode: confirm.group.match_type,
                  },
                  {
                    onSuccess: (result) => {
                      if (result.mismatched.length) {
                        toast.error(
                          `${result.mismatched.length} file(s) no longer exist`,
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

      <Dialog
        open={confirmReset}
        onOpenChange={(open) => (open ? undefined : setConfirmReset(false))}
      >
        <DialogContent aria-describedby={undefined} className="max-w-md">
          <DialogHeader>
            <DialogTitle>Reset duplicates?</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <p className="text-sm text-fg-muted">
              This clears every ignored pair and every cached five-frame check,
              so all duplicate candidates come back. Files and hashes are not
              deleted.
            </p>
          </DialogBody>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmReset(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => void runReset()}
              disabled={reset.isPending}
            >
              {reset.isPending ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw />}
              Reset duplicates
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
