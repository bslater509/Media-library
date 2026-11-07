import { describe, expect, it, vi } from 'vitest'
import { makeUploadId, uploadFile } from './upload'

function makeFile(content: string, name = 'clip.mp4'): File {
  return new File([content], name, { lastModified: 1_700_000_000_000 })
}

function fakeApi(received: number) {
  const chunks: Array<{ offset: number; size: number }> = []
  const status = vi.fn(async () => ({ received_bytes: received, exists: received > 0 }))
  const chunk = vi.fn(async (_id: string, offset: number, blob: Blob) => {
    chunks.push({ offset, size: blob.size })
    return { received_bytes: offset + blob.size, duplicate: false }
  })
  const finalize = vi.fn(async () => ({ skipped: false, path: '/library/clip.mp4' }))
  const cancel = vi.fn(async () => ({ cancelled: true }))
  return {
    status,
    chunk,
    finalize,
    cancel,
    chunks,
    api: {
      uploadStatus: status,
      uploadChunk: chunk,
      uploadFinalize: finalize,
      uploadCancel: cancel,
    },
  }
}

describe('makeUploadId', () => {
  it('is stable for the same name, size and mtime', () => {
    expect(makeUploadId(makeFile('abc'))).toBe(makeUploadId(makeFile('abc')))
  })

  it('changes when the bytes change', () => {
    expect(makeUploadId(makeFile('abc'))).not.toBe(makeUploadId(makeFile('abcd')))
  })

  it('only contains characters the server accepts', () => {
    expect(makeUploadId(makeFile('abc'))).toMatch(/^[a-z0-9_-]{6,80}$/)
  })
})

describe('uploadFile', () => {
  it('sends a fresh file as one chunk at offset 0 and finalizes', async () => {
    const file = makeFile('hello world')
    const fake = fakeApi(0)
    const states: string[] = []

    const result = await uploadFile(
      file,
      fake.api,
      { folder: '/library' },
      { onProgress: (progress) => states.push(progress.state) },
    )

    expect(fake.status).toHaveBeenCalledTimes(1)
    expect(fake.status).toHaveBeenCalledWith(expect.any(String), expect.anything())
    expect(fake.chunks).toEqual([{ offset: 0, size: file.size }])
    expect(fake.finalize).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'clip.mp4',
        folder: '/library',
        total_size: file.size,
      }),
      expect.anything(),
    )
    expect(result).toEqual({ skipped: false, path: '/library/clip.mp4' })
    expect(states).toContain('uploading')
    expect(states.at(-1)).toBe('done')
  })

  it('resumes from the bytes the server already holds', async () => {
    const file = makeFile('hello world')
    const fake = fakeApi(6)

    await uploadFile(file, fake.api, { folder: '/library' }, { onProgress: () => {} })

    expect(fake.chunks).toEqual([{ offset: 6, size: file.size - 6 }])
  })

  it('does not re-send a file the server already holds completely', async () => {
    const file = makeFile('hello world')
    const fake = fakeApi(file.size)

    await uploadFile(file, fake.api, { folder: '/library' }, { onProgress: () => {} })

    expect(fake.chunks).toEqual([])
    expect(fake.finalize).toHaveBeenCalledTimes(1)
  })

  it('reports a skipped finalize, e.g. when the name collides under skip', async () => {
    const file = makeFile('hello')
    const fake = fakeApi(0)
    fake.finalize.mockResolvedValueOnce({ skipped: true, path: '/library/clip.mp4' })
    const states: string[] = []

    const result = await uploadFile(
      file,
      fake.api,
      { folder: '/library', conflict: 'skip' },
      { onProgress: (progress) => states.push(progress.state) },
    )

    expect(result.skipped).toBe(true)
    expect(states.at(-1)).toBe('skipped')
  })

  it('bubbles an aborted chunk and cancels the staged upload', async () => {
    const file = makeFile('hello world')
    const fake = fakeApi(0)
    const controller = new AbortController()
    fake.chunk.mockImplementationOnce(async () => {
      controller.abort()
      throw new DOMException('The operation was aborted', 'AbortError')
    })

    await expect(
      uploadFile(
        file,
        fake.api,
        { folder: '/library', signal: controller.signal },
        { onProgress: () => {} },
      ),
    ).rejects.toMatchObject({ name: 'AbortError' })

    expect(controller.signal.aborted).toBe(true)
    expect(fake.cancel).toHaveBeenCalledWith(expect.any(String))
  })
})
