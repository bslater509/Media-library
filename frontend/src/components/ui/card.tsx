import { forwardRef, type HTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

export const Card = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(function Card(
  { className, ...props },
  ref,
) {
  return (
    <div
      ref={ref}
      data-slot="card"
      className={cn(
        'rounded-lg border border-border bg-surface text-fg shadow-e1',
        className,
      )}
      {...props}
    />
  )
})

export const CardHeader = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  function CardHeader({ className, ...props }, ref) {
    return (
      <div
        ref={ref}
        data-slot="card-header"
        className={cn('flex flex-col gap-1 px-4 pt-4 pb-3', className)}
        {...props}
      />
    )
  },
)

export const CardTitle = forwardRef<HTMLHeadingElement, HTMLAttributes<HTMLHeadingElement>>(
  function CardTitle({ className, ...props }, ref) {
    return (
      <h3
        ref={ref}
        data-slot="card-title"
        className={cn('text-sm leading-5 font-semibold text-fg', className)}
        {...props}
      />
    )
  },
)

export const CardBody = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  function CardBody({ className, ...props }, ref) {
    return (
      <div
        ref={ref}
        data-slot="card-body"
        className={cn('px-4 pt-0 pb-4 text-sm text-fg-muted', className)}
        {...props}
      />
    )
  },
)
