import type { WebMP4Status } from '../../lib/api'
import { videoName, webMP4Failure, webMP4Progress } from '../../lib/web-mp4'

export function WebMP4Progress({ data }: { data: WebMP4Status }) {
  const progress = webMP4Progress(data)
  const running = data.status === 'running'
  const percent = Math.max(0, Math.min(100, Number.isFinite(data.percent) ? data.percent : 0))
  const stages: Record<string, { step: number; title: string; help: string }> = {
    preparing: { step: 1, title: 'Preparing this video', help: 'Checking the source before conversion starts.' },
    converting: { step: 1, title: 'Converting this video', help: 'This percentage is for this video only, not the whole library. Next: verify the new MP4, then replace the original.' },
    verifying: { step: 2, title: 'Verifying the new MP4', help: 'Encoding is finished. The new file is being checked for errors. This is a separate 0–100% step before replacement.' },
    committing: { step: 3, title: 'Replacing the original', help: 'Verification passed. Saving the new MP4 and updating the library.' },
  }
  const stage = stages[data.phase] ?? stages.preparing

  return (
    <div className="flex flex-col gap-3 text-xs text-fg-muted">
      {data.current ? (
        <section aria-label="Current video conversion" className="flex flex-col gap-3 rounded-lg border border-accent/30 bg-surface-2 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-[10px] tracking-wide text-fg-subtle uppercase">Current video · Step {stage.step} of 3</p>
              <h3 className="mt-1 text-sm font-semibold text-fg">{stage.title}</h3>
            </div>
            {progress.showPhaseProgress ? (
              <span className="font-mono text-3xl font-semibold tracking-tight text-accent">{percent.toFixed(1)}%</span>
            ) : null}
          </div>
          <p className="break-words text-sm font-medium text-fg">{videoName(data.current)}</p>
          {progress.showPhaseProgress ? (
            <div role="progressbar" aria-label={stage.title} aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}
              className="h-3 overflow-hidden rounded-full bg-surface-3">
              <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${percent}%` }} />
            </div>
          ) : null}
          <p className="leading-5">{stage.help}</p>
          <details>
            <summary className="cursor-pointer text-fg-subtle">Show full file path</summary>
            <p className="mt-1 break-all font-mono text-[10px]">{data.current}</p>
          </details>
        </section>
      ) : running ? (
        <p className="rounded-md bg-surface-2 p-3">
          {progress.pending ? 'Waiting to start the next conversion. Browsing priority may defer queued videos.' : 'Checking the library for videos that need conversion.'}
        </p>
      ) : null}

      <div className="flex flex-wrap gap-x-5 gap-y-2" aria-live="polite">
        <p><strong className="text-fg">{data.converted.toLocaleString()}</strong> successfully replaced</p>
        <p><strong className="text-fg">{progress.waiting.toLocaleString()}</strong> waiting{data.current ? ' + 1 active' : ''}</p>
        <p><strong className="text-fg">{data.skipped.toLocaleString()}</strong> already web-compatible</p>
      </div>

      <section aria-label="Library compatibility check" className="flex flex-col gap-1.5 rounded-md bg-surface-2 p-3">
        <p className={progress.checkingDone ? 'text-success' : 'font-medium text-fg'}>
          {progress.checkingDone ? 'Library check complete' : 'Library check'} · {progress.checkLabel}
        </p>
        {progress.checkingDone ? (
          <p>Checking is not conversion. {progress.pending ? 'Videos needing replacement are still being processed above.' : 'No replacements are waiting.'}</p>
        ) : (
          <>
            <progress aria-label="Library checking progress" className="h-2 w-full accent-accent" value={progress.checked} max={data.total || 1} />
            <p>{progress.unchecked.toLocaleString()} left to check · {data.checking?.length ?? 0} / {data.checker_concurrency} checkers active</p>
          </>
        )}
      </section>

      {data.persistence_error ? (
        <p role="alert" className="rounded-md border border-danger/30 p-3 text-danger">
          Progress-saving problem: {data.persistence_error}. The batch stops until progress can be saved.
        </p>
      ) : null}

      {data.failed > 0 || data.errors.length > 0 ? (
        <section aria-label="Files needing attention" className="rounded-md border border-warning/30 bg-surface-2 p-3">
          <p className="font-medium text-warning">{data.failed.toLocaleString()} files need attention{running ? ' — batch continues' : ''}</p>
          <p className="mt-1 leading-5">
            {running ? 'These individual file failures do not stop the current conversion. ' : ''}
            Those files were not replaced; their originals were retained.
          </p>
          <details className="mt-2">
            <summary className="cursor-pointer">Show affected files ({data.errors.length} most recent)</summary>
            <ul className="mt-3 flex max-h-72 flex-col gap-3 overflow-auto">
              {data.errors.map((error, index) => {
                const help = webMP4Failure(error.error)
                return (
                  <li key={`${error.path}-${index}`} className="min-w-0">
                    <p className="break-words font-medium text-fg">{videoName(error.path)}</p>
                    <p className="mt-1 font-medium">{help.title}</p>
                    <p className="mt-1 leading-5">{help.explanation}</p>
                    <details className="mt-1">
                      <summary className="cursor-pointer text-fg-subtle">Technical details and path</summary>
                      <p className="mt-1 break-all font-mono text-[10px]">{error.path}</p>
                      <p className="mt-1 break-all whitespace-pre-wrap font-mono text-[10px]">{error.error}</p>
                    </details>
                  </li>
                )
              })}
            </ul>
          </details>
        </section>
      ) : null}
    </div>
  )
}
