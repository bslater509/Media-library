import type { ReactNode } from 'react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { Command } from 'cmdk'
import {
  Clock,
  FolderOpen,
  Image as ImageIcon,
  LayoutGrid,
  Search,
  Settings,
  Sparkles,
  Star,
  Video,
} from 'lucide-react'
import { useFolderSearch, useSearch } from '../../lib/queries'
import { useOnboardingStore, usePaletteStore } from '../../lib/stores'
import { Dialog, DialogContent, DialogTitle } from '../ui'
import { formatDuration, resolutionLabel } from '../../lib/format'

interface PaletteItem {
  id: string
  label: string
  hint?: string
  icon: ReactNode
  run: () => void
}

/** Cmd/Ctrl-K palette: jump to a folder, a file, a page or an action. */
export function CommandPalette() {
  const open = usePaletteStore((state) => state.open)
  const setOpen = usePaletteStore((state) => state.setOpen)
  const navigate = useNavigate()
  const setOnboardingOpen = useOnboardingStore((state) => state.setOpen)
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 180)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    if (!open) {
      setQuery('')
      setDebounced('')
    }
  }, [open])

  const folders = useFolderSearch(debounced)
  const files = useSearch(debounced, { limit: 8 })

  const actions = useMemo<PaletteItem[]>(
    () => [
      {
        id: 'library',
        label: 'Go to Library',
        icon: <LayoutGrid className="size-4" />,
        run: () => navigate('/library'),
      },
      {
        id: 'search',
        label: 'Search the library',
        icon: <Search className="size-4" />,
        run: () => navigate('/search'),
      },
      {
        id: 'favorites',
        label: 'Go to Favorites',
        icon: <Star className="size-4" />,
        run: () => navigate('/favorites'),
      },
      {
        id: 'settings',
        label: 'Settings and appearance',
        icon: <Settings className="size-4" />,
        run: () => navigate('/settings'),
      },
      {
        id: 'onboarding',
        label: 'Show welcome tour',
        icon: <Sparkles className="size-4" />,
        run: () => setOnboardingOpen(true),
      },
    ],
    [navigate, setOnboardingOpen],
  )

  const close = () => setOpen(false)

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        hideClose
        aria-describedby={undefined}
        className="max-w-xl overflow-hidden p-0"
      >
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <Command
          label="Command palette"
          className="flex max-h-[70dvh] flex-col"
          shouldFilter={false}
        >
          <div className="flex items-center gap-2 border-b border-border px-3">
            <Search aria-hidden="true" className="size-4 shrink-0 text-fg-subtle" />
            <Command.Input
              value={query}
              onValueChange={setQuery}
              autoFocus
              placeholder="Jump to a folder, a file, a page…"
              className="h-11 w-full bg-transparent text-sm text-fg outline-none placeholder:text-fg-subtle"
            />
            <kbd className="hidden shrink-0 rounded-sm border border-border px-1.5 py-0.5 font-mono text-[10px] text-fg-subtle sm:block">
              esc
            </kbd>
          </div>

          <Command.List className="min-h-0 flex-1 overflow-y-auto p-2">
            <Command.Empty className="px-2 py-6 text-center text-sm text-fg-muted">
              {debounced.length > 1 ? 'No matches.' : 'Type to search the library.'}
            </Command.Empty>

            {debounced.length < 2 ? (
              <Command.Group
                heading="Actions"
                className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-fg-subtle [&_[cmdk-group-heading]]:uppercase"
              >
                {actions.map((action) => (
                  <PaletteRow key={action.id} item={action} onDone={close} />
                ))}
              </Command.Group>
            ) : null}

            {debounced.length > 1 && folders.data?.results.length ? (
              <Command.Group
                heading="Folders"
                className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-fg-subtle [&_[cmdk-group-heading]]:uppercase"
              >
                {folders.data.results.map((folder) => (
                  <PaletteRow
                    key={folder.path}
                    onDone={close}
                    item={{
                      id: folder.path,
                      label: folder.name,
                      hint: `${folder.file_count.toLocaleString()} files`,
                      icon: <FolderOpen className="size-4" />,
                      run: () =>
                        navigate(`/library?path=${encodeURIComponent(folder.path)}`),
                    }}
                  />
                ))}
              </Command.Group>
            ) : null}

            {debounced.length > 1 && files.data?.results.length ? (
              <Command.Group
                heading="Media"
                className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-fg-subtle [&_[cmdk-group-heading]]:uppercase"
              >
                {files.data.results.map((file) => {
                  const duration = formatDuration(file.duration)
                  const resolution = resolutionLabel(file.width, file.height)
                  return (
                    <PaletteRow
                      key={file.id}
                      onDone={close}
                      item={{
                        id: file.id,
                        label: file.name,
                        hint: [resolution, duration, file.rel_folder]
                          .filter(Boolean)
                          .join(' · '),
                        icon:
                          file.type === 'video' ? (
                            <Video className="size-4" />
                          ) : (
                            <ImageIcon className="size-4" />
                          ),
                        run: () =>
                          navigate(
                            `/library?path=${encodeURIComponent(file.folder_path)}&open=${encodeURIComponent(file.path)}`,
                          ),
                      }}
                    />
                  )
                })}
              </Command.Group>
            ) : null}

            {debounced.length > 1 && files.isFetching ? (
              <div className="flex items-center gap-2 px-2 py-3 text-xs text-fg-subtle">
                <Clock className="size-3.5" /> Searching…
              </div>
            ) : null}
          </Command.List>
        </Command>
      </DialogContent>
    </Dialog>
  )
}

function PaletteRow({ item, onDone }: { item: PaletteItem; onDone: () => void }) {
  return (
    <Command.Item
      value={`${item.label} ${item.hint ?? ''} ${item.id}`}
      onSelect={() => {
        item.run()
        onDone()
      }}
      className="flex cursor-pointer items-center gap-2.5 rounded-sm px-2 py-2 text-sm text-fg select-none data-[selected=true]:bg-surface-2"
    >
      <span className="text-fg-subtle">{item.icon}</span>
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
      {item.hint ? (
        <span className="max-w-[45%] truncate text-[11px] text-fg-subtle">{item.hint}</span>
      ) : null}
    </Command.Item>
  )
}
