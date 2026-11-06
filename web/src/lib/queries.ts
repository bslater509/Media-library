/** React Query hooks — the only place features read the v2 API. */

import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { api, type ConflictPolicy } from './api'

export const queryKeys = {
  authSession: ['auth', 'session'] as const,
  info: ['info'] as const,
  onboarding: ['onboarding'] as const,
  indexStatus: ['index', 'status'] as const,
  browse: (path: string | null) => ['browse', path] as const,
  folders: (path: string | null) => ['folders', path] as const,
  folderSearch: (query: string) => ['folders', 'search', query] as const,
  search: (query: string, type: string, sort: string) =>
    ['search', query, type, sort] as const,
  favorites: (type: string) => ['favorites', type] as const,
  sorterConfig: ['sorter', 'config'] as const,
  sorterQueue: ['sorter', 'queue'] as const,
  sorterDestinations: (query: string) => ['sorter', 'destinations', query] as const,
  webpStatus: ['webp', 'status'] as const,
  zipOverview: ['zip', 'overview'] as const,
  zipBatch: ['zip', 'batch'] as const,
  backupStatus: ['backups', 'status'] as const,
  emptyFolders: ['empty', 'folders'] as const,
  dedupeStatus: ['dedupe', 'status'] as const,
  dedupeGroups: (mode: string, limit: number) =>
    ['dedupe', 'groups', mode, limit] as const,
  dedupeIgnored: (limit: number) => ['dedupe', 'ignored', limit] as const,
  downloadStatus: ['downloads', 'status'] as const,
  downloadHistory: (limit: number) => ['downloads', 'history', limit] as const,
}

export function useAuthSession() {
  return useQuery({
    queryKey: queryKeys.authSession,
    queryFn: api.authSession,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: true,
  })
}

export function useAuthLogin() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (password: string) => api.authLogin(password),
    onSuccess: (session) => {
      client.setQueryData(queryKeys.authSession, session)
      void client.invalidateQueries()
    },
  })
}

export function useAuthLogout() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.authLogout(),
    onSuccess: (session) => {
      client.setQueryData(queryKeys.authSession, session)
      client.removeQueries({ predicate: (query) => query.queryKey[0] !== 'auth' })
    },
  })
}

export function useIndexStatus() {
  return useQuery({
    queryKey: queryKeys.indexStatus,
    queryFn: api.indexStatus,
    refetchInterval: (query) =>
      query.state.data?.status === 'crawling' ? 2000 : 15_000,
    staleTime: 0,
  })
}

export function useIndexSync() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.indexSync(),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.indexStatus })
    },
  })
}

export function useOnboardingInfo() {
  return useQuery({
    queryKey: queryKeys.onboarding,
    queryFn: api.onboardingInfo,
    staleTime: 60_000,
  })
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

/** Invalidate everything a mutation can make stale. */
export function invalidateLibrary(client: QueryClient): Promise<unknown> {
  return Promise.all([
    client.invalidateQueries({ queryKey: ['browse'] }),
    client.invalidateQueries({ queryKey: ['folders'] }),
    client.invalidateQueries({ queryKey: ['search'] }),
    client.invalidateQueries({ queryKey: ['favorites'] }),
    client.invalidateQueries({ queryKey: ['info'] }),
  ])
}

export function useToggleFavorite() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (path: string) => api.toggleFavorite(path),
    onSuccess: () => invalidateLibrary(client),
  })
}

export function useFavoriteBatch() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      paths,
      action,
    }: {
      paths: string[]
      action: 'add' | 'remove' | 'toggle'
    }) => api.favoriteBatch(paths, action),
    onSuccess: () => invalidateLibrary(client),
  })
}

export function useBulkRename() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (renames: { path: string; name: string }[]) =>
      api.bulkRename(renames),
    onSuccess: () => invalidateLibrary(client),
  })
}

export function useRenameFile() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ path, name }: { path: string; name: string }) =>
      api.rename(path, name),
    onSuccess: () => invalidateLibrary(client),
  })
}

export function useDeleteFiles() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (paths: string[]) => api.deleteFiles(paths),
    onSuccess: () => invalidateLibrary(client),
  })
}

export function useCreateFolder() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ parent, name }: { parent: string; name: string }) =>
      api.createFolder(parent, name),
    onSuccess: () => invalidateLibrary(client),
  })
}

export function useMoveFiles() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      paths,
      destination,
      conflict,
    }: {
      paths: string[]
      destination: string
      conflict: ConflictPolicy
    }) => api.moveFiles(paths, destination, conflict),
    onSuccess: () => invalidateLibrary(client),
  })
}

export function useSorterConfig() {
  return useQuery({
    queryKey: queryKeys.sorterConfig,
    queryFn: api.sorterConfig,
    staleTime: 10_000,
  })
}

export function useSorterQueue() {
  return useQuery({
    queryKey: queryKeys.sorterQueue,
    queryFn: api.sorterQueue,
    staleTime: 5_000,
  })
}

export function useSorterDuplicate(path: string | null) {
  return useQuery({
    queryKey: ['sorter', 'duplicate', path],
    queryFn: () => api.sorterDuplicate(path as string),
    enabled: Boolean(path),
    staleTime: 30_000,
  })
}

export function useSorterDestinations(query: string) {
  const trimmed = query.trim()
  return useQuery({
    queryKey: queryKeys.sorterDestinations(trimmed),
    queryFn: () => api.sorterDestinations(trimmed),
    enabled: trimmed.length > 0,
    staleTime: 20_000,
  })
}

function useSorterMutation<TArgs>(
  fn: (args: TArgs) => Promise<import('./api').SorterQueue>,
) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: (payload) => {
      client.setQueryData(queryKeys.sorterQueue, payload)
      void client.invalidateQueries({ queryKey: queryKeys.sorterConfig })
      void invalidateLibrary(client)
    },
  })
}

export function useSorterMove() {
  return useSorterMutation(({ path, destination }: { path: string; destination: string }) =>
    api.sorterMove(path, destination),
  )
}

export function useSorterUndo() {
  return useSorterMutation(() => api.sorterUndo())
}

export function useSorterRename() {
  return useSorterMutation(({ path, name }: { path: string; name: string }) =>
    api.sorterRename(path, name),
  )
}

export function useSorterDelete() {
  return useSorterMutation(({ path }: { path: string }) => api.sorterDelete(path))
}

export function useSorterCreateFolder() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ parent, name }: { parent: string; name: string }) =>
      api.sorterCreateFolder(parent, name),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.sorterConfig })
      void invalidateLibrary(client)
    },
  })
}

export function useDownloadStatus() {
  return useQuery({
    queryKey: queryKeys.downloadStatus,
    queryFn: api.downloadStatus,
    refetchInterval: (query) =>
      query.state.data?.status === 'active' ? 1000 : 5000,
    staleTime: 0,
  })
}

export function useDownloadHistory(limit = 50) {
  return useQuery({
    queryKey: queryKeys.downloadHistory(limit),
    queryFn: () => api.downloadHistory(limit, 0),
    refetchInterval: 5000,
    staleTime: 0,
  })
}

export function useDownloadQueue() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ urls, destination }: { urls: string[]; destination: string }) =>
      api.downloadQueue(urls, destination),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['downloads'] })
    },
  })
}

export function useDownloadCancel() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => api.downloadCancel(jobId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['downloads'] })
    },
  })
}

export function useDownloadRetry() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => api.downloadRetry(jobId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['downloads'] })
    },
  })
}

export function useDownloadClear() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.downloadClearHistory(),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['downloads'] })
    },
  })
}

export function useDedupeStatus() {
  return useQuery({
    queryKey: queryKeys.dedupeStatus,
    queryFn: api.dedupeStatus,
    staleTime: 10_000,
  })
}

export function useDedupeGroups(mode: string, limit = 25) {
  return useQuery({
    queryKey: queryKeys.dedupeGroups(mode, limit),
    queryFn: () => api.dedupeGroups(mode, limit, 0),
    staleTime: 15_000,
  })
}

export function useDedupeIgnored(limit = 50) {
  return useQuery({
    queryKey: queryKeys.dedupeIgnored(limit),
    queryFn: () => api.dedupeIgnored(limit, 0),
    staleTime: 15_000,
  })
}

export function useDedupeHash() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ mode, limit = 50 }: { mode: string; limit?: number }) =>
      api.dedupeHash(mode, limit),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['dedupe'] })
    },
  })
}

export function useDedupeResolve() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      keep,
      deletes,
      mode,
    }: {
      keep: string
      deletes: string[]
      mode: string
    }) => api.dedupeResolve(keep, deletes, mode),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['dedupe'] })
      void invalidateLibrary(client)
    },
  })
}

export function useDedupeIgnore() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      path1,
      path2,
      mode,
    }: {
      path1: string
      path2: string
      mode: string
    }) => api.dedupeIgnore(path1, path2, mode),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['dedupe'] })
    },
  })
}

export function useDedupeUnignore() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ path1, path2 }: { path1: string; path2: string }) =>
      api.dedupeUnignore(path1, path2),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['dedupe'] })
    },
  })
}

export function useBackupStatus() {
  return useQuery({
    queryKey: queryKeys.backupStatus,
    queryFn: api.backupStatus,
    staleTime: 10_000,
  })
}

export function useBackupSnapshot() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.backupSnapshot(),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.backupStatus })
    },
  })
}

export function useBackupVerify() {
  return useMutation({ mutationFn: () => api.backupVerify() })
}

export function useBackupRestore() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.backupRestore(),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.backupStatus })
      void invalidateLibrary(client)
    },
  })
}

export function useZipOverview() {
  return useQuery({
    queryKey: queryKeys.zipOverview,
    queryFn: api.zipOverview,
    staleTime: 10_000,
  })
}

export function useZipBatch() {
  return useQuery({
    queryKey: queryKeys.zipBatch,
    queryFn: api.zipBatchStatus,
    refetchInterval: (query) =>
      ['running', 'paused'].includes(query.state.data?.status ?? '') ? 1000 : 5000,
    staleTime: 0,
  })
}

function useZipBatchInvalidate() {
  const client = useQueryClient()
  return () => {
    void client.invalidateQueries({ queryKey: queryKeys.zipBatch })
    void client.invalidateQueries({ queryKey: ['zip'] })
  }
}

export function useZipBatchStart() {
  const invalidate = useZipBatchInvalidate()
  return useMutation({
    mutationFn: (mode: 'compress' | 'unpack') => api.zipBatchStart(mode),
    onSuccess: invalidate,
  })
}

export function useZipBatchPause() {
  const invalidate = useZipBatchInvalidate()
  return useMutation({ mutationFn: () => api.zipBatchPause(), onSuccess: invalidate })
}

export function useZipBatchResume() {
  const invalidate = useZipBatchInvalidate()
  return useMutation({ mutationFn: () => api.zipBatchResume(), onSuccess: invalidate })
}

export function useZipBatchCancel() {
  const invalidate = useZipBatchInvalidate()
  return useMutation({ mutationFn: () => api.zipBatchCancel(), onSuccess: invalidate })
}

export function useZipCompress() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (path: string) => api.zipCompress(path),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['zip'] })
      void invalidateLibrary(client)
    },
  })
}

export function useZipUnpack() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (path: string) => api.zipUnpack(path),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['zip'] })
      void invalidateLibrary(client)
    },
  })
}

export function useAnimatedStatus(enabled = true) {
  return useQuery({
    queryKey: ['thumbnails', 'animated', 'status'],
    queryFn: api.animatedStatus,
    enabled,
    refetchInterval: (query) =>
      query.state.data?.status === 'working' || query.state.data?.status === 'queued'
        ? 2000
        : false,
    staleTime: 5_000,
  })
}

export function useGenerateAnimated() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (path: string) => api.generateAnimated(path),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['thumbnails', 'animated'] })
      void invalidateLibrary(client)
    },
  })
}

export function useGenerateAnimatedBatch() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { folder?: string; all_videos?: boolean }) =>
      api.generateAnimatedBatch(input),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['thumbnails', 'animated'] })
    },
  })
}

export function useSetPoster() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ path, timestamp }: { path: string; timestamp: number }) =>
      api.setPoster(path, timestamp),
    onSuccess: () => {
      void invalidateLibrary(client)
    },
  })
}

export function useWebpStatus() {
  return useQuery({
    queryKey: queryKeys.webpStatus,
    queryFn: api.webpStatus,
    staleTime: 10_000,
  })
}

export function useWebpOptimize() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: number | { limit: number; paths: string[] }) =>
      typeof input === 'number'
        ? api.webpOptimize(input)
        : api.webpOptimize(input.limit, input.paths),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['webp'] })
      void invalidateLibrary(client)
    },
  })
}

export function useEmptyFolders() {
  return useQuery({
    queryKey: queryKeys.emptyFolders,
    queryFn: api.emptyFolders,
    staleTime: 10_000,
  })
}

export function useDeleteEmptyFolders() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (paths: string[]) => api.deleteEmptyFolders(paths),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['empty'] })
      void invalidateLibrary(client)
    },
  })
}

export function useAnalyticsOverview(refreshNonce = 0) {
  return useQuery({
    queryKey: ['analytics', refreshNonce],
    queryFn: () => api.analyticsOverview(refreshNonce > 0),
    staleTime: 30_000,
  })
}
