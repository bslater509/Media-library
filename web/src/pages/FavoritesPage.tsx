import { useState } from 'react'
import { Link } from 'react-router'
import { Star } from 'lucide-react'
import { useFavorites } from '../lib/queries'
import { TILE_ROW_HEIGHT, useDensityStore } from '../lib/stores'
import { EmptyState, Segmented, Spinner } from '../components/ui'
import { JustifiedGrid } from '../components/media/JustifiedGrid'
import { MediaLightbox } from '../components/media/MediaLightbox'

type TypeFilter = 'all' | 'video' | 'image'

export function FavoritesPage() {
  const [type, setType] = useState<TypeFilter>('all')
  const scale = useDensityStore((state) => state.scale)
  const favourites = useFavorites(type)
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)
  const files = favourites.data?.files ?? []
  const counts = favourites.data?.counts

  return (
    <div className="flex flex-col gap-4 px-3 py-4 sm:px-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-base font-semibold tracking-tight">Favorites</h1>
          <p className="text-xs text-fg-muted">
            {counts
              ? `${counts.favorites.toLocaleString()} starred across the library`
              : 'Starred media from every folder'}
          </p>
        </div>
        <Segmented<TypeFilter>
          aria-label="Filter favorites"
          size="sm"
          value={type}
          onChange={setType}
          options={[
            { value: 'all', label: 'All' },
            { value: 'video', label: 'Videos' },
            { value: 'image', label: 'Images' },
          ]}
        />
      </div>

      {favourites.isLoading ? (
        <div className="flex items-center gap-2 py-10 text-sm text-fg-subtle">
          <Spinner /> Loading favorites…
        </div>
      ) : files.length === 0 ? (
        <EmptyState
          icon={<Star />}
          title="No favorites yet"
          description="Star a file from the current app and it appears here — favorites are stored in the index, not in this preview."
          action={
            <Link to="/library" className="text-sm text-accent hover:underline">
              Browse the library
            </Link>
          }
        />
      ) : (
        <JustifiedGrid
          files={files}
          targetRowHeight={TILE_ROW_HEIGHT[scale]}
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
