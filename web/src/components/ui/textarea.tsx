import { forwardRef, type TextareaHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement>

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, rows = 4, ...props },
  ref,
) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      data-slot="textarea"
      className={cn(
        'min-h-20 w-full resize-y rounded-md border border-border bg-surface px-3 py-2 text-sm text-fg',
        'placeholder:text-fg-subtle',
        'transition-colors duration-150',
        'hover:border-border-strong',
        'focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent-ring',
        'disabled:cursor-not-allowed disabled:opacity-60',
        'aria-invalid:border-danger aria-invalid:ring-danger/30',
        'scrollbar-thin',
        className,
      )}
      {...props}
    />
  )
})
