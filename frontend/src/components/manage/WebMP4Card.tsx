import { useState } from 'react'
import { Film, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '../../lib/api'
import { WebMP4Progress } from './WebMP4Progress'
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
        {data && data.status !== 'idle' ? <WebMP4Progress data={data} /> : null}
        <p className="text-xs leading-5 text-fg-muted">
          Check every indexed video and replace incompatible files with H.264,
          8-bit MP4 and AAC stereo audio. Compatible MP4s stay untouched; compatible
          streams are copied without re-encoding. Each new file is fully decoded
          to verify it before replacement. Archives must be unpacked first.
        </p>
        {!running ? <p className="text-xs leading-5 text-warning">
          Replacement is permanent. Re-encoding can reduce quality, use more space,
          and take a long time. Subtitles, attachments and extra tracks other than
          audio are not kept. Back up original media first: library snapshots
          contain only the index and artwork.
        </p> : null}
        {status.error ? <p role="alert" className="text-xs text-danger">Could not refresh conversion status: {status.error.message}</p> : null}
        {data?.failure_log_error ? (
          <p role="alert" className="text-xs text-danger">{data.failure_log_error}. Recent errors remain in the queue state.</p>
        ) : null}
        <p className="text-xs text-fg-muted">
          Failures are retained in a persistent server log across batches and
          restarts, including file paths, stages and probe diagnostics.{' '}
          <a className="text-accent hover:underline" href={api.webMP4FailureLogUrl()} download>
            Download failure log
          </a>
        </p>
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
