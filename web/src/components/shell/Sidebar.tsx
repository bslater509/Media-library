import { Link, NavLink, useSearchParams } from 'react-router'
import {
  Clapperboard,
  Download,
  ExternalLink,
  Folder,
  Gauge,
  LayoutGrid,
  Search,
  Settings,
  Star,
  Wrench,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { useInfo, useRootFolders } from '../../lib/queries'
import { IS_MOUNTED_PREVIEW } from '../../lib/base'

const NAV = [
  { to: '/library', label: 'Library', icon: LayoutGrid },
  { to: '/sorter', label: 'Sorter', icon: Clapperboard },
  { to: '/downloads', label: 'Downloads', icon: Download },
  { to: '/analytics', label: 'Analytics', icon: Gauge },
  { to: '/tools', label: 'Tools', icon: Wrench },
  { to: '/search', label: 'Search', icon: Search },
  { to: '/favorites', label: 'Favorites', icon: Star },
]

export function Sidebar() {
  const info = useInfo()
  const rootFolders = useRootFolders()
  const [params] = useSearchParams()
  const activePath = params.get('path')

  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-surface md:flex">
      <div className="flex h-14 shrink-0 items-center gap-2.5 border-b border-border px-4">
        <span className="flex size-8 items-center justify-center rounded-md bg-accent-soft text-accent">
          <Clapperboard aria-hidden="true" className="size-4" />
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold tracking-tight">Media Library</p>
          <p className="truncate text-[11px] text-fg-subtle">
            {IS_MOUNTED_PREVIEW ? 'v2 preview' : 'Library'}
          </p>
        </div>
      </div>

      <nav className="flex shrink-0 flex-col gap-0.5 p-2" aria-label="Main">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2.5 rounded-sm px-2.5 py-2 text-sm transition-colors',
                'outline-none focus-visible:ring-2 focus-visible:ring-accent-ring',
                isActive
                  ? 'bg-accent-soft font-medium text-accent'
                  : 'text-fg-muted hover:bg-surface-2 hover:text-fg',
              )
            }
          >
            <Icon aria-hidden="true" className="size-4" />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        <p className="px-2.5 pt-3 pb-1.5 text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
          Folders
        </p>
        {rootFolders.isLoading ? (
          <div className="flex flex-col gap-1 px-2">
            {[0, 1, 2, 3, 4].map((row) => (
              <div key={row} className="h-6 animate-pulse rounded-sm bg-surface-2" />
            ))}
          </div>
        ) : null}
        {rootFolders.data?.folders.slice(0, 200).map((folder) => {
          const isActive = activePath === folder.path
          return (
            <Link
              key={folder.path}
              to={`/library?path=${encodeURIComponent(folder.path)}`}
              title={folder.name}
              className={cn(
                'flex items-center gap-2 rounded-sm px-2.5 py-1.5 text-[13px] transition-colors',
                isActive
                  ? 'bg-surface-2 font-medium text-fg'
                  : 'text-fg-muted hover:bg-surface-2 hover:text-fg',
              )}
            >
              <Folder aria-hidden="true" className="size-3.5 shrink-0 opacity-70" />
              <span className="truncate">{folder.name}</span>
              <span className="ml-auto shrink-0 text-[10px] text-fg-subtle tabular-nums">
                {folder.file_count > 0 ? folder.file_count.toLocaleString() : ''}
              </span>
            </Link>
          )
        })}
      </div>

      <div className="shrink-0 border-t border-border p-2">
        <NavLink
          to="/settings"
          className={({ isActive }) =>
            cn(
              'flex items-center gap-2.5 rounded-sm px-2.5 py-2 text-sm transition-colors',
              isActive
                ? 'bg-surface-2 font-medium text-fg'
                : 'text-fg-muted hover:bg-surface-2 hover:text-fg',
            )
          }
        >
          <Settings aria-hidden="true" className="size-4" />
          Settings
        </NavLink>
        <div className="mt-1 flex items-center justify-between gap-2 px-2.5 py-1 text-[11px] text-fg-subtle">
          <span className="tabular-nums">
            {info.data ? `${info.data.counts.files.toLocaleString()} items` : '—'}
          </span>
          {IS_MOUNTED_PREVIEW ? (
            <a
              href="/"
              className="inline-flex items-center gap-1 hover:text-fg"
              title="Open the current app"
            >
              v1 <ExternalLink aria-hidden="true" className="size-3" />
            </a>
          ) : null}
        </div>
      </div>
    </aside>
  )
}

/** Compact folder + nav list for the phone drawer. */
export function MobileSidebarContent() {
  const rootFolders = useRootFolders()
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <nav className="flex flex-col gap-0.5 p-2" aria-label="Main">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 rounded-sm px-3 py-2.5 text-sm',
                isActive ? 'bg-accent-soft font-medium text-accent' : 'text-fg-muted',
              )
            }
          >
            <Icon aria-hidden="true" className="size-4" />
            {label}
          </NavLink>
        ))}
      </nav>
      <p className="px-4 pt-2 pb-1.5 text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
        Folders
      </p>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {rootFolders.data?.folders.map((folder) => (
          <Link
            key={folder.path}
            to={`/library?path=${encodeURIComponent(folder.path)}`}
            className="flex items-center gap-2 rounded-sm px-3 py-2 text-[13px] text-fg-muted hover:bg-surface-2"
          >
            <Folder aria-hidden="true" className="size-3.5 shrink-0 opacity-70" />
            <span className="truncate">{folder.name}</span>
          </Link>
        ))}
      </div>
    </div>
  )
}
