import type { HTMLAttributes } from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../../lib/cn'

export const badgeVariants = cva(
  [
    'inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[11px] leading-4 font-medium',
    'whitespace-nowrap tabular-nums',
    '[&_svg]:size-3 [&_svg]:shrink-0',
  ],
  {
    variants: {
      variant: {
        neutral: 'border-border bg-surface-2 text-fg-muted',
        accent: 'border-transparent bg-accent-soft text-accent',
        success: 'border-success/25 bg-success/10 text-success',
        warning: 'border-warning/25 bg-warning/10 text-warning',
        danger: 'border-danger/25 bg-danger/10 text-danger',
        outline: 'border-border-strong bg-transparent text-fg-muted',
      },
    },
    defaultVariants: {
      variant: 'neutral',
    },
  },
)

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {
  /** Render the label in uppercase with wider tracking (badges, not tags). */
  uppercase?: boolean
}

export function Badge({ className, variant, uppercase = false, ...props }: BadgeProps) {
  return (
    <span
      data-slot="badge"
      className={cn(
        badgeVariants({ variant }),
        uppercase && 'text-[10px] tracking-[0.06em] uppercase',
        className,
      )}
      {...props}
    />
  )
}
