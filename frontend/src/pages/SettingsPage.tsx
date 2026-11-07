import type { ReactNode } from 'react'
import {
  ExternalLink,
  HardDrive,
  Info,
  LogOut,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import {
  useAuthLogout,
  useAuthSession,
  useIndexStatus,
  useIndexSync,
  useInfo,
} from '../lib/queries'
import { toast } from 'sonner'
import { TILE_ROW_HEIGHT, useDensityStore, useOnboardingStore, useThemeStore } from '../lib/stores'
import { IS_MOUNTED_PREVIEW } from '../lib/base'
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  Segmented,
} from '../components/ui'

const ROADMAP = [
  { label: 'Editing files inside ZIP archives', phase: 'later' },
  { label: 'Background pre-transcoding of non-web containers', phase: 'later' },
]

export function SettingsPage() {
  const info = useInfo()
  const index = useIndexStatus()
  const syncIndex = useIndexSync()
  const session = useAuthSession()
  const signOut = useAuthLogout()
  const theme = useThemeStore((state) => state.mode)
  const setTheme = useThemeStore((state) => state.setMode)
  const scale = useDensityStore((state) => state.scale)
  const setScale = useDensityStore((state) => state.setScale)
  const setOnboardingOpen = useOnboardingStore((state) => state.setOpen)

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-6 sm:px-5">
      <div>
        <h1 className="text-base font-semibold tracking-tight">Settings</h1>
        <p className="text-xs text-fg-muted">Appearance and what this preview reads.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Appearance</CardTitle>
        </CardHeader>
        <CardBody className="flex flex-col gap-4">
          <SettingRow
            label="Theme"
            hint="Follows the operating system by default."
            control={
              <Segmented
                aria-label="Theme"
                size="sm"
                value={theme}
                onChange={setTheme}
                options={[
                  { value: 'light', label: 'Light' },
                  { value: 'dark', label: 'Dark' },
                  { value: 'system', label: 'System' },
                ]}
              />
            }
          />
          <SettingRow
            label="Gallery density"
            hint={`Row height ${TILE_ROW_HEIGHT[scale]} px.`}
            control={
              <Segmented
                aria-label="Gallery density"
                size="sm"
                value={scale}
                onChange={setScale}
                options={[
                  { value: 's', label: 'S' },
                  { value: 'm', label: 'M' },
                  { value: 'l', label: 'L' },
                ]}
              />
            }
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <HardDrive aria-hidden="true" className="size-4 text-fg-subtle" />
            Library
          </CardTitle>
        </CardHeader>
        <CardBody className="flex flex-col gap-3">
          {info.isLoading ? (
            <p className="text-sm text-fg-subtle">Reading the index…</p>
          ) : info.data ? (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Files" value={info.data.counts.files} />
                <Stat label="Videos" value={info.data.counts.videos} />
                <Stat label="Images" value={info.data.counts.images} />
                <Stat label="Folders" value={info.data.counts.folders} />
              </div>
              <div className="rounded-md border border-border bg-surface-2 px-3 py-2">
                <p className="text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
                  Library root
                </p>
                <p className="truncate font-mono text-xs text-fg-muted">
                  {info.data.library_root}
                </p>
              </div>
            </>
          ) : (
            <p className="text-sm text-danger">The index did not answer.</p>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle className="flex items-center gap-2">
              <HardDrive aria-hidden="true" className="size-4 text-fg-subtle" />
              Index
            </CardTitle>
            <Badge variant={index.data?.owned ? 'accent' : 'outline'}>
              {index.data?.owned ? 'Owned crawler' : 'External indexer'}
            </Badge>
          </div>
        </CardHeader>
        <CardBody className="flex flex-col gap-2 text-sm text-fg-muted">
          {index.isLoading || !index.data ? (
            <p className="text-xs text-fg-subtle">Reading crawler state…</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                <span>
                  Status:{' '}
                  <span className="font-medium text-fg">{index.data.status}</span>
                </span>
                <span>
                  Folders:{' '}
                  <span className="font-mono text-fg">
                    {index.data.scanned_folders.toLocaleString()}
                    {index.data.total_folders
                      ? ` / ${index.data.total_folders.toLocaleString()}`
                      : ''}
                  </span>
                </span>
                <span>
                  Skipped:{' '}
                  <span className="font-mono text-fg">
                    {index.data.skipped_folders.toLocaleString()}
                  </span>
                </span>
                <span>
                  Last sync:{' '}
                  <span className="font-mono text-fg">
                    {index.data.last_synced
                      ? new Date(index.data.last_synced * 1000).toLocaleString()
                      : '—'}
                  </span>
                </span>
              </div>
              {index.data.current_folder ? (
                <p className="truncate font-mono text-[10px] text-fg-subtle">
                  {index.data.current_folder}
                </p>
              ) : null}
              {index.data.stale_folders ? (
                <p className="text-[11px] text-warning">
                  {index.data.stale_folders.toLocaleString()} indexed folders look
                  missing; the crawler aborted pruning to protect against a flaky
                  mount.
                </p>
              ) : null}
              {index.data.owned ? (
                <Button
                  variant="secondary"
                  size="sm"
                  className="w-fit"
                  disabled={syncIndex.isPending || index.data.status === 'crawling'}
                  onClick={() =>
                    syncIndex.mutate(undefined, {
                      onSuccess: () => toast.success('Full crawl started'),
                      onError: (error) =>
                        toast.error(
                          error instanceof Error ? error.message : 'Could not start',
                        ),
                    })
                  }
                >
                  <RefreshCw
                    className={index.data.status === 'crawling' ? 'animate-spin' : ''}
                  />
                  Sync now
                </Button>
              ) : (
                <p className="text-[11px] leading-5 text-fg-subtle">
                  v2 is not running the crawler. Set{' '}
                  <span className="font-mono">OWN_WORKERS=true</span> to run the
                  crawler, metadata probes, poster sweep and automatic snapshots.
                </p>
              )}
            </>
          )}
        </CardBody>
      </Card>

      {session.data?.enabled ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck aria-hidden="true" className="size-4 text-fg-subtle" />
              Account
            </CardTitle>
          </CardHeader>
          <CardBody className="flex flex-col gap-2 text-sm text-fg-muted">
            <p>
              This browser is signed in with the shared password. Signing out only
              affects this browser; the password itself is unchanged.
            </p>
            <Button
              variant="secondary"
              size="sm"
              className="w-fit"
              disabled={signOut.isPending}
              onClick={() =>
                signOut.mutate(undefined, {
                  onSuccess: () => toast.success('Signed out'),
                  onError: (error) =>
                    toast.error(
                      error instanceof Error ? error.message : 'Could not sign out',
                    ),
                })
              }
            >
              <LogOut aria-hidden="true" className="size-3.5" />
              Sign out
            </Button>
          </CardBody>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles aria-hidden="true" className="size-4 text-fg-subtle" />
            What is next
          </CardTitle>
        </CardHeader>
        <CardBody className="flex flex-col gap-2">
          {ROADMAP.map((item) => (
            <div
              key={item.label}
              className="flex items-center justify-between gap-2 text-sm text-fg-muted"
            >
              <span>{item.label}</span>
              <Badge variant={item.phase === 'next' ? 'accent' : 'outline'}>
                {item.phase}
              </Badge>
            </div>
          ))}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Info aria-hidden="true" className="size-4 text-fg-subtle" />
            About
          </CardTitle>
        </CardHeader>
        <CardBody className="flex flex-col gap-2 text-sm text-fg-muted">
          <p>
            {IS_MOUNTED_PREVIEW ? 'v2 preview' : 'Media Library'}
            {info.data ? ` ${info.data.version}` : ''} — a from-scratch client and
            API over the same library and index
            {IS_MOUNTED_PREVIEW ? ' as the current app' : ''}.
          </p>
          <div className="flex flex-wrap items-center gap-4">
            {IS_MOUNTED_PREVIEW ? (
              <a
                href="/"
                className="inline-flex w-fit items-center gap-1.5 font-medium text-accent hover:underline"
              >
                Open the current app
                <ExternalLink aria-hidden="true" className="size-3.5" />
              </a>
            ) : null}
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setOnboardingOpen(true)}
            >
              Show welcome tour
            </Button>
          </div>
        </CardBody>
      </Card>
    </div>
  )
}

function SettingRow({
  label,
  hint,
  control,
}: {
  label: string
  hint: string
  control: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div>
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-fg-subtle">{hint}</p>
      </div>
      {control}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-surface-2 px-3 py-2">
      <p className="text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
        {label}
      </p>
      <p className="font-mono text-sm font-medium tabular-nums">
        {value.toLocaleString()}
      </p>
    </div>
  )
}
