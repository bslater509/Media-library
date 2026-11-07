import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import {
  ArrowRight,
  Check,
  Clapperboard,
  Clock,
  FolderOpen,
  HardDrive,
  Keyboard,
  Sparkles,
} from 'lucide-react'
import type { OnboardingInfo } from '../../lib/api'
import { useOnboardingInfo } from '../../lib/queries'
import { useDensityStore, useOnboardingStore, useThemeStore } from '../../lib/stores'
import type { ThemeMode } from '../../lib/theme'
import {
  Badge,
  Button,
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Segmented,
  Skeleton,
} from '../ui'
import { buildCapabilities, markOnboardingSeen } from '../../lib/onboarding'

const STEPS = 4

export function OnboardingWizard() {
  const open = useOnboardingStore((state) => state.open)
  const setOpen = useOnboardingStore((state) => state.setOpen)
  const [step, setStep] = useState(0)
  const info = useOnboardingInfo()
  const navigate = useNavigate()

  const close = (finished = false) => {
    markOnboardingSeen()
    setOpen(false)
    setStep(0)
    if (finished) navigate('/library')
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close(false)
      }}
    >
      <DialogContent aria-describedby={undefined} className="max-w-xl">
        <DialogHeader>
          <div className="flex items-center justify-between gap-2 pr-8">
            <DialogTitle className="flex items-center gap-2">
              <Clapperboard aria-hidden="true" className="size-4 text-accent" />
              {step === 0 ? 'Welcome' : `Step ${step + 1} of ${STEPS}`}
            </DialogTitle>
            <button
              type="button"
              onClick={() => close(false)}
              className="text-[11px] text-fg-subtle hover:text-fg"
            >
              Skip tour
            </button>
          </div>
          <div className="flex gap-1.5 pt-1" aria-hidden="true">
            {Array.from({ length: STEPS }).map((_, index) => (
              <span
                key={index}
                className={
                  index <= step
                    ? 'h-1 flex-1 rounded-full bg-accent'
                    : 'h-1 flex-1 rounded-full bg-surface-3'
                }
              />
            ))}
          </div>
        </DialogHeader>

        <DialogBody className="min-h-72">
          {step === 0 ? <WelcomeStep /> : null}
          {step === 1 ? <LibraryStep info={info.data} loading={info.isLoading} /> : null}
          {step === 2 ? <CapabilitiesStep info={info.data} /> : null}
          {step === 3 ? <AppearanceStep /> : null}
        </DialogBody>

        <DialogFooter className="flex items-center justify-between gap-2">
          <div>
            {step > 0 ? (
              <Button variant="ghost" onClick={() => setStep((value) => value - 1)}>
                Back
              </Button>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            {step < STEPS - 1 ? (
              <Button onClick={() => setStep((value) => value + 1)}>
                Next <ArrowRight />
              </Button>
            ) : (
              <Button onClick={() => close(true)}>
                <Sparkles /> Start browsing
              </Button>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function WelcomeStep() {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex size-11 items-center justify-center rounded-lg bg-accent-soft text-accent">
        <Clapperboard aria-hidden="true" className="size-5" />
      </div>
      <div>
        <h2 className="text-base font-semibold">The library, rebuilt</h2>
        <p className="mt-1 text-sm leading-6 text-fg-muted">
          A from-scratch client and API over the same library directory and index. Everything you had before is here.
        </p>
      </div>
      <ul className="flex flex-col gap-2 text-sm text-fg-muted">
        <li className="flex gap-2">
          <Keyboard aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-accent" />
          <span>
            Keyboard-first: <kbd className="font-mono text-xs">⌘K</kbd> jumps
            anywhere, the sorter runs on 1–6, and the viewer on arrows.
          </span>
        </li>
        <li className="flex gap-2">
          <FolderOpen aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-accent" />
          <span>
            Browse, search, upload, move, rename, delete, sort incoming clips,
            download URLs, find duplicates and tidy archives.
          </span>
        </li>
        <li className="flex gap-2">
          <Sparkles aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-accent" />
          <span>Light, dark or system theme, with a calm neutral palette.</span>
        </li>
      </ul>
    </div>
  )
}

function LibraryStep({
  info,
  loading,
}: {
  info: OnboardingInfo | undefined
  loading: boolean
}) {
  if (loading || !info) {
    return (
      <div className="flex flex-col gap-2">
        {[0, 1, 2, 3, 4].map((row) => (
          <Skeleton key={row} className="h-8" />
        ))}
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-base font-semibold">Your library</h2>
        <p className="mt-1 text-sm leading-6 text-fg-muted">
          Everything below is read from the index — no scan runs for this tour.
        </p>
      </div>
      <div className="flex flex-col gap-1.5">
        <Row label="Library root" value={info.library_root} mono />
        <Row label="Files" value={info.counts.files.toLocaleString()} />
        <Row label="Folders" value={info.counts.folders.toLocaleString()} />
        <Row
          label="Videos / images"
          value={`${info.counts.videos.toLocaleString()} / ${info.counts.images.toLocaleString()}`}
        />
        <Row
          label="Index"
          value={info.index.root_indexed ? 'Library root indexed' : 'Root not indexed'}
          tone={info.index.root_indexed ? 'ok' : 'warn'}
        />
        <Row
          label="Incoming folder"
          value={info.source_available ? info.source_dir : 'Not available'}
          tone={info.source_available ? 'ok' : 'warn'}
          mono
        />
        <Row
          label="Last snapshot"
          value={info.backup.created_at_str ?? 'None yet'}
          tone={info.backup.exists ? 'ok' : 'warn'}
        />
        <Row
          label="Downloaders"
          value={[
            `yt-dlp ${info.tools.ytdlp ? 'ready' : 'missing'}`,
            `gallery-dl ${info.tools.gallery_dl ? 'ready' : 'missing'}`,
          ].join(' · ')}
        />
      </div>
      {!info.source_available ? (
        <p className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-[11px] leading-5 text-fg-muted">
          Set <span className="font-mono">INCOMING_DIR</span> (or{' '}
          <span className="font-mono">SOURCE_DIR</span>) to a folder to enable
          the sorter. Everything else works without it.
        </p>
      ) : null}
    </div>
  )
}

function CapabilitiesStep({ info }: { info: OnboardingInfo | undefined }) {
  const groups = useMemo(() => buildCapabilities(info?.features), [info?.features])
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-base font-semibold">What lives where</h2>
        <p className="mt-1 text-sm leading-6 text-fg-muted">
          Everything below reads and writes the same library and index.
        </p>
      </div>
      <div>
        <p className="pb-1.5 text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
          Available
        </p>
        <ul className="grid gap-1 sm:grid-cols-2">
          {groups.available.map((item) => (
            <li
              key={item.key}
              className="flex items-center gap-1.5 text-xs text-fg-muted"
            >
              <Check aria-hidden="true" className="size-3.5 shrink-0 text-success" />
              {item.label}
            </li>
          ))}
        </ul>
      </div>
      {groups.pending.length ? (
        <div>
          <p className="pb-1.5 text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
            Still in the current app
          </p>
          <ul className="flex flex-col gap-1">
            {groups.pending.map((item) => (
              <li
                key={item.key}
                className="flex items-center gap-1.5 text-xs text-fg-muted"
              >
                <Clock aria-hidden="true" className="size-3.5 shrink-0 text-warning" />
                {item.label}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

function AppearanceStep() {
  const theme = useThemeStore((state) => state.mode)
  const setTheme = useThemeStore((state) => state.setMode)
  const scale = useDensityStore((state) => state.scale)
  const setScale = useDensityStore((state) => state.setScale)
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h2 className="text-base font-semibold">Make it yours</h2>
        <p className="mt-1 text-sm leading-6 text-fg-muted">
          These are saved on this device and can be changed later in Settings.
        </p>
      </div>
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-sm font-medium">Theme</p>
            <p className="text-xs text-fg-subtle">System follows the OS.</p>
          </div>
          <Segmented<ThemeMode>
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
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-sm font-medium">Gallery density</p>
            <p className="text-xs text-fg-subtle">Row height in the media grid.</p>
          </div>
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
        </div>
      </div>
      <p className="flex items-start gap-2 rounded-md border border-border bg-surface-2 px-3 py-2 text-[11px] leading-5 text-fg-subtle">
        <HardDrive aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
        Replay this tour any time from Settings → About.
      </p>
    </div>
  )
}

function Row({
  label,
  value,
  mono = false,
  tone,
}: {
  label: string
  value: string
  mono?: boolean
  tone?: 'ok' | 'warn'
}) {
  return (
    <div className="flex items-center gap-3 border-b border-border py-1.5 text-xs last:border-b-0">
      <span className="w-32 shrink-0 text-fg-subtle">{label}</span>
      <span
        className={
          (mono ? 'truncate font-mono ' : '') +
          'min-w-0 flex-1 text-right ' +
          (tone === 'warn' ? 'text-warning' : 'text-fg-muted')
        }
        title={value}
      >
        {value}
      </span>
      {tone === 'ok' ? (
        <Check aria-hidden="true" className="size-3.5 shrink-0 text-success" />
      ) : tone === 'warn' ? (
        <Badge variant="warning">!</Badge>
      ) : null}
    </div>
  )
}
