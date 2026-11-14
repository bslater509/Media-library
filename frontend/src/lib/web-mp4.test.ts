import { afterEach, expect, it, vi } from 'vitest'
import { api } from './api'

afterEach(() => vi.unstubAllGlobals())

it('previews without writing and sends explicit confirmation with the preview token', async () => {
  vi.stubGlobal('window', { location: { origin: 'https://example.test' } })
  const fetcher = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ token: 'preview-token', total: 2, archives: 1, sample: [] }),
  })
  vi.stubGlobal('fetch', fetcher)
  await api.webMP4Preview()
  expect(String(fetcher.mock.calls[0][0])).toContain('/transcode/web-mp4/preview')
  expect(fetcher.mock.calls[0][1]?.method).not.toBe('POST')
  await api.webMP4Start('preview-token')
  const init = fetcher.mock.calls[1][1]
  expect(init.method).toBe('POST')
  expect(JSON.parse(init.body)).toEqual({
    token: 'preview-token',
    confirm_replace: true,
  })
  await api.webMP4Status()
  expect(String(fetcher.mock.calls[2][0])).toContain('/transcode/web-mp4/status')
  await api.webMP4Cancel()
  expect(fetcher.mock.calls[3][1].method).toBe('POST')
})
