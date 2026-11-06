import { forwardRef, type ButtonHTMLAttributes, type ElementType, type Ref } from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../../lib/cn'

export const buttonVariants = cva(
  [
    'inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap font-medium select-none',
    'transition-colors duration-150',
    'outline-none focus-visible:ring-2 focus-visible:ring-accent-ring',
    'disabled:pointer-events-none disabled:opacity-50',
    '[&_svg]:size-4 [&_svg]:shrink-0',
  ],
  {
    variants: {
      variant: {
        primary: 'bg-accent text-accent-fg hover:bg-accent/90',
        secondary: 'border border-border bg-surface-2 text-fg hover:bg-surface-3',
        ghost: 'bg-transparent text-fg-muted hover:bg-surface-2 hover:text-fg',
        danger: 'bg-danger text-danger-fg hover:bg-danger/90',
        outline: 'border border-border-strong bg-transparent text-fg hover:bg-surface-2',
      },
      size: {
        sm: 'h-8 gap-1.5 rounded-sm px-3 text-[13px]',
        md: 'h-9 rounded-md px-4 text-sm',
        icon: 'size-9 rounded-md',
      },
    },
    defaultVariants: {
      variant: 'primary',
      size: 'md',
    },
  },
)

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  /** Render the child element (e.g. a router link) instead of a <button>. */
  asChild?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, asChild = false, type = 'button', ...props },
  ref,
) {
  const Comp = (asChild ? Slot : 'button') as ElementType

  return (
    <Comp
      ref={ref as Ref<HTMLButtonElement>}
      type={asChild ? undefined : type}
      data-slot="button"
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  )
})
