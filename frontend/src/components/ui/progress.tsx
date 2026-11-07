import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from 'react'
import * as ProgressPrimitive from '@radix-ui/react-progress'
import { cn } from '../../lib/cn'

export interface ProgressProps
  extends ComponentPropsWithoutRef<typeof ProgressPrimitive.Root> {
  /** 0-100. Pass undefined for an indeterminate-looking empty track. */
  value?: number
}

export const Progress = forwardRef<ElementRef<typeof ProgressPrimitive.Root>, ProgressProps>(
  function Progress({ className, value, ...props }, ref) {
    const clamped = Math.min(100, Math.max(0, value ?? 0))

    return (
      <ProgressPrimitive.Root
        ref={ref}
        value={clamped}
        data-slot="progress"
        className={cn(
          'relative h-1.5 w-full overflow-hidden rounded-full bg-surface-3',
          className,
        )}
        {...props}
      >
        <ProgressPrimitive.Indicator
          className="h-full w-full flex-1 rounded-full bg-accent transition-transform duration-300 ease-out"
          style={{ transform: `translateX(-${100 - clamped}%)` }}
        />
      </ProgressPrimitive.Root>
    )
  },
)
