import { afterEach, expect, it, vi } from 'vitest'
import { api } from './api'
import type { WebMP4Status } from './api'
import { webMP4Progress } from './web-mp4'

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
  await api.webMP4Resume()
  expect(String(fetcher.mock.calls[4][0])).toContain('/transcode/web-mp4/resume')
  expect(fetcher.mock.calls[4][1].method).toBe('POST')
})

it('shows independent check totals, queued replacements and per-phase encoding', () => {
  const progress = webMP4Progress({
    total: 6650, processed: 577, checked: 900, unchecked: 5750,
    conversion_pending: 323, converted: 0, skipped: 576, failed: 1,
    phase: 'converting',
  } as WebMP4Status)
  expect(progress.checkLabel).toBe('900 / 6,650 checked')
  expect(progress.queueLabel).toBe('323 awaiting replacement (includes the active file)')
  expect(progress.phase).toBe('Encoding MP4')
  expect(progress.showPhaseProgress).toBe(true)
  expect(progress.checkingDone).toBe(false)
  expect(progress.outcomeLabel).toBe('0 replaced · 576 already compatible · 1 failed')
})

it('reports checking complete even while replacements remain and tolerates old servers', () => {
  const status = {
    total: 40, processed: 0, checked: 40, unchecked: 0,
    conversion_pending: 40, converted: 0, skipped: 0, failed: 0,
    phase: 'verifying',
  } as WebMP4Status
  expect(webMP4Progress(status).checkingDone).toBe(true)
  expect(webMP4Progress(status).phase).toBe('Verifying output (full decode)')
  const old = { total: 40, processed: 3, converted: 0, skipped: 3, failed: 0, phase: '' } as WebMP4Status
  expect(webMP4Progress(old).checked).toBe(3)
  expect(webMP4Progress(old).unchecked).toBe(37)
  expect(webMP4Progress(old).pending).toBe(0)
  expect(webMP4Progress({ ...status, phase: 'committing' }).showPhaseProgress).toBe(false)
})
