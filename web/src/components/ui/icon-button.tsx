import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

export type IconButtonVariant = 'ghost' | 'secondary' | 'outline' | 'danger'
export type IconButtonSize = 28 | 32 | 36

const sizeClasses: Record<IconButtonSize, string> = {
  28: 'size-7 rounded-sm',
  32: 'size-8 rounded-sm',
  36: 'size-9 rounded-md',
}

const variantClasses: Record<IconButtonVariant, string> = {
  ghost: 'text-fg-muted hover:bg-surface-2 hover:text-fg',
  secondary: 'border border-border bg-surface-2 text-fg hover:bg-surface-3',
  outline: 'border border-border-strong bg-transparent text-fg hover:bg-surface-2',
  danger: 'text-danger hover:bg-danger/10',
}

export interface IconButtonProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label' | 'title'> {
  /** Accessible name — required, this button has no visible label. */
  'aria-label': string
  /** Native hover tooltip text — required for pointer discovery. */
  title: string
  variant?: IconButtonVariant
  size?: IconButtonSize
}

/**
 * Square, icon-only button. The caller supplies the icon as children; it is
 * sized by the CSS so any 24x24 lucide icon fits every size.
 */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { className, variant = 'ghost', size = 32, type = 'button', ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      data-slot="icon-button"
      className={cn(
        'inline-flex shrink-0 items-center justify-center transition-colors duration-150',
        'outline-none focus-visible:ring-2 focus-visible:ring-accent-ring',
        'disabled:pointer-events-none disabled:opacity-50',
        '[&_svg]:size-4 [&_svg]:shrink-0',
        sizeClasses[size],
        variantClasses[variant],
        className,
      )}
      {...props}
    />
  )
})
