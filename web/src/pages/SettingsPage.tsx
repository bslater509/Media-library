import type { ReactNode } from 'react'
import { ExternalLink, HardDrive, Info, Sparkles } from 'lucide-react'
import { useInfo } from '../lib/queries'
import { TILE_ROW_HEIGHT, useDensityStore, useOnboardingStore, useThemeStore } from '../lib/stores'
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
  { label: 'Batch ZIP and archive WebP tools', phase: 'later' },
  { label: 'Background workers and archive WebP rewrites', phase: 'later' },
]

export function SettingsPage() {
  const info = useInfo()
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
            v2 preview{info.data ? ` ${info.data.version}` : ''} — a from-scratch rewrite
            running beside the current app. Both read the same library and index.
          </p>
          <div className="flex flex-wrap items-center gap-4">
            <a
              href="/"
              className="inline-flex w-fit items-center gap-1.5 font-medium text-accent hover:underline"
            >
              Open the current app
              <ExternalLink aria-hidden="true" className="size-3.5" />
            </a>
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
