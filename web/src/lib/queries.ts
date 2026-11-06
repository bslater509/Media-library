/** React Query hooks — the only place features read the v2 API. */

import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
} from '@tanstack/react-query'
import { api } from './api'

export const queryKeys = {
  info: ['info'] as const,
  browse: (path: string | null) => ['browse', path] as const,
  folders: (path: string | null) => ['folders', path] as const,
  folderSearch: (query: string) => ['folders', 'search', query] as const,
  search: (query: string, type: string, sort: string) =>
    ['search', query, type, sort] as const,
  favorites: (type: string) => ['favorites', type] as const,
}

export function useInfo() {
  return useQuery({
    queryKey: queryKeys.info,
    queryFn: api.info,
    staleTime: 60_000,
  })
}

export function useRootFolders() {
  return useQuery({
    queryKey: queryKeys.folders(null),
    queryFn: () => api.folders(null),
    staleTime: 60_000,
  })
}

export function useFolderChildren(path: string | null) {
  return useQuery({
    queryKey: queryKeys.folders(path),
    queryFn: () => api.folders(path),
    staleTime: 30_000,
  })
}

/** Infinite, offset-paged browse for one folder. */
export function useInfiniteBrowse(
  path: string | null,
  options: {
    type?: string
    favorite?: boolean
    sort?: string
    pageSize?: number
  } = {},
) {
  const type = options.type ?? 'all'
  const favorite = options.favorite ?? false
  const sort = options.sort ?? 'name_asc'
  const pageSize = options.pageSize ?? 120
  return useInfiniteQuery({
    queryKey: [...queryKeys.browse(path), type, favorite, sort] as const,
    queryFn: ({ pageParam }) =>
      api.browse({
        path,
        offset: pageParam,
        limit: pageSize,
        type: type === 'all' ? undefined : type,
        favorite,
        sort,
      }),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_offset ?? undefined,
    placeholderData: keepPreviousData,
    staleTime: 15_000,
  })
}

export function useFolderSearch(query: string) {
  const trimmed = query.trim()
  return useQuery({
    queryKey: queryKeys.folderSearch(trimmed),
    queryFn: () => api.folderSearch(trimmed),
    enabled: trimmed.length > 1,
    staleTime: 30_000,
  })
}

export function useSearch(
  query: string,
  options: { type?: string; sort?: string; limit?: number } = {},
) {
  const trimmed = query.trim()
  const type = options.type ?? 'all'
  const sort = options.sort ?? 'name_asc'
  return useQuery({
    queryKey: queryKeys.search(trimmed, type, sort),
    queryFn: () =>
      api.search(trimmed, {
        type: type === 'all' ? undefined : type,
        sort,
        limit: options.limit ?? 200,
      }),
    enabled: trimmed.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 15_000,
  })
}

export function useFavorites(type = 'all') {
  return useQuery({
    queryKey: queryKeys.favorites(type),
    queryFn: () => api.favorites(type === 'all' ? undefined : type),
    staleTime: 15_000,
  })
}
