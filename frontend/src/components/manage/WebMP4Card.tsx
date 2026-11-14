import { useState } from 'react'
import { Film, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { webMP4Progress } from '../../lib/web-mp4'
import {
  useWebMP4Cancel,
  useWebMP4Preview,
  useWebMP4Start,
  useWebMP4Status,
  useWebMP4Resume,
} from '../../lib/queries'
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle,
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '../ui'

export function WebMP4Card() {
  const status = useWebMP4Status()
  const preview = useWebMP4Preview()
  const start = useWebMP4Start()
  const cancel = useWebMP4Cancel()
  const resume = useWebMP4Resume()
  const [open, setOpen] = useState(false)
  const data = status.data
  const running = data?.status === 'running'
  const progress = data ? webMP4Progress(data) : null
  const onError = (error: Error) => toast.error(error.message)

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2">
            <Film aria-hidden="true" className="size-4 text-fg-subtle" />
            Convert to web MP4
          </CardTitle>
          <Badge variant={running ? 'accent' : data?.failed ? 'warning' : 'outline'}>
            {data?.status ?? 'Loading…'}
          </Badge>
        </div>
      </CardHeader>
      <CardBody className="flex flex-col gap-3">
        <p className="text-xs leading-5 text-fg-muted">
          Check every indexed video and replace incompatible files with H.264,
          8-bit MP4 and AAC stereo audio. Compatible MP4s stay untouched; compatible
          streams are copied without re-encoding. Each new file is fully decoded
          to verify it before replacement. Archives must be unpacked first.
        </p>
        <p className="text-xs leading-5 text-warning">
          Replacement is permanent. Re-encoding can reduce quality, use more space,
          and take a long time. Subtitles, attachments and extra tracks other than
          audio are not kept. Back up original media first: library snapshots
          contain only the index and artwork.
        </p>
        {data && progress && data.status !== 'idle' ? (
          <div aria-live="polite" className="flex flex-col gap-2 text-xs text-fg-muted">
            <div className="flex flex-col gap-1.5 rounded-md bg-surface-2 p-3">
              <p className="font-medium text-fg">Library check · {progress.checkLabel}</p>
              <progress aria-label="Library checking progress" className="h-2 w-full accent-accent"
                value={progress.checked} max={data.total || 1} />
              <p>{progress.unchecked.toLocaleString()} waiting to be checked · {data.checking?.length ?? 0} / {data.checker_concurrency} checkers active · {data.cached ?? 0} cached checks</p>
              {progress.checkingDone ? (
                <p className="text-success">
                  Checking complete.{progress.pending ? ' Replacements are still running or queued below.' : ''}
                </p>
              ) : null}
            </div>
            <p className="font-medium text-fg">Replacements · {progress.queueLabel}</p>
            <p>{progress.outcomeLabel}</p>
            {data.persistence_error ? (
              <p role="alert" className="text-danger">{data.persistence_error}. Work stops until progress can be saved.</p>
            ) : null}
            {data.current ? (
              <>
                <p className="break-all font-mono text-[10px]">
                  {progress.phase}{progress.showPhaseProgress ? ` · ${Math.round(data.percent)}% of this phase` : '…'} · {data.current}
                </p>
                {progress.showPhaseProgress ? (
                  <progress aria-label="Current replacement phase progress" className="h-2 w-full accent-accent" value={data.percent} max={100} />
                ) : null}
              </>
            ) : null}
            {data.errors.length ? (
              <details>
                <summary className="cursor-pointer text-warning">
                  Recent failures ({data.errors.length}; latest 100 retained)
                </summary>
                <ul className="mt-2 max-h-48 overflow-auto">
                  {data.errors.map((error, index) => (
                    <li key={`${error.path}-${index}`} className="mb-2 break-all">
                      {error.path}: {error.error}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
        ) : null}
        {status.error ? <p className="text-xs text-danger">{status.error.message}</p> : null}
        <p className="text-[11px] text-fg-subtle">
          Four checkers scan independently of the single converter, so a long
          encode does not stop library checking. The replacement percentage is
          for the current file and phase, not the whole library. Browsing priority
          defers new work. Progress is saved locally; running batches resume after
          restart. Cancelled batches can be resumed below. Unchanged files reuse
          cached codec checks. Existing MP4 filename
          conflicts and unreadable files are left untouched.
        </p>
        <div className="flex flex-wrap gap-2">
          {!running && (data?.remaining ?? 0) > 0 ? (
            <Button size="sm" variant="secondary" disabled={resume.isPending || start.isPending}
              onClick={() => resume.mutate(undefined, { onError })}>
              Resume {data?.remaining} remaining videos
            </Button>
          ) : null}
          {running ? (
            <Button size="sm" variant="secondary"
              disabled={cancel.isPending || data.cancel_requested}
              onClick={() => cancel.mutate(undefined, { onError })}>
              {data.cancel_requested ? 'Cancelling after safe boundary…' : 'Cancel batch'}
            </Button>
          ) : (
            <Button size="sm" disabled={preview.isPending || start.isPending || !data}
              onClick={() => preview.mutate(undefined, {
                onSuccess: () => setOpen(true), onError,
              })}>
              {preview.isPending ? <Loader2 className="animate-spin" /> : <Film />}
              Preview library conversion
            </Button>
          )}
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Replace videos with web-compatible MP4s?</DialogTitle>
              <DialogDescription>
                {preview.data?.total ?? 0} indexed loose videos will be checked.
                Compatible MP4s will be skipped. {preview.data?.archives ?? 0} archive
                members are excluded. Actual codecs are probed as each file starts.
              </DialogDescription>
            </DialogHeader>
            <p className="mt-3 text-sm text-warning">
              Originals are removed after verification. Re-encoding is lossy;
              subtitles and attachments are dropped. Keep a separate media backup.
              Temporary output needs free space in the source folder.
              {(data?.remaining ?? 0) > 0 ? ' Starting a new batch replaces the saved queue and resets its counters; use Resume to keep existing progress.' : ''}
            </p>
            <div className="mt-3 max-h-52 overflow-auto text-xs">
              <p className="mb-2 text-fg-subtle">First 50 videos (indexed codec hints):</p>
              {preview.data?.sample.map((item) => (
                <p key={item.path} className="mb-2 break-all font-mono">
                  {item.path} · {item.video_codec ?? '?'} / {item.audio_codec ?? 'unknown / none'}
                </p>
              ))}
            </div>
            <DialogFooter>
              <Button variant="secondary" onClick={() => setOpen(false)}>Keep originals</Button>
              <Button disabled={!preview.data?.total || start.isPending}
                onClick={() => {
                  if (preview.data) start.mutate(preview.data.token, {
                    onSuccess: () => { setOpen(false); toast.success('MP4 check started') },
                    onError,
                  })
                }}>
                {start.isPending ? <Loader2 className="animate-spin" /> : null}
                Confirm replacement
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </CardBody>
    </Card>
  )
}
