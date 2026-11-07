import { forwardRef, type SelectHTMLAttributes } from 'react'
import { ChevronDown } from 'lucide-react'
import { cn } from '../../lib/cn'

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>

/**
 * Native <select> with the option list left to the platform (mobile picks the
 * OS wheel, which is what you want on touch) and only the closed control styled.
 */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, children, ...props },
  ref,
) {
  return (
    <div className="relative w-full">
      <select
        ref={ref}
        data-slot="select"
        className={cn(
          'h-9 w-full appearance-none rounded-md border border-border bg-surface pl-3 pr-9 text-sm text-fg',
          'transition-colors duration-150',
          'hover:border-border-strong',
          'focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent-ring',
          'disabled:cursor-not-allowed disabled:opacity-60',
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden="true"
        className="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-fg-subtle"
      />
    </div>
  )
})
