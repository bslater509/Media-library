import { forwardRef, type InputHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

export type InputProps = InputHTMLAttributes<HTMLInputElement>

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, type = 'text', ...props },
  ref,
) {
  return (
    <input
      ref={ref}
      type={type}
      data-slot="input"
      className={cn(
        'h-9 w-full rounded-md border border-border bg-surface px-3 text-sm text-fg',
        'placeholder:text-fg-subtle',
        'transition-colors duration-150',
        'hover:border-border-strong',
        'focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent-ring',
        'disabled:cursor-not-allowed disabled:opacity-60',
        'aria-invalid:border-danger aria-invalid:ring-danger/30',
        className,
      )}
      {...props}
    />
  )
})
