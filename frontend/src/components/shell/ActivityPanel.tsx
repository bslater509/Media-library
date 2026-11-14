import { useState } from 'react'
import { Link } from 'react-router'
import { Activity, AlertTriangle, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import type { ActivityAction, ActivityItem } from '../../lib/api'
import { activityGroups } from '../../lib/activity'
import { useActivity, useActivityAction, useActivityPriority } from '../../lib/queries'
import {
  Badge, Button, Progress, Sheet, SheetBody, SheetContent, SheetDescription,
  SheetHeader, SheetTitle,
} from '../ui'

const ACTION_LABELS: Record<ActivityAction, string> = {
  pause: 'Pause', resume: 'Resume', cancel: 'Cancel', retry: 'Retry',
}

export function ActivityPanel() {
  const [open, setOpen] = useState(false)
  const query = useActivity(open)
  const priority = useActivityPriority()
  const action = useActivityAction()
  const data = query.data
  const groups = activityGroups(data?.items ?? [])
  const reportError = (error: Error) => toast.error(error.message)

  const runAction = (item: ActivityItem, control: ActivityAction) => {
    action.mutate({ id: item.id, action: control }, {
      onError: reportError,
      onSuccess: () => toast.success(control === 'cancel' && item.id === 'animated'
        ? 'Queued previews cleared; the current preview can finish'
        : `${ACTION_LABELS[control]} requested`),
    })
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Background activity${data ? `: ${data.active_count} active, ${data.failure_count} failures` : ''}`}
        title="Background activity"
        className="inline-flex h-9 items-center gap-1.5 rounded-md px-2 text-xs text-fg-muted hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-accent-ring"
      >
        {query.isError || data?.failure_count ? <AlertTriangle className="size-4 text-warning" /> : <Activity className="size-4" />}
        <span className="hidden sm:inline">Activity</span>
        {data ? <span className="font-mono tabular-nums">{data.active_count}</span> : null}
        {data?.prioritize_browsing ? <span aria-label="Browsing priority enabled" className="size-1.5 rounded-full bg-accent" /> : null}
      </button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>Background activity</SheetTitle>
            <SheetDescription>
              {data ? `${data.active_count} active · ${data.failure_count} with errors` : 'Loading worker status…'}
            </SheetDescription>
          </SheetHeader>
          <SheetBody className="flex flex-col gap-4">
            <div className="rounded-md border border-border bg-surface-2 p-4">
              <label className="flex cursor-pointer items-center justify-between gap-3 text-sm font-medium">
                Prioritize browsing
                <input
                  type="checkbox"
                  aria-label="Prioritize browsing"
                  className="size-4 accent-accent"
                  checked={data?.prioritize_browsing ?? false}
                  disabled={!data || priority.isPending || query.isError}
                  onChange={(event) => priority.mutate(event.target.checked, { onError: reportError })}
                />
              </label>
              <p className="mt-2 text-xs leading-5 text-fg-muted">
                Finish current items, then defer automatic sweeps and queued jobs.
                Playback and explicit actions stay available. Applies to all devices;
                resets when the server restarts.
              </p>
            </div>
            {query.isError ? (
              <div role="alert" className="text-sm text-warning">
                Could not refresh activity. {data ? 'Showing the last known status.' : ''}
                <Button variant="ghost" size="sm" onClick={() => void query.refetch()}>Try again</Button>
              </div>
            ) : null}
            {query.isPending ? <Loader2 aria-label="Loading activity" className="size-5 animate-spin" /> : null}
            {[
              { label: 'Active', rows: groups.active },
              { label: 'Recent activity', rows: groups.recent },
              { label: 'Idle workers', rows: groups.idle },
            ].map(({ label, rows }) => rows.length ? (
              <section key={label} aria-label={label} className="flex flex-col gap-2">
                <h2 className="text-xs font-semibold uppercase text-fg-subtle">{label}</h2>
                {rows.map((item) => (
                  <div key={item.id} className="rounded-md border border-border p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <Link to={item.href} onClick={() => setOpen(false)} className="text-sm font-medium hover:text-accent hover:underline">
                        {item.label}
                      </Link>
                      <Badge variant={item.error ? 'warning' : 'outline'}>
                        {item.state === 'deferred' ? 'Waiting for browsing' : item.state}
                      </Badge>
                    </div>
                    {item.current ? <p title={item.current} className="mt-2 truncate font-mono text-xs text-fg-muted">{item.current}</p> : null}
                    {item.detail ? <p className="mt-1 text-xs text-fg-subtle">{item.detail}</p> : null}
                    {item.updated_at ? <p className="mt-1 text-[11px] text-fg-subtle">Last result: {new Date(item.updated_at * 1000).toLocaleString()}</p> : null}
                    {item.percent !== null && item.state !== 'idle' ? (
                      <div className="mt-2 flex items-center gap-2">
                        <Progress value={item.percent} className="flex-1" aria-label={`${item.label} progress`} />
                        <span className="text-xs tabular-nums">{Math.round(item.percent)}%</span>
                      </div>
                    ) : null}
                    {item.error ? <p className="mt-2 break-words text-xs text-warning">{item.error}</p> : null}
                    {item.controls.length ? (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {item.controls.map((control) => (
                          <Button
                            key={control} size="sm" variant="secondary"
                            disabled={action.isPending || query.isError}
                            onClick={() => runAction(item, control)}
                          >
                            {item.id === 'animated' && control === 'cancel' ? 'Clear queued previews' : ACTION_LABELS[control]}
                          </Button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ))}
              </section>
            ) : null)}
          </SheetBody>
        </SheetContent>
      </Sheet>
    </>
  )
}
