/**
 * Typed client for the app's JSON API.
 *
 * The shapes mirror `media_library/schemas.py`. When the app moves to a
 * generated client (`openapi-typescript`), this file is the only place that
 * changes — features import the hooks from `queries.ts`, never `fetch`.
 */

export const API_BASE = '/api'

export interface WebMP4Preview {
  token: string
  total: number
  archives: number
  sample: { path: string; ext: string | null; video_codec: string | null; audio_codec: string | null }[]
}

export interface WebMP4Status {
  status: string
  total: number
  processed: number
  converted: number
  skipped: number
  failed: number
  current: string | null
  percent: number
  phase: string
  errors: { path: string; error: string }[]
  cancel_requested: boolean
}

export interface PosterBatchStatus {
  status: string
  active: string | null
  queued: number
  completed: number
  failed: number
  error: string | null
  added: number
}

export type ActivityAction = 'pause' | 'resume' | 'cancel' | 'retry'

export interface ActivityItem {
  id: string
  label: string
  href: string
  state: string
  current: string | null
  detail: string
  error: string | null
  percent: number | null
  controls: ActivityAction[]
  job_id: string | null
  updated_at: number | null
}

export interface ActivitySummary {
  prioritize_browsing: boolean
  active_count: number
  failure_count: number
  items: ActivityItem[]
}

export interface Breadcrumb {
  name: string
  path: string
  is_zip: boolean
  raw_name?: string | null
}

export interface FolderEntry {
  name: string
  raw_name: string
  path: string
  file_count: number
  subfolder_count: number
  is_zip: boolean
  total_size: number
  size_formatted: string
  cover_thumb_url: string | null
  cover_width: number | null
  cover_height: number | null
}

export interface FileEntry {
  id: string
  name: string
  path: string
  folder_path: string
  type: 'video' | 'image' | string
  ext: string
  size: number
  size_formatted: string
  mtime: number | null
  width: number | null
  height: number | null
  duration: number | null
  video_codec: string | null
  audio_codec: string | null
  fps: number | null
  capture_time: number | null
  has_thumb: boolean
  has_animated: boolean
  is_favorite: boolean
  seconds_watched: number | null
  play_count: number | null
  last_viewed_at: number | null
  needs_transcode: boolean
  playable: boolean
  thumb_url: string
  animated_url: string | null
  media_url: string
  transcode_url: string | null
}

export interface SearchFileEntry extends FileEntry {
  folder_name: string
  rel_folder: string
}

export interface BrowseResponse {
  path: string
  root: string
  name: string
  breadcrumbs: Breadcrumb[]
  folders: FolderEntry[]
  files: FileEntry[]
  total: number
  offset: number
  limit: number
  next_offset: number | null
  indexed: boolean
  is_zip: boolean
}

export interface FoldersResponse {
  path: string
  root: string
  folders: FolderEntry[]
}

export interface FolderSearchResponse {
  query: string
  results: FolderEntry[]
}

export interface SearchResponse {
  query: string
  total: number
  limit: number
  offset: number
  truncated: boolean
  results: SearchFileEntry[]
}

export interface Counts {
  files: number
  folders: number
  videos: number
  images: number
  favorites: number
  watched: number
}

export interface InfoResponse {
  version: string
  library_root: string
  counts: Counts
  features: Record<string, boolean>
}

export interface FavoritesResponse {
  counts: Counts
  files: FileEntry[]
}

/** One rollup row after a viewing was recorded. */
export interface ViewRecordResponse {
  path: string
  seconds_watched: number
  play_count: number
  last_viewed_at: number
}

/** One of the watch-history listings: recent, in-progress or never viewed. */
export interface WatchedListResponse {
  total: number
  limit: number
  offset: number
  files: FileEntry[]
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export interface FavoriteCounts {
  all: number
  videos: number
  images: number
}

export interface FavoriteResponse {
  path: string
  is_favorite: boolean
  counts: FavoriteCounts
}

export interface FavoriteBatchResult {
  updated: number
  counts: FavoriteCounts
  failed: { path: string; error: string }[]
}

export interface RenameResponse {
  path: string
  file: FileEntry | null
}

export interface BulkRenameResult {
  renamed: string[]
  failed: { path: string; error: string }[]
}

export interface OperationFailure {
  path: string
  error: string
}

export interface DeleteResponse {
  deleted: string[]
  failed: OperationFailure[]
}

export interface MoveItem {
  source?: string
  target?: string
  path?: string
  reason?: string
}

export interface MoveResponse {
  moved: MoveItem[]
  skipped: MoveItem[]
  failed: OperationFailure[]
}

export interface CreateFolderResponse {
  path: string
  name: string
}

export interface UploadStatus {
  upload_id: string
  exists: boolean
  received_bytes: number
}

export interface UploadChunkResponse {
  received_bytes: number
  duplicate: boolean
}

export interface UploadFinalizeResponse {
  skipped: boolean
  path: string
  file: FileEntry | null
}

export interface SorterItem {
  id: string
  name: string
  path: string
  size: number
  size_formatted: string
  mtime: number
  ext: string
  playable: boolean
  needs_transcode: boolean
  stream_url: string
  transcode_url: string | null
}

export interface SorterDestination {
  path: string
  name: string
  file_count?: number | null
  subfolder_count?: number | null
}

export interface SorterDuplicate {
  path: string
  matched: boolean
  duplicates: { path: string; name: string; size_formatted: string }[]
}

export interface SorterQueue {
  source_dir: string
  available: boolean
  items: SorterItem[]
  undo_count: number
  moved?: { source: string; target: string } | null
  restored?: string | null
  path?: string | null
}

export interface SorterConfig {
  source_dir: string
  source_available: boolean
  library_root: string
  recent_destinations: SorterDestination[]
  queue_count: number
  undo_count: number
}

export interface SorterSourceChild {
  name: string
  path: string
}

export interface SorterSourceListing {
  path: string
  parent: string | null
  children: SorterSourceChild[]
}

export interface DownloadFileProgress {
  name: string
  done: boolean
  percent: number | null
  downloaded_bytes: number | null
  total_bytes: number | null
}

export interface DownloadJob {
  id: string
  url: string
  destination: string
  status: 'queued' | 'downloading' | 'completed' | 'failed' | 'cancelled'
  percent: number
  downloaded_bytes: number
  total_bytes: number
  speed: string
  eta: string
  title: string
  downloader: string
  error: string
  files: string[]
  file_progress: DownloadFileProgress[]
  playlist_index: number | null
  playlist_count: number | null
  created_at: number
  started_at: number | null
  finished_at: number | null
}

export interface DownloadStatus {
  status: string
  concurrency: number
  tools: { ytdlp: boolean; gallery_dl: boolean }
  active: DownloadJob[]
  queued: DownloadJob[]
  active_count: number
  queued_count: number
  history: DownloadJob[]
}

export interface DownloadHistory {
  items: DownloadJob[]
  total: number
  limit: number
  offset: number
}

export interface ToolInfo {
  key: 'ytdlp' | 'gallery_dl'
  name: string
  version: string | null
  latest: string | null
  update_available: boolean
  check_error: string | null
}

export interface ToolUpdateJob {
  status: 'running' | 'success' | 'failed' | 'cancelled'
  tools: string[]
  phase: string
  progress: number
  log: string[]
  error: string | null
  versions: Record<string, string | null>
  cancel_requested: boolean
  started_at: number
  finished_at: number | null
}

export interface ToolStatus {
  tools: ToolInfo[]
  checking: boolean
  update: ToolUpdateJob | null
}

export interface DownloadEnqueueResponse {
  jobs: DownloadJob[]
  rejected: Array<{ url: string; error: string }>
}

export interface DedupeFramePair {
  path: string
  against: string
  compared: number
  matches: number
  distances: number[]
  confirmed: boolean | null
}

export interface DedupeVerification {
  available: boolean
  confirmed: boolean | null
  checked_pairs: number
  confirmed_pairs: number
  frames: number
  pairs: DedupeFramePair[]
}

export interface DedupeGroup {
  id: string
  match_type: 'content' | 'visual'
  size: number
  size_formatted: string
  reclaimable: number
  files: FileEntry[]
  verification: DedupeVerification | null
}

export interface DedupeGroups {
  mode: string
  total_groups: number
  total_files: number
  unhashed: number
  rejected_groups: number
  groups: DedupeGroup[]
}

export interface DedupeStatus {
  content: { candidate_files: number; unhashed: number }
  visual: { hashed: number; unhashed: number }
  ignored: number
}

export interface DedupeResetResult {
  ignored_cleared: number
  signatures_cleared: number
}

export interface DedupeVisualCheckStatus {
  status: 'idle' | 'queued' | 'running' | 'done' | string
  running: boolean
  total_groups: number
  completed_groups: number
  queued_groups: number
  current: string[]
  stage: string
  active_files: number
  active_index: number
  active_file: string
  active_frame: number
  active_frames: number
  confirmed: number
  rejected: number
  unknown: number
  errors: number
  started_at: number | null
  finished_at: number | null
}

export interface DedupeHashResult {
  hashed: number
  failed: number
  remaining: number
}

export interface DedupeResolveResult {
  kept: string
  deleted: string[]
  failed: Array<{ path: string; error: string }>
  mismatched: string[]
}

export interface DedupeResolveBatchItemInput {
  keep: string
  deletes: string[]
  mode: string
}

export interface DedupeResolveBatchResult {
  attempted: number
  resolved: number
  deleted: string[]
  failed: Array<{ path: string; error: string }>
  mismatched: string[]
}

export interface IgnoredPair {
  id: number
  path1: string
  path2: string
  match_type: string
  created_at: number
}

export interface IgnoredPairsPage {
  items: IgnoredPair[]
  total: number
  limit: number
  offset: number
}

export interface WebpStatus {
  pending: number
  saved_bytes: number
  saved_formatted: string
  converted: number
  skipped: number
  quality: number
  skip_larger: boolean
  scope: string
  updated_at: number
}

export interface WebpOptimizeResult {
  converted: number
  skipped: number
  failed: number
  saved_bytes: number
  saved_formatted: string
  remaining: number
  errors: Array<{ path: string; error: string }>
  quality: number
  skip_larger: boolean
}

export interface EmptyFolder {
  path: string
  name: string
  parent_path: string | null
}

export interface EmptyFolders {
  count: number
  folders: EmptyFolder[]
}

export interface EmptyDeleteResult {
  deleted: string[]
  failed: Array<{ path: string; error: string }>
  remaining: number
}

export interface ZipArrayItem {
  path: string
  name: string
  file_count: number
  size_formatted: string
}

export interface ThumbnailFrame {
  timestamp: number
  label: string
  url: string
}

export interface ThumbnailCandidates {
  path: string
  duration: number | null
  interval: number
  frames: ThumbnailFrame[]
}

export interface PosterResult {
  path: string
  timestamp: number
  thumb_mtime: number
}

export interface AnimatedStatus {
  added: number
  status: string
  active: string | null
  queued: number
  completed: number
  failed: number
  total: number
  pending: number
  ready: number
  failed_total: number
}

export interface ZipOverview {
  archives: ZipArrayItem[]
  cache: { files: number; bytes: number; size_formatted: string }
}

export interface ZipUnpackResult {
  folder: string
  files: number
  removed: string
}

export interface ZipBatchStatus {
  mode: 'unpack' | null
  status: 'idle' | 'running' | 'paused' | 'completed' | 'cancelled'
  total: number
  processed: number
  succeeded: number
  failed: number
  percent: number
  current_path: string | null
  errors: Array<{ path: string; error: string }>
  started_at: number | null
  finished_at: number | null
}

export interface AuthSession {
  enabled: boolean
  authenticated: boolean
}

export interface IndexStatus {
  owned: boolean
  runtime: {
    owned: boolean
    started: boolean
    crawler: boolean
    metadata: boolean
    thumbnails: boolean
    backups: boolean
  }
  status: string
  scanned_folders: number
  skipped_folders: number
  total_folders: number
  current_folder: string
  last_synced: number
  revision: number
  prune_aborted: number
  stale_folders: number
  stale_files: number
  error: string
  started_at: number
  finished_at: number
  duration_seconds: number
  elapsed_seconds: number
  force_full: boolean
  files_seen: number
  files_added: number
  files_updated: number
  files_removed: number
  archives_indexed: number
}

export interface X265Job {
  id: string
  source_path: string
  output_path: string | null
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  percent: number
  fps: string
  speed: string
  eta: string
  error: string
  duration: number
  audio_mode: string
  created_at: number
  started_at: number | null
  finished_at: number | null
}

export interface X265Status {
  active: X265Job | null
  queued: X265Job[]
  job: X265Job | null
  active_count: number
  queued_count: number
  history: X265Job[]
}

export interface OnboardingInfo {
  version: string
  library_root: string
  source_dir: string
  source_available: boolean
  counts: Counts
  features: Record<string, boolean>
  index: {
    root_indexed: boolean
    folders: number
    files: number
    last_scanned: number | null
  }
  backup: { exists: boolean; created_at_str: string | null }
  tools: { ytdlp: boolean; gallery_dl: boolean }
}

export interface BackupStatus {
  exists: boolean
  directory: string
  created_at: number | null
  created_at_str: string | null
  version: string | null
  files: number | null
  folders: number | null
  thumbnails: number | null
  db_bytes: number
  db_size_formatted: string
  tar_bytes: number
  tar_size_formatted: string
  allow_live_restore: boolean
}

export interface BackupSnapshotResult {
  success: boolean
  created_at: number
  files: number
  folders: number
  thumbnails: number
  db_bytes: number
  tar_bytes: number
  directory: string
}

export interface BackupVerifyResult {
  snapshot_integrity: string
  live_integrity: string
  files: number
  folders: number
  thumbnails: number
  meta_present: boolean
}

export interface BackupRestoreResult {
  restored: boolean
  database: string
  thumbnails: number
  files: number
  folders: number
  restart_required: boolean
}

export interface AnalyticsTreeNode {
  name: string
  path: string
  bytes: number
  files: number
  share: number
  is_group: boolean
  children?: Array<{ name: string; path: string; bytes: number; files: number }>
}

export interface AnalyticsOverview {
  generated_at: number
  library_root: string
  limits: { folders: number; files: number }
  totals: {
    files: number
    folders: number
    zips: number
    videos: number
    images: number
    bytes: number
    runtime: number
    runtime_formatted: string
    size_formatted: string
  }
  by_type: Array<{ type: string; files: number; bytes: number; share: number }>
  by_extension: {
    items: Array<{ ext: string; files: number; bytes: number; share: number }>
    total_files: number
  }
  folders: {
    root: string
    root_bytes: number
    root_files: number
    top: AnalyticsTreeNode[]
    largest: AnalyticsTreeNode[]
    folder_count: number
  }
  largest_files: FileEntry[]
  quality: {
    buckets: Array<{ label: string; count: number }>
    probed: number
    high_fps: number
    runtime: number
    runtime_formatted: string
  }
  reclaimable: {
    duplicates: { groups: number; files: number; bytes: number; size_formatted: string }
    webp: { saved_bytes: number; saved_formatted: string; converted: number; pending: number }
    empty_folders: number
  }
  health: {
    missing_thumbnails: number
    unprobed: number
    missing_visual_hashes: number
    missing_animated: number
    needs_transcode: number
    ignored_pairs: number
  }
}

export type ConflictPolicy = 'auto_rename' | 'skip' | 'error'

type ParamValue = string | number | boolean | null | undefined

function buildUrl(path: string, params?: Record<string, ParamValue>): string {
  const url = new URL(API_BASE + path, window.location.origin)
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, String(value))
      }
    }
  }
  return url.pathname + url.search
}

async function request<T>(
  path: string,
  params?: Record<string, ParamValue>,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(buildUrl(path, params), {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
    ...init,
  })

  if (!response.ok) {
    let detail = response.statusText || 'Request failed'
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* a non-JSON error body is still an error; keep the status text */
    }
    throw new ApiError(response.status, detail)
  }

  return (await response.json()) as T
}

async function post<T>(path: string, body: unknown, init?: RequestInit): Promise<T> {
  return request<T>(path, undefined, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...init,
  })
}

export const api = {
  webMP4Preview: () => request<WebMP4Preview>('/transcode/web-mp4/preview'),
  webMP4Status: () => request<WebMP4Status>('/transcode/web-mp4/status'),
  webMP4Start: (token: string) =>
    post<WebMP4Status>('/transcode/web-mp4/start', { token, confirm_replace: true }),
  webMP4Cancel: () => post<WebMP4Status>('/transcode/web-mp4/cancel', {}),
  regeneratePosters: (scope: { paths?: string[]; folder?: string }) =>
    post<PosterBatchStatus>('/thumbnails/poster/batch', scope),
  posterBatchStatus: () => request<PosterBatchStatus>('/thumbnails/poster/status'),
  authSession: () => request<AuthSession>('/auth/session'),

  authLogin: (password: string) =>
    post<AuthSession>('/auth/login', { password }),

  authLogout: () => post<AuthSession>('/auth/logout', {}),

  info: () => request<InfoResponse>('/system/info'),

  browse: (options: {
    path: string | null
    offset?: number
    limit?: number
    type?: string
    favorite?: boolean
    sort?: string
  }) =>
    request<BrowseResponse>('/library/browse', {
      path: options.path,
      offset: options.offset ?? 0,
      limit: options.limit ?? 120,
      type: options.type,
      favorite: options.favorite || undefined,
      sort: options.sort === 'name_asc' ? undefined : options.sort,
    }),

  folders: (path: string | null) =>
    request<FoldersResponse>('/library/folders', { path }),

  folderSearch: (query: string, limit = 20) =>
    request<FolderSearchResponse>('/library/folders/search', { q: query, limit }),

  search: (
    query: string,
    options: { type?: string; sort?: string; limit?: number; offset?: number } = {},
  ) =>
    request<SearchResponse>('/library/search', {
      q: query,
      type: options.type,
      sort: options.sort ?? 'name_asc',
      limit: options.limit ?? 120,
      offset: options.offset ?? 0,
    }),

  favorites: (type?: string) =>
    request<FavoritesResponse>('/library/favorites', { type }),

  /** `bump` counts a play; closing/progress follow-ups send `bump: false`. */
  recordView: (path: string, seconds: number, bump = true) =>
    post<ViewRecordResponse>('/history/record', { path, seconds, bump }),

  watchHistoryRecent: (options: { type?: 'video' | 'image'; limit?: number; offset?: number } = {}) =>
    request<WatchedListResponse>('/history/recent', {
      type: options.type,
      limit: options.limit,
      offset: options.offset ?? 0,
    }),

  watchHistoryResume: (options: { limit?: number; offset?: number } = {}) =>
    request<WatchedListResponse>('/history/resume', {
      limit: options.limit,
      offset: options.offset ?? 0,
    }),

  watchHistoryUnseen: (options: { type?: 'video' | 'image'; limit?: number; offset?: number } = {}) =>
    request<WatchedListResponse>('/history/unwatched', {
      type: options.type,
      limit: options.limit,
      offset: options.offset ?? 0,
    }),

  toggleFavorite: (path: string) =>
    post<FavoriteResponse>('/favorites/toggle', { path }),

  favoriteBatch: (paths: string[], action: 'add' | 'remove' | 'toggle') =>
    post<FavoriteBatchResult>('/favorites/batch', { paths, action }),

  bulkRename: (renames: { path: string; name: string }[]) =>
    post<BulkRenameResult>('/manage/bulk-rename', { renames }),

  renameFolder: (path: string, name: string) =>
    post<{ path: string; name: string }>('/manage/folders/rename', {
      path,
      name,
    }),

  rename: (path: string, name: string) =>
    post<RenameResponse>('/manage/rename', { path, name }),

  deleteFiles: (paths: string[]) =>
    post<DeleteResponse>('/manage/delete', { paths }),

  createFolder: (parent: string, name: string) =>
    post<CreateFolderResponse>('/manage/folders', { parent, name }),

  moveFiles: (paths: string[], destination: string, conflict: ConflictPolicy) =>
    post<MoveResponse>('/manage/move', { paths, destination, conflict }),

  uploadStatus: (uploadId: string, init?: RequestInit) =>
    request<UploadStatus>('/uploads/status', { upload_id: uploadId }, init),

  uploadChunk: async (
    uploadId: string,
    offset: number,
    chunk: Blob,
    init?: RequestInit,
  ) => {
    const form = new FormData()
    form.set('upload_id', uploadId)
    form.set('offset', String(offset))
    form.set('file', chunk, 'chunk')
    const response = await fetch(`${API_BASE}/uploads/chunk`, {
      method: 'POST',
      credentials: 'same-origin',
      body: form,
      ...init,
    })
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as { detail?: string }
      throw new ApiError(response.status, body.detail ?? 'Chunk upload failed')
    }
    return (await response.json()) as UploadChunkResponse
  },

  uploadFinalize: (
    payload: {
      upload_id: string
      name: string
      folder: string
      total_size: number
      conflict?: ConflictPolicy
    },
    init?: RequestInit,
  ) =>
    post<UploadFinalizeResponse>('/uploads/finalize', payload, init),

  uploadCancel: (uploadId: string) =>
    post<{ cancelled: boolean }>('/uploads/cancel', { upload_id: uploadId }),

  sorterConfig: () => request<SorterConfig>('/sorter/config'),

  sorterSetSource: (path: string | null) =>
    post<SorterConfig>('/sorter/source', { path }),

  sorterSourceChildren: (path?: string) =>
    request<SorterSourceListing>('/sorter/source/children', {
      path: path || undefined,
    }),

  sorterQueue: () => request<SorterQueue>('/sorter/queue'),

  sorterDuplicate: (path: string) =>
    request<SorterDuplicate>('/sorter/duplicate', { path }),

  sorterRefresh: () => post<SorterQueue>('/sorter/refresh', {}),

  sorterDestinations: (query: string, limit = 30) =>
    request<SorterDestination[]>('/sorter/destinations', { q: query, limit }),

  sorterCreateFolder: (parent: string, name: string) =>
    post<SorterDestination>('/sorter/folders', { parent, name }),

  sorterMove: (path: string, destination: string) =>
    post<SorterQueue>('/sorter/move', { path, destination }),

  sorterUndo: () => post<SorterQueue>('/sorter/undo', {}),

  sorterRename: (path: string, name: string) =>
    post<SorterQueue>('/sorter/rename', { path, name }),

  sorterDelete: (path: string) => post<SorterQueue>('/sorter/delete', { path }),

  sorterThumbnail: (path: string) =>
    `${API_BASE}/sorter/thumbnail?path=${encodeURIComponent(path)}`,

  backupDownload: (kind: 'db' | 'thumbs') =>
    `${API_BASE}/backups/download?kind=${kind}`,

  downloadStatus: () => request<DownloadStatus>('/downloads/status'),

  toolStatus: (refresh = false) =>
    request<ToolStatus>('/tools/status', { refresh: refresh || undefined }),

  toolUpdate: (tools: string[]) => post<ToolUpdateJob>('/tools/update', { tools }),

  toolUpdateCancel: () => post<{ cancelled: boolean }>('/tools/update/cancel', {}),

  downloadQueue: (urls: string[], destination: string) =>
    post<DownloadEnqueueResponse>('/downloads/queue', { urls, destination }),

  downloadCancel: (jobId: string) =>
    post<{ cancelled: string }>('/downloads/cancel', { job_id: jobId }),

  downloadRetry: (jobId: string) =>
    post<{ job: DownloadJob }>('/downloads/retry', { job_id: jobId }),

  downloadClearHistory: () =>
    post<{ cleared: number }>('/downloads/clear-history', {}),

  downloadHistory: (limit = 50, offset = 0) =>
    request<DownloadHistory>('/downloads/history', { limit, offset }),

  dedupeStatus: (includeImages = true) =>
    request<DedupeStatus>('/dedupe/status', { include_images: includeImages }),

  dedupeGroups: (
    mode: string,
    limit = 25,
    offset = 0,
    includeRejected = false,
    includeImages = true,
  ) =>
    request<DedupeGroups>('/dedupe/groups', {
      mode,
      limit,
      offset,
      include_rejected: includeRejected,
      include_images: includeImages,
    }),

  dedupeHash: (mode: string, limit = 50, includeImages = true) =>
    post<DedupeHashResult>('/dedupe/hash', {
      mode,
      limit,
      include_images: includeImages,
    }),

  dedupeResolve: (keep: string, deletes: string[], mode: string) =>
    post<DedupeResolveResult>('/dedupe/resolve', {
      keep,
      delete: deletes,
      mode,
    }),

  dedupeResolveBatch: (items: DedupeResolveBatchItemInput[]) =>
    post<DedupeResolveBatchResult>('/dedupe/resolve/batch', {
      items: items.map((item) => ({
        keep: item.keep,
        delete: item.deletes,
        mode: item.mode,
      })),
    }),

  dedupeVerifyVisual: (paths: string[]) =>
    post<DedupeVerification>('/dedupe/verify-visual', { paths }),

  dedupeVerifyVisualBatch: (groups: string[][]) =>
    post<DedupeVisualCheckStatus>('/dedupe/verify-visual/batch', { groups }),

  dedupeVerifyVisualStatus: () =>
    request<DedupeVisualCheckStatus>('/dedupe/verify-visual/status'),

  dedupeReset: () => post<DedupeResetResult>('/dedupe/reset', {}),

  dedupeIgnore: (path1: string, path2: string, matchType: string) =>
    post<{ ignored: boolean }>('/dedupe/ignore', {
      path1,
      path2,
      match_type: matchType,
    }),

  dedupeIgnored: (limit = 50, offset = 0) =>
    request<IgnoredPairsPage>('/dedupe/ignored', { limit, offset }),

  dedupeUnignore: (path1: string, path2: string) =>
    post<{ unignored: boolean }>('/dedupe/unignore', { path1, path2 }),

  webpStatus: () => request<WebpStatus>('/webp/status'),

  webpOptimize: (limit = 20, paths?: string[]) =>
    post<WebpOptimizeResult>('/webp/optimize', paths ? { limit, paths } : { limit }),

  emptyFolders: () => request<EmptyFolders>('/folders/empty'),

  deleteEmptyFolders: (paths: string[]) =>
    post<EmptyDeleteResult>('/folders/empty/delete', { paths }),

  zipOverview: () => request<ZipOverview>('/zip/overview'),

  zipUnpack: (path: string) => post<ZipUnpackResult>('/zip/unpack', { path }),

  zipBatchStatus: () => request<ZipBatchStatus>('/zip/batch/status'),

  zipBatchStart: (mode: 'unpack', paths?: string[]) =>
    post<ZipBatchStatus>('/zip/batch/start', { mode, paths }),

  zipBatchPause: () => post<ZipBatchStatus>('/zip/batch/pause', {}),

  zipBatchResume: () => post<ZipBatchStatus>('/zip/batch/resume', {}),

  zipBatchCancel: () => post<ZipBatchStatus>('/zip/batch/cancel', {}),

  onboardingInfo: () => request<OnboardingInfo>('/system/onboarding'),

  indexStatus: () => request<IndexStatus>('/system/index/status'),

  indexSync: (full = true) =>
    request<{ started: boolean }>(
      '/system/index/sync',
      { full },
      { method: 'POST' },
    ),

  thumbnailCandidates: (path: string, interval = 30, count?: number) =>
    request<ThumbnailCandidates>('/thumbnails/candidates', {
      path,
      interval,
      count,
    }),

  setPoster: (path: string, timestamp: number) =>
    post<PosterResult>('/thumbnails/poster', { path, timestamp }),

  generateAnimated: (path: string) =>
    post<AnimatedStatus>('/thumbnails/animated', { path }),

  generateAnimatedBatch: (input: { folder?: string; all_videos?: boolean }) =>
    post<AnimatedStatus>('/thumbnails/animated/batch', input),

  animatedStatus: () => request<AnimatedStatus>('/thumbnails/animated/status'),

  activity: () => request<ActivitySummary>('/system/activity'),
  activityPriority: (prioritize_browsing: boolean) =>
    post<ActivitySummary>('/system/activity/priority', { prioritize_browsing }),
  activityAction: (id: string, action: ActivityAction) =>
    post<ActivitySummary>('/system/activity/action', { id, action }),

  cancelAnimated: () => post<AnimatedStatus>('/thumbnails/animated/cancel', {}),

  x265Start: (path: string) =>
    post<X265Job>('/transcode/x265', { path }),

  x265Status: (path: string) =>
    request<X265Status>('/transcode/x265/status', { path }),

  x265Cancel: (jobId: string) =>
    post<{ cancelled: string }>('/transcode/x265/cancel', { job_id: jobId }),

  backupStatus: () => request<BackupStatus>('/backups/status'),

  backupSnapshot: () => post<BackupSnapshotResult>('/backups/snapshot', {}),

  backupVerify: () => request<BackupVerifyResult>('/backups/verify'),

  backupRestore: () => post<BackupRestoreResult>('/backups/restore', {}),

  analyticsOverview: (refresh = false, folderLimit = 15, fileLimit = 25) =>
    request<AnalyticsOverview>('/analytics/overview', {
      refresh: refresh || undefined,
      folder_limit: folderLimit,
      file_limit: fileLimit,
    }),
}
