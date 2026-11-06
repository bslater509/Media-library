import type { HTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

/** Loading placeholder block. Size it with the usual width/height utilities. */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden="true"
      className={cn('animate-pulse rounded-sm bg-surface-3', className)}
      {...props}
    />
  )
}
