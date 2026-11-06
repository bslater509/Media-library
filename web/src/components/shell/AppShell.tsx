import { useEffect, useState } from 'react'
import { Outlet } from 'react-router'
import {
  ExternalLink,
  Keyboard,
  Menu as MenuIcon,
  Monitor,
  Moon,
  Search,
  Sun,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { usePaletteStore, useThemeStore } from '../../lib/stores'
import type { ThemeMode } from '../../lib/theme'
import {
  Button,
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
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
import { Sidebar, MobileSidebarContent } from './Sidebar'
import { MobileNav } from './MobileNav'
import { CommandPalette } from './CommandPalette'
import { ShortcutsDialog } from './ShortcutsDialog'

export function AppShell() {
  const setPaletteOpen = usePaletteStore((state) => state.setOpen)
  const [navOpen, setNavOpen] = useState(false)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)

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
            <a
              href="/"
              title="Open the current app"
              className="hidden items-center gap-1 rounded-sm px-2 py-1.5 text-xs text-fg-subtle hover:bg-surface-2 hover:text-fg sm:inline-flex"
            >
              Current app
              <ExternalLink aria-hidden="true" className="size-3" />
            </a>
          </div>
        </header>

        <main className="min-w-0 flex-1 pb-20 md:pb-0">
          <Outlet />
        </main>
      </div>

      <MobileNav />
      <CommandPalette />
      <IntroDialog />
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

const INTRO_KEY = 'v2_intro_dismissed'

/** First-run explanation. Honest about what the preview can and cannot do yet. */
function IntroDialog() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    try {
      setOpen(window.localStorage.getItem(INTRO_KEY) !== '1')
    } catch {
      setOpen(true)
    }
  }, [])

  const dismiss = () => {
    try {
      window.localStorage.setItem(INTRO_KEY, '1')
    } catch {
      /* ignore */
    }
    setOpen(false)
  }

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : dismiss())}>
      <DialogContent aria-describedby={undefined} className="max-w-md">
        <DialogHeader>
          <DialogTitle>Welcome to the new Media Library</DialogTitle>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-4">
          <p className="text-sm leading-6 text-fg-muted">
            This is the rebuilt interface, running beside the app you know. It reads
            the same library and index, so nothing needs to be re-imported.
          </p>
          <ul className="flex flex-col gap-2 text-sm text-fg-muted">
            <li className="flex gap-2">
              <span className="text-accent">•</span> Justified gallery, fullscreen
              viewer and byte-range video.
            </li>
            <li className="flex gap-2">
              <span className="text-accent">•</span> Search filenames or folder names,
              and jump anywhere with ⌘K / Ctrl-K.
            </li>
            <li className="flex gap-2">
              <span className="text-accent">•</span> Light, dark or system theme.
            </li>
          </ul>
          <p className="rounded-md border border-border bg-surface-2 px-3 py-2 text-xs leading-5 text-fg-muted">
            Still in the current app for now: uploads, moving and renaming, the sorter,
            downloads, duplicates, ZIP tools and analytics.
          </p>
        </DialogBody>
        <DialogFooter className="flex items-center justify-between gap-2">
          <a href="/" className="text-xs text-fg-subtle hover:text-fg">
            Open the current app
          </a>
          <Button onClick={dismiss}>Start browsing</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
