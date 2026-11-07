import { forwardRef, type ComponentPropsWithoutRef, type ElementRef, type HTMLAttributes } from 'react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { cn } from '../../lib/cn'

export const Sheet = DialogPrimitive.Root
export const SheetTrigger = DialogPrimitive.Trigger
export const SheetClose = DialogPrimitive.Close
export const SheetPortal = DialogPrimitive.Portal

export interface SheetContentProps
  extends ComponentPropsWithoutRef<typeof DialogPrimitive.Content> {
  hideClose?: boolean
}

/**
 * Same Radix dialog as <DialogContent>, presented as a bottom sheet below the
 * md breakpoint (768px) and as a regular centred panel above it. The grabber
 * is a visual affordance only; dismiss with Escape, the backdrop or the X.
 */
export const SheetContent = forwardRef<
  ElementRef<typeof DialogPrimitive.Content>,
  SheetContentProps
>(function SheetContent({ className, children, hideClose = false, ...props }, ref) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="anim-overlay fixed inset-0 z-50 bg-black/50" />
      <div className="pointer-events-none fixed inset-0 z-50 flex items-end justify-center md:items-center md:p-4">
        <DialogPrimitive.Content
          ref={ref}
          data-slot="sheet-content"
          className={cn(
            'anim-sheet pointer-events-auto flex h-[100dvh] w-full flex-col overflow-hidden outline-none',
            'rounded-t-lg border-t border-border bg-surface text-fg shadow-e2',
            'md:h-auto md:max-h-[85dvh] md:max-w-lg md:rounded-lg md:border',
            className,
          )}
          {...props}
        >
          <div
            aria-hidden="true"
            data-sheet-grabber=""
            className="mx-auto mt-2.5 h-1.5 w-10 shrink-0 rounded-full bg-border-strong md:hidden"
          />
          {children}
          {hideClose ? null : (
            <DialogPrimitive.Close
              aria-label="Close sheet"
              className={cn(
                'absolute top-3.5 right-3.5 inline-flex size-7 items-center justify-center rounded-sm',
                'text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg',
                'outline-none focus-visible:ring-2 focus-visible:ring-accent-ring',
              )}
            >
              <X aria-hidden="true" className="size-4" />
            </DialogPrimitive.Close>
          )}
        </DialogPrimitive.Content>
      </div>
    </DialogPrimitive.Portal>
  )
})

export function SheetHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'flex shrink-0 flex-col gap-1.5 border-b border-border px-5 pt-3 pb-4 pr-12',
        'md:pt-5',
        className,
      )}
      {...props}
    />
  )
}

export const SheetTitle = forwardRef<
  ElementRef<typeof DialogPrimitive.Title>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(function SheetTitle({ className, ...props }, ref) {
  return (
    <DialogPrimitive.Title
      ref={ref}
      className={cn('text-base leading-6 font-semibold text-fg', className)}
      {...props}
    />
  )
})

export const SheetDescription = forwardRef<
  ElementRef<typeof DialogPrimitive.Description>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(function SheetDescription({ className, ...props }, ref) {
  return (
    <DialogPrimitive.Description
      ref={ref}
      className={cn('text-sm leading-5 text-fg-muted', className)}
      {...props}
    />
  )
})

export function SheetBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('scrollbar-thin min-h-0 flex-1 overflow-y-auto px-5 py-4', className)}
      {...props}
    />
  )
}

export function SheetFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'flex shrink-0 flex-col-reverse gap-2 border-t border-border px-5 py-4',
        'pb-[max(1rem,calc(var(--sab,0px)+0.75rem))] sm:flex-row sm:justify-end',
        className,
      )}
      {...props}
    />
  )
}
