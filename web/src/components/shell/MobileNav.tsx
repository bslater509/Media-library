import { useState } from 'react'
import { NavLink } from 'react-router'
import { Clapperboard, Ellipsis, ExternalLink, LayoutGrid, Search, Star } from 'lucide-react'
import { cn } from '../../lib/cn'
import { useThemeStore } from '../../lib/stores'
import { Segmented, Sheet, SheetBody, SheetContent, SheetHeader, SheetTitle } from '../ui'
import { MobileSidebarContent } from './Sidebar'

const TABS = [
  { to: '/library', label: 'Library', icon: LayoutGrid },
  { to: '/sorter', label: 'Sorter', icon: Clapperboard },
  { to: '/search', label: 'Search', icon: Search },
  { to: '/favorites', label: 'Favorites', icon: Star },
]

/** Phone bottom navigation: three destinations plus a "More" sheet. */
export function MobileNav() {
  const [moreOpen, setMoreOpen] = useState(false)
  const theme = useThemeStore((state) => state.mode)
  const setTheme = useThemeStore((state) => state.setMode)

  return (
    <>
      <nav
        aria-label="Primary"
        className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-surface pb-[env(safe-area-inset-bottom)] md:hidden"
      >
        <div className="grid grid-cols-5">
          {TABS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  'flex flex-col items-center gap-0.5 py-1.5 text-[10px] font-medium',
                  isActive ? 'text-accent' : 'text-fg-subtle',
                )
              }
            >
              <Icon aria-hidden="true" className="size-5" />
              {label}
            </NavLink>
          ))}
          <button
            type="button"
            onClick={() => setMoreOpen(true)}
            className="flex flex-col items-center gap-0.5 py-1.5 text-[10px] font-medium text-fg-subtle"
            aria-label="More"
          >
            <Ellipsis aria-hidden="true" className="size-5" />
            More
          </button>
        </div>
      </nav>

      <Sheet open={moreOpen} onOpenChange={setMoreOpen}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>More</SheetTitle>
          </SheetHeader>
          <SheetBody className="flex min-h-0 flex-1 flex-col gap-4 px-2 pt-0">
            <MobileSidebarContent />
            <div className="shrink-0 border-t border-border px-2 pt-3">
              <p className="pb-2 text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
                Appearance
              </p>
              <Segmented
                aria-label="Theme"
                value={theme}
                onChange={setTheme}
                options={[
                  { value: 'light', label: 'Light' },
                  { value: 'dark', label: 'Dark' },
                  { value: 'system', label: 'System' },
                ]}
              />
              <a
                href="/"
                className="mt-4 flex items-center justify-between rounded-sm border border-border px-3 py-2.5 text-sm text-fg-muted"
              >
                Open the current app
                <ExternalLink aria-hidden="true" className="size-4" />
              </a>
              <p className="py-3 text-center text-[11px] text-fg-subtle">v2 preview</p>
            </div>
          </SheetBody>
        </SheetContent>
      </Sheet>
    </>
  )
}
