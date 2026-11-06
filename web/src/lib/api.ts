/**
 * Typed client for the v2 API.
 *
 * The shapes mirror `media_library_v2/schemas.py`. When the app moves to a
 * generated client (`openapi-typescript`), this file is the only place that
 * changes — features import the hooks from `queries.ts`, never `fetch`.
 */

export const API_BASE = '/api/v2'

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
  needs_transcode: boolean
  playable: boolean
  thumb_url: string
  animated_url: string | null
  media_url: string
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

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

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

export const api = {
  info: () => request<InfoResponse>('/system/info'),

  browse: (path: string | null, offset = 0, limit = 120) =>
    request<BrowseResponse>('/library/browse', { path, offset, limit }),

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
}
