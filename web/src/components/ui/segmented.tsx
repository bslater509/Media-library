import { useRef, type KeyboardEvent, type ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
  icon?: ReactNode
  disabled?: boolean
  title?: string
}

export interface SegmentedProps<T extends string> {
  value: T
  onChange: (value: T) => void
  options: ReadonlyArray<SegmentedOption<T>>
  size?: 'sm' | 'md'
  disabled?: boolean
  className?: string
  'aria-label'?: string
}

const sizeClasses = {
  sm: 'h-7 px-2.5 text-xs',
  md: 'h-8 px-3 text-[13px]',
} as const

/**
 * Controlled segmented control with radio-group semantics: exactly one option
 * selected, arrow keys move between enabled options.
 */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  size = 'md',
  disabled = false,
  className,
  'aria-label': ariaLabel,
}: SegmentedProps<T>) {
  const buttons = useRef<Array<HTMLButtonElement | null>>([])

  const selectAt = (index: number) => {
    const option = options[index]
    if (!option || option.disabled || disabled) return
    buttons.current[index]?.focus()
    if (option.value !== value) onChange(option.value)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return

    event.preventDefault()
    const total = options.length
    if (total === 0) return

    const step = event.key === 'ArrowRight' ? 1 : -1
    const current = options.findIndex((option) => option.value === value)
    let next = current

    for (let attempt = 0; attempt < total; attempt += 1) {
      next = (next + step + total) % total
      if (!options[next].disabled) break
    }

    if (next !== current) selectAt(next)
  }

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      data-slot="segmented"
      onKeyDown={onKeyDown}
      className={cn(
        'inline-flex items-center gap-0.5 rounded-md border border-border bg-surface-2 p-0.5',
        disabled && 'opacity-60',
        className,
      )}
    >
      {options.map((option, index) => {
        const active = option.value === value
        const optionDisabled = disabled || option.disabled

        return (
          <button
            key={option.value}
            ref={(node) => {
              buttons.current[index] = node
            }}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={option.label ? undefined : option.title}
            title={option.title}
            disabled={optionDisabled}
            tabIndex={active && !optionDisabled ? 0 : -1}
            onClick={() => {
              if (!optionDisabled && !active) onChange(option.value)
            }}
            className={cn(
              'inline-flex items-center justify-center gap-1.5 rounded-sm font-medium whitespace-nowrap',
              'transition-colors duration-150',
              'outline-none focus-visible:ring-2 focus-visible:ring-accent-ring',
              'disabled:pointer-events-none disabled:opacity-50',
              '[&_svg]:size-3.5 [&_svg]:shrink-0',
              sizeClasses[size],
              active
                ? 'bg-surface text-fg shadow-e1'
                : 'text-fg-muted hover:bg-surface-3 hover:text-fg',
            )}
          >
            {option.icon}
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
