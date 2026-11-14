import { useEffect, useState } from 'react'
import { Outlet } from 'react-router'
import {
  Keyboard,
  Menu as MenuIcon,
  Monitor,
  Moon,
  Search,
  Sun,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { useOnboardingStore, usePaletteStore, useThemeStore } from '../../lib/stores'
import type { ThemeMode } from '../../lib/theme'
import {
  IconButton,
  Menu,
  MenuContent,
  MenuItem,
  MenuTrigger,
  Sheet,
  SheetBody,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '../ui'
import { OnboardingWizard } from '../onboarding/OnboardingWizard'
import { hasSeenOnboarding } from '../../lib/onboarding'
import { Sidebar, MobileSidebarContent } from './Sidebar'
import { MobileNav } from './MobileNav'
import { CommandPalette } from './CommandPalette'
import { ShortcutsDialog } from './ShortcutsDialog'
import { ActivityPanel } from './ActivityPanel'

export function AppShell() {
  const setPaletteOpen = usePaletteStore((state) => state.setOpen)
  const [navOpen, setNavOpen] = useState(false)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const setOnboardingOpen = useOnboardingStore((state) => state.setOpen)

  useEffect(() => {
    if (!hasSeenOnboarding()) setOnboardingOpen(true)
  }, [setOnboardingOpen])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const typing =
        target?.tagName === 'INPUT' ||
        target?.tagName === 'TEXTAREA' ||
        target?.tagName === 'SELECT' ||
        target?.isContentEditable

      if (event.key.toLowerCase() === 'k' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        setPaletteOpen(true)
        return
      }
      if (event.key === '?' && !typing) {
        event.preventDefault()
        setShortcutsOpen(true)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [setPaletteOpen])

  return (
    <div className="flex min-h-dvh bg-bg text-fg">
      <Sidebar />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b border-border bg-surface px-3 sm:px-4">
          <IconButton
            aria-label="Open navigation"
            title="Navigation"
            className="md:hidden"
            onClick={() => setNavOpen(true)}
          >
            <MenuIcon />
          </IconButton>

          <span className="text-sm font-semibold tracking-tight md:hidden">
            Media Library
          </span>

          <DesktopSearch />

          <div className="ml-auto flex items-center gap-1 sm:gap-2">
            <IconButton
              aria-label="Search the library"
              title="Search (Ctrl/⌘ K)"
              className="md:hidden"
              onClick={() => setPaletteOpen(true)}
            >
              <Search />
            </IconButton>
            <IconButton
              aria-label="Keyboard shortcuts"
              title="Keyboard shortcuts (?)"
              className="hidden sm:inline-flex"
              onClick={() => setShortcutsOpen(true)}
            >
              <Keyboard />
            </IconButton>
            <ThemeMenu />
            <ActivityPanel />
          </div>
        </header>

        <main className="min-w-0 flex-1 pb-20 md:pb-0">
          <Outlet />
        </main>
      </div>

      <MobileNav />
      <CommandPalette />
      <OnboardingWizard />
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />

      <Sheet open={navOpen} onOpenChange={setNavOpen}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>Navigation</SheetTitle>
          </SheetHeader>
          <SheetBody className="flex min-h-0 flex-1 flex-col px-0 pt-0">
            <MobileSidebarContent />
          </SheetBody>
        </SheetContent>
      </Sheet>
    </div>
  )
}

function DesktopSearch() {
  const setOpen = usePaletteStore((state) => state.setOpen)
  return (
    <button
      type="button"
      onClick={() => setOpen(true)}
      className={cn(
        'hidden h-9 w-full max-w-sm items-center gap-2 rounded-md border border-border bg-surface-2 px-3 md:flex',
        'text-left text-sm text-fg-subtle transition-colors hover:border-border-strong hover:text-fg-muted',
        'outline-none focus-visible:ring-2 focus-visible:ring-accent-ring',
      )}
    >
      <Search aria-hidden="true" className="size-4 shrink-0" />
      <span className="flex-1 truncate">Search the library…</span>
      <kbd className="shrink-0 rounded-sm border border-border px-1.5 py-0.5 font-mono text-[10px]">
        ⌘K
      </kbd>
    </button>
  )
}

const THEME_OPTIONS: Array<{ value: ThemeMode; label: string; icon: typeof Sun }> = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
]

function ThemeMenu() {
  const mode = useThemeStore((state) => state.mode)
  const setMode = useThemeStore((state) => state.setMode)
  const current = THEME_OPTIONS.find((option) => option.value === mode) ?? THEME_OPTIONS[2]
  const Icon = current.icon

  return (
    <Menu>
      <MenuTrigger asChild>
        <IconButton aria-label="Appearance" title={`Theme: ${current.label}`}>
          <Icon />
        </IconButton>
      </MenuTrigger>
      <MenuContent>
        {THEME_OPTIONS.map(({ value, label, icon: OptionIcon }) => (
          <MenuItem
            key={value}
            onSelect={() => setMode(value)}
            className={cn('justify-between', value === mode && 'font-medium text-accent')}
          >
            <span className="flex items-center gap-2">
              <OptionIcon aria-hidden="true" className="size-4" />
              {label}
            </span>
            {value === mode ? <span className="text-[10px] uppercase">active</span> : null}
          </MenuItem>
        ))}
      </MenuContent>
    </Menu>
  )
}
