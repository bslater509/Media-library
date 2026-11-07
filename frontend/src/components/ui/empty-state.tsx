import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface EmptyStateProps {
  /** A lucide icon element, rendered inside the tinted tile. */
  icon?: ReactNode
  title: ReactNode
  description?: ReactNode
  /** Primary action, e.g. a <Button>. */
  action?: ReactNode
  className?: string
}

/** Centred "nothing here yet" panel for empty lists, grids and searches. */
export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div
      data-slot="empty-state"
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border px-6 py-12 text-center',
        className,
      )}
    >
      {icon ? (
        <div className="flex size-11 items-center justify-center rounded-md bg-surface-2 text-fg-subtle [&_svg]:size-5">
          {icon}
        </div>
      ) : null}

      <div className="flex flex-col gap-1">
        <p className="text-sm font-medium text-fg">{title}</p>
        {description ? (
          <p className="mx-auto max-w-sm text-sm leading-5 text-fg-muted">{description}</p>
        ) : null}
      </div>

      {action ? <div className="mt-1 flex items-center gap-2">{action}</div> : null}
    </div>
  )
}
