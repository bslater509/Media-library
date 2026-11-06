import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from 'react'
import * as SwitchPrimitive from '@radix-ui/react-switch'
import { cn } from '../../lib/cn'

export type SwitchProps = ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>

/**
 * Accessible on/off switch (role="switch"). Always give it a name, either with
 * an `aria-label`/`aria-labelledby` or a <label htmlFor> in the consumer.
 */
export const Switch = forwardRef<ElementRef<typeof SwitchPrimitive.Root>, SwitchProps>(
  function Switch({ className, ...props }, ref) {
    return (
      <SwitchPrimitive.Root
        ref={ref}
        data-slot="switch"
        className={cn(
          'peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full',
          'border-2 border-transparent transition-colors duration-150',
          'bg-surface-3 hover:bg-border-strong',
          'data-[state=checked]:bg-accent data-[state=checked]:hover:bg-accent',
          'outline-none focus-visible:ring-2 focus-visible:ring-accent-ring',
          'disabled:cursor-not-allowed disabled:opacity-50',
          className,
        )}
        {...props}
      >
        <SwitchPrimitive.Thumb
          className={cn(
            'pointer-events-none block size-4 rounded-full bg-white shadow-sm',
            'transition-transform duration-150',
            'translate-x-0 data-[state=checked]:translate-x-4',
          )}
        />
      </SwitchPrimitive.Root>
    )
  },
)
