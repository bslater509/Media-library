import type { ComponentPropsWithoutRef, ElementRef, ReactNode } from 'react'
import { forwardRef } from 'react'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { cn } from '../../lib/cn'

export interface TooltipProviderProps
  extends ComponentPropsWithoutRef<typeof TooltipPrimitive.Provider> {}

/** Wrap the app once so every tooltip shares one open/close delay. */
export function TooltipProvider({ delayDuration = 300, ...props }: TooltipProviderProps) {
  return <TooltipPrimitive.Provider delayDuration={delayDuration} {...props} />
}

export const TooltipTrigger = TooltipPrimitive.Trigger
export const TooltipPortal = TooltipPrimitive.Portal

export const TooltipContent = forwardRef<
  ElementRef<typeof TooltipPrimitive.Content>,
  ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(function TooltipContent({ className, sideOffset = 6, ...props }, ref) {
  return (
    <TooltipPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      data-slot="tooltip-content"
      className={cn(
        'z-50 max-w-64 rounded-sm border border-border bg-surface-3 px-2 py-1 text-xs leading-4 text-fg shadow-e1',
        'select-none',
        className,
      )}
      {...props}
    />
  )
})

export interface TooltipProps {
  /** Tooltip body; keep it short (a label, not a paragraph). */
  content: ReactNode
  /** The trigger element — must accept a ref (a Button, an IconButton, an <a>). */
  children: ReactNode
  side?: ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>['side']
  align?: ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>['align']
  sideOffset?: number
  delayDuration?: number
  className?: string
  disabled?: boolean
}

/** One-shot tooltip: trigger + content in a single element. */
export function Tooltip({
  content,
  children,
  side = 'top',
  align = 'center',
  sideOffset = 6,
  delayDuration = 300,
  className,
  disabled = false,
}: TooltipProps) {
  if (disabled || content === null || content === undefined || content === '') {
    return <>{children}</>
  }

  return (
    <TooltipPrimitive.Root delayDuration={delayDuration}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipContent side={side} align={align} sideOffset={sideOffset} className={className}>
          {content}
        </TooltipContent>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  )
}
