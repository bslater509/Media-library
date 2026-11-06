import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import { AlertTriangle, Search as SearchIcon } from 'lucide-react'
import type { SearchFileEntry } from '../lib/api'
import { useSearch } from '../lib/queries'
import { TILE_ROW_HEIGHT, useDensityStore } from '../lib/stores'
import {
  Badge,
  EmptyState,
  Input,
  Segmented,
  Select,
  Spinner,
} from '../components/ui'
import { JustifiedGrid } from '../components/media/JustifiedGrid'
import { MediaLightbox } from '../components/media/MediaLightbox'

type TypeFilter = 'all' | 'video' | 'image'

export function SearchPage() {
  const [params, setParams] = useSearchParams()
  const query = params.get('q') ?? ''
  const type = (params.get('type') as TypeFilter | null) ?? 'all'
  const sort = params.get('sort') ?? 'name_asc'

  const [draft, setDraft] = useState(query)
  const scale = useDensityStore((state) => state.scale)
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)

  useEffect(() => {
    setDraft(query)
  }, [query])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (draft === query) return
      const next = new URLSearchParams(params)
      if (draft.trim()) next.set('q', draft.trim())
      else next.delete('q')
      setParams(next, { replace: true })
    }, 280)
    return () => window.clearTimeout(timer)
    // Only re-run when the draft changes; params are read fresh inside.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft])

  const result = useSearch(query, { type, sort, limit: 200 })
  const files = result.data?.results ?? []

  const setFilter = (nextType: TypeFilter) => {
    const next = new URLSearchParams(params)
    if (nextType === 'all') next.delete('type')
    else next.set('type', nextType)
    setParams(next)
  }

  const setSort = (nextSort: string) => {
    const next = new URLSearchParams(params)
    if (nextSort === 'name_asc') next.delete('sort')
    else next.set('sort', nextSort)
    setParams(next)
  }

  return (
    <div className="flex flex-col gap-4 px-3 py-4 sm:px-5">
      <div className="flex flex-col gap-3">
        <div className="relative">
          <SearchIcon
            aria-hidden="true"
            className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-fg-subtle"
          />
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Search filenames and folder names…"
            aria-label="Search the library"
            autoFocus
            className="h-10 pl-9"
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Segmented<TypeFilter>
            aria-label="Filter by media type"
            size="sm"
            value={type}
            onChange={setFilter}
            options={[
              { value: 'all', label: 'All' },
              { value: 'video', label: 'Videos' },
              { value: 'image', label: 'Images' },
            ]}
          />
          <div className="ml-auto flex items-center gap-2">
            <label className="text-xs text-fg-subtle" htmlFor="search-sort">
              Sort
            </label>
            <Select
              id="search-sort"
              value={sort}
              onChange={(event) => setSort(event.target.value)}
              className="h-8 w-40 text-xs"
            >
              <option value="name_asc">Name A–Z</option>
              <option value="name_desc">Name Z–A</option>
              <option value="date_desc">Newest first</option>
              <option value="date_asc">Oldest first</option>
              <option value="size_desc">Largest first</option>
              <option value="size_asc">Smallest first</option>
            </Select>
          </div>
        </div>

        {query ? (
          <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
            {result.isFetching && !result.isLoading ? (
              <Spinner size="sm" />
            ) : (
              <span className="tabular-nums">{result.data?.total.toLocaleString()} matches</span>
            )}
            {result.data?.truncated ? (
              <Badge variant="warning">
                Showing the first {result.data.limit.toLocaleString()} — refine the query
              </Badge>
            ) : null}
            <span className="text-fg-subtle">Every word must match the name or folder path.</span>
          </div>
        ) : null}
      </div>

      {!query ? (
        <EmptyState
          icon={<SearchIcon />}
          title="Search the whole library"
          description="Two words narrow it down: “iceland 2023” finds files in a folder by that name as well as files with those words in the filename."
        />
      ) : result.isError ? (
        <EmptyState
          icon={<AlertTriangle />}
          title="Search failed"
          description={result.error instanceof Error ? result.error.message : 'The index did not answer.'}
        />
      ) : result.isLoading ? (
        <div className="flex items-center gap-2 py-10 text-sm text-fg-subtle">
          <Spinner /> Searching…
        </div>
      ) : files.length === 0 ? (
        <EmptyState
          icon={<SearchIcon />}
          title={`No matches for “${query}”`}
          description="Try fewer words, or a folder name instead of a filename."
        />
      ) : (
        <JustifiedGrid
          files={files}
          targetRowHeight={TILE_ROW_HEIGHT[scale]}
          hintFor={(file) => (file as SearchFileEntry).rel_folder}
          onOpen={setLightboxIndex}
        />
      )}

      {lightboxIndex !== null ? (
        <MediaLightbox
          files={files}
          index={lightboxIndex}
          onIndexChange={setLightboxIndex}
          onClose={() => setLightboxIndex(null)}
        />
      ) : null}
    </div>
  )
}
