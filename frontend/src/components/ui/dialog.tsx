import { forwardRef, type ComponentPropsWithoutRef, type ElementRef, type HTMLAttributes } from 'react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { cn } from '../../lib/cn'

export const Dialog = DialogPrimitive.Root
export const DialogTrigger = DialogPrimitive.Trigger
export const DialogClose = DialogPrimitive.Close
export const DialogPortal = DialogPrimitive.Portal

export interface DialogContentProps
  extends ComponentPropsWithoutRef<typeof DialogPrimitive.Content> {
  /** Hide the built-in top-right close button (e.g. for a confirm dialog). */
  hideClose?: boolean
}

export const DialogContent = forwardRef<
  ElementRef<typeof DialogPrimitive.Content>,
  DialogContentProps
>(function DialogContent({ className, children, hideClose = false, ...props }, ref) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay
        className={cn(
          'anim-overlay fixed inset-0 z-50 bg-black/50',
          'supports-[backdrop-filter]:bg-black/40 supports-[backdrop-filter]:backdrop-blur-[2px]',
        )}
      />
      <div className="fixed inset-0 z-50 overflow-y-auto">
        <div className="flex min-h-full items-center justify-center p-4">
          <DialogPrimitive.Content
            ref={ref}
            data-slot="dialog-content"
            className={cn(
              'anim-panel relative w-full max-w-lg rounded-lg border border-border bg-surface p-5 text-fg shadow-e2',
              'outline-none',
              className,
            )}
            {...props}
          >
            {children}
            {hideClose ? null : (
              <DialogPrimitive.Close
                aria-label="Close dialog"
                className={cn(
                  'absolute top-3 right-3 inline-flex size-7 items-center justify-center rounded-sm',
                  'text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg',
                  'outline-none focus-visible:ring-2 focus-visible:ring-accent-ring',
                )}
              >
                <X aria-hidden="true" className="size-4" />
              </DialogPrimitive.Close>
            )}
          </DialogPrimitive.Content>
        </div>
      </div>
    </DialogPrimitive.Portal>
  )
})

export function DialogHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex flex-col gap-1.5 pr-8', className)} {...props} />
}

export const DialogTitle = forwardRef<
  ElementRef<typeof DialogPrimitive.Title>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(function DialogTitle({ className, ...props }, ref) {
  return (
    <DialogPrimitive.Title
      ref={ref}
      className={cn('text-base leading-6 font-semibold text-fg', className)}
      {...props}
    />
  )
})

export const DialogDescription = forwardRef<
  ElementRef<typeof DialogPrimitive.Description>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(function DialogDescription({ className, ...props }, ref) {
  return (
    <DialogPrimitive.Description
      ref={ref}
      className={cn('text-sm leading-5 text-fg-muted', className)}
      {...props}
    />
  )
})

export function DialogBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('mt-4 text-sm text-fg-muted', className)} {...props} />
}

export function DialogFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end', className)}
      {...props}
    />
  )
}
