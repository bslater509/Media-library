import { useState } from 'react'
import { ImageDown, Loader2, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { useWebpStatus } from '../lib/queries'
import { useQueryClient } from '@tanstack/react-query'
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  Progress,
  Spinner,
} from '../components/ui'

export function ImagesPage() {
  const status = useWebpStatus()
  const client = useQueryClient()
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState('')
  const [runSaved, setRunSaved] = useState(0)
  const [runConverted, setRunConverted] = useState(0)

  const optimize = async () => {
    setRunning(true)
    setRunSaved(0)
    setRunConverted(0)
    let saved = 0
    let converted = 0
    try {
      let guard = 0
      while (guard < 500) {
        guard += 1
        const result = await api.webpOptimize(20)
        saved += result.saved_bytes
        converted += result.converted
        setRunSaved(saved)
        setRunConverted(converted)
        setProgress(
          `${result.converted} converted · ${result.remaining} left`,
        )
        if (result.remaining === 0) break
        if (result.converted + result.skipped + result.failed === 0) break
      }
      await client.invalidateQueries({ queryKey: ['webp'] })
      await client.invalidateQueries({ queryKey: ['browse'] })
      await client.invalidateQueries({ queryKey: ['info'] })
      toast.success(
        `${converted} image${converted === 1 ? '' : 's'} converted`,
      )
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Conversion failed')
    } finally {
      setRunning(false)
      setProgress('')
    }
  }

  const data = status.data
  const pending = data?.pending ?? 0
  const total = pending + (data?.converted ?? 0)
  const percent = total ? Math.round(((data?.converted ?? 0) / total) * 100) : 0

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-4 sm:px-5">
      <div>
        <h1 className="flex items-center gap-2 text-base font-semibold tracking-tight">
          <ImageDown aria-hidden="true" className="size-4 text-fg-subtle" />
          WebP optimizer
        </h1>
        <p className="text-xs text-fg-muted">
          Converts loose JPEG/PNG images to WebP, atomically replaces the
          original, and moves the cached poster with it. Images inside ZIP
          archives stay with the current app.
        </p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle>Progress</CardTitle>
            {data ? (
              <Badge variant={pending ? 'accent' : 'success'}>
                {pending.toLocaleString()} pending
              </Badge>
            ) : null}
          </div>
        </CardHeader>
        <CardBody className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <Stat label="Saved" value={data?.saved_formatted ?? '—'} />
            <Stat label="Converted" value={(data?.converted ?? 0).toLocaleString()} />
            <Stat label="Skipped" value={(data?.skipped ?? 0).toLocaleString()} />
            <Stat label="Quality" value={`${data?.quality ?? 85}`} />
          </div>
          <Progress value={percent} />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-[11px] text-fg-subtle">
              {running ? progress || 'Starting…' : `${percent}% of known images are WebP`}
              {runConverted ? ` · ${runConverted} this run` : ''}
              {runSaved ? ` · ${(runSaved / 1024).toFixed(0)} KB saved` : ''}
            </p>
            <Button onClick={() => void optimize()} disabled={running || pending === 0}>
              {running ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Sparkles />
              )}
              {pending ? `Optimize ${pending.toLocaleString()}` : 'Nothing to optimize'}
            </Button>
          </div>
        </CardBody>
      </Card>

      {status.isLoading ? (
        <div className="flex items-center gap-2 py-6 text-sm text-fg-subtle">
          <Spinner /> Reading the index…
        </div>
      ) : pending === 0 ? (
        <EmptyState
          icon={<Sparkles />}
          title="Every loose image is already WebP"
          description="Archive members are optimised by the current app when you browse them."
        />
      ) : (
        <p className="text-xs leading-5 text-fg-subtle">
          Files are processed in batches of 20, off the request thread. A file
          whose WebP would be larger is skipped when <code>WEBP_SKIP_LARGER</code>{' '}
          is enabled; otherwise it is converted anyway.
        </p>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-24 rounded-md border border-border bg-surface-2 px-3 py-2">
      <p className="text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
        {label}
      </p>
      <p className="font-mono text-sm font-semibold tabular-nums">{value}</p>
    </div>
  )
}
