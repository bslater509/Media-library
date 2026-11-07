import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from 'react'
import * as DropdownMenuPrimitive from '@radix-ui/react-dropdown-menu'
import { cn } from '../../lib/cn'

export const Menu = DropdownMenuPrimitive.Root
export const MenuTrigger = DropdownMenuPrimitive.Trigger
export const MenuGroup = DropdownMenuPrimitive.Group

export interface MenuContentProps
  extends ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Content> {
  /** Force the menu to at least the width of its trigger. */
  matchTrigger?: boolean
}

export const MenuContent = forwardRef<
  ElementRef<typeof DropdownMenuPrimitive.Content>,
  MenuContentProps
>(function MenuContent({ className, align = 'end', sideOffset = 6, matchTrigger = false, ...props }, ref) {
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content
        ref={ref}
        align={align}
        sideOffset={sideOffset}
        data-slot="menu-content"
        className={cn(
          'anim-panel z-50 min-w-44 overflow-hidden rounded-md border border-border bg-surface p-1 shadow-e2',
          'text-fg outline-none',
          matchTrigger && 'min-w-[var(--radix-dropdown-menu-trigger-width)]',
          className,
        )}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  )
})

export interface MenuItemProps
  extends ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Item> {
  variant?: 'default' | 'danger'
}

export const MenuItem = forwardRef<
  ElementRef<typeof DropdownMenuPrimitive.Item>,
  MenuItemProps
>(function MenuItem({ className, variant = 'default', ...props }, ref) {
  return (
    <DropdownMenuPrimitive.Item
      ref={ref}
      data-slot="menu-item"
      className={cn(
        'relative flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-sm select-none',
        'outline-none transition-colors',
        'data-[highlighted]:bg-surface-2 data-[highlighted]:text-fg',
        'data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        '[&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-fg-subtle',
        variant === 'danger'
          ? 'text-danger data-[highlighted]:bg-danger/10 data-[highlighted]:text-danger [&_svg]:text-danger'
          : 'text-fg-muted',
        className,
      )}
      {...props}
    />
  )
})

export const MenuSeparator = forwardRef<
  ElementRef<typeof DropdownMenuPrimitive.Separator>,
  ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Separator>
>(function MenuSeparator({ className, ...props }, ref) {
  return (
    <DropdownMenuPrimitive.Separator
      ref={ref}
      className={cn('-mx-1 my-1 h-px bg-border', className)}
      {...props}
    />
  )
})

export const MenuLabel = forwardRef<
  ElementRef<typeof DropdownMenuPrimitive.Label>,
  ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Label>
>(function MenuLabel({ className, ...props }, ref) {
  return (
    <DropdownMenuPrimitive.Label
      ref={ref}
      className={cn('px-2 py-1.5 text-[11px] font-medium tracking-wide text-fg-subtle uppercase', className)}
      {...props}
    />
  )
})
