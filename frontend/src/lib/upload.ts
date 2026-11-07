/**
 * Resumable upload engine.
 *
 * A stable id per (name, size, mtime) lets a dropped connection or a reload
 * resume from the bytes the server already holds instead of starting over.
 */

export const CHUNK_SIZE = 4 * 1024 * 1024

/** FNV-1a over the identity string; no crypto.subtle (absent on plain HTTP). */
function hash(value: string): string {
  let result = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index)
    result = Math.imul(result, 0x01000193) >>> 0
  }
  return result.toString(16).padStart(8, '0')
}

export function makeUploadId(file: File): string {
  const identity = `${file.name}:${file.size}:${file.lastModified}`
  return `u${hash(identity)}${hash(identity.split('').reverse().join(''))}`
}

export interface UploadProgress {
  file: File
  uploadId: string
  sent: number
  total: number
  state: 'queued' | 'uploading' | 'done' | 'error' | 'skipped'
  error?: string
}

export interface UploadCallbacks {
  onProgress: (progress: UploadProgress) => void
}

interface UploadOptions {
  folder: string
  conflict?: 'auto_rename' | 'skip' | 'error'
  signal?: AbortSignal
}

interface UploadApi {
  uploadStatus: (
    uploadId: string,
    init?: RequestInit,
  ) => Promise<{ received_bytes: number; exists: boolean }>
  uploadChunk: (
    uploadId: string,
    offset: number,
    chunk: Blob,
    init?: RequestInit,
  ) => Promise<unknown>
  uploadFinalize: (
    payload: {
      upload_id: string
      name: string
      folder: string
      total_size: number
      conflict?: 'auto_rename' | 'skip' | 'error'
    },
    init?: RequestInit,
  ) => Promise<{ skipped: boolean; path: string }>
  uploadCancel: (uploadId: string) => Promise<unknown>
}

/** Upload one file, resuming from `received_bytes` when the server has some. */
export async function uploadFile(
  file: File,
  api: UploadApi,
  options: UploadOptions,
  callbacks: UploadCallbacks,
): Promise<{ skipped: boolean; path: string }> {
  const uploadId = makeUploadId(file)
  let sent = 0
  const report = (
    state: UploadProgress['state'],
    extra: Partial<UploadProgress> = {},
  ) => {
    callbacks.onProgress({
      file,
      uploadId,
      sent,
      total: file.size,
      state,
      ...extra,
    })
  }

  report('queued')

  const existing = await api.uploadStatus(uploadId, {
    signal: options.signal,
  })
  sent = Math.min(existing.received_bytes, file.size)
  report(sent === file.size ? 'queued' : 'uploading')

  try {
    if (sent < file.size) {
      while (sent < file.size) {
        if (options.signal?.aborted) {
          await api.uploadCancel(uploadId)
          throw new DOMException('Upload cancelled', 'AbortError')
        }
        const end = Math.min(sent + CHUNK_SIZE, file.size)
        const chunk = file.slice(sent, end)
        // The chunk transfer itself honours the signal: cancel acts on an
        // in-flight 4 MB chunk instead of waiting for it to land first.
        await api.uploadChunk(uploadId, sent, chunk, { signal: options.signal })
        sent = end
        report('uploading')
      }
    }

    const result = await api.uploadFinalize(
      {
        upload_id: uploadId,
        name: file.name,
        folder: options.folder,
        total_size: file.size,
        conflict: options.conflict ?? 'auto_rename',
      },
      { signal: options.signal },
    )
    report(result.skipped ? 'skipped' : 'done')
    return result
  } catch (error) {
    if (
      error instanceof DOMException &&
      error.name === 'AbortError' &&
      options.signal?.aborted
    ) {
      // A fetch aborted by our signal: make sure staging does not linger.
      await api.uploadCancel(uploadId).catch(() => undefined)
      throw new DOMException('Upload cancelled', 'AbortError')
    }
    throw error
  }
}
