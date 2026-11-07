import { cn } from '../../lib/cn'

export type SpinnerSize = 'sm' | 'md' | 'lg'

const sizeClasses: Record<SpinnerSize, string> = {
  sm: 'size-3.5',
  md: 'size-5',
  lg: 'size-7',
}

export interface SpinnerProps {
  size?: SpinnerSize
  className?: string
  /** Announced to assistive tech; keep the default unless the context is clear. */
  label?: string
}

/** Indeterminate loading indicator. Hand-drawn so it carries no icon dependency. */
export function Spinner({ size = 'md', className, label = 'Loading' }: SpinnerProps) {
  return (
    <span role="status" data-slot="spinner" className={cn('inline-flex shrink-0', className)}>
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        fill="none"
        className={cn('animate-spin text-current', sizeClasses[size])}
      >
        <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.5" opacity="0.2" />
        <path
          d="M21 12a9 9 0 0 0-9-9"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
      </svg>
      <span className="sr-only">{label}</span>
    </span>
  )
}
