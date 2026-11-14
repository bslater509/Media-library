import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { WebMP4Progress } from '../components/manage/WebMP4Progress'
import type { WebMP4Status } from './api'
import { videoName, webMP4Failure } from './web-mp4'

function status(changes: Partial<WebMP4Status> = {}): WebMP4Status {
  return {
    status: 'running', total: 6650, processed: 5938, converted: 3,
    skipped: 5932, failed: 3, current: '/library/Current video.mp4',
    percent: 27.1, phase: 'converting', errors: [], cancel_requested: false,
    checking: [], checker_concurrency: 4, cached: 13, remaining: 712,
    persistence_error: null, checked: 6650, unchecked: 0,
    conversion_pending: 712, failure_log_error: null,
    ...changes,
  }
}

function render(changes: Partial<WebMP4Status> = {}) {
  return renderToStaticMarkup(createElement(WebMP4Progress, { data: status(changes) }))
}

describe('MP4 conversion explanations', () => {
  it('leads with this video’s percentage, distinct from a finished library check', () => {
    const html = render()
    expect(html).toContain('27.1%')
    expect(html).toContain('Converting this video')
    expect(html).toContain('Current video.mp4')
    expect(html).toContain('Step 1 of 3')
    expect(html).toContain('for this video only, not the whole library')
    expect(html).toContain('Library check complete')
    expect(html).toContain('Checking is not conversion')
    expect(html).toContain('711</strong> waiting + 1 active')
    expect(html).toContain('successfully replaced')
    expect(html).toContain('aria-valuenow="27.1"')
  })

  it('explains why verification starts a separate percentage', () => {
    const html = render({ phase: 'verifying', percent: 8 })
    expect(html).toContain('Step 2 of 3')
    expect(html).toContain('8.0%')
    expect(html).toContain('Encoding is finished')
    expect(html).toContain('separate 0–100% step')
  })

  it('does not show invented percentages while preparing or saving', () => {
    const html = render({ phase: 'committing', percent: 99 })
    expect(html).toContain('Step 3 of 3')
    expect(html).toContain('Verification passed')
    expect(html).not.toContain('99.0%')
    expect(html).not.toContain('role="progressbar"')
  })

  it('labels individual errors as skipped files, not a failed current conversion', () => {
    const html = render({
      errors: [{ path: '/library/Bad.mp4', error: "Command '['ffprobe']' returned non-zero exit status 1." }],
    })
    expect(html).toContain('3 files need attention — batch continues')
    expect(html).toContain('do not stop the current conversion')
    expect(html).toContain('Could not read video metadata')
    expect(html).toContain('It was skipped, not replaced')
    expect(html).toContain('Technical details and path')
    expect(html).not.toContain('role="alert"')
  })

  it('reserves alerts for actual progress-saving problems and escapes filenames', () => {
    const html = render({ persistence_error: 'Cannot save state', current: '/library/<script>video.mp4' })
    expect(html).toContain('role="alert"')
    expect(html).toContain('Progress-saving problem')
    expect(html).toContain('&lt;script&gt;video.mp4')
    expect(html).not.toContain('<script>video')
  })

  it('explains waiting, active scanning and completed work without an active video', () => {
    expect(render({ current: null })).toContain('Waiting to start the next conversion')
    const scanning = render({ current: null, conversion_pending: 0, checked: 100, unchecked: 6550 })
    expect(scanning).toContain('Checking the library')
    expect(scanning).toContain('left to check')
    expect(render({ current: null, status: 'completed', conversion_pending: 0 })).toContain('No replacements are waiting')
  })

  it('gives short, specific failure explanations without pretending to know an old diagnostic', () => {
    expect(webMP4Failure('moov atom not found').title).toBe('Missing MP4 metadata')
    expect(webMP4Failure('FFprobe timed out').title).toContain('timed out')
    expect(webMP4Failure('Output verification failed').title).toContain('verification')
    expect(webMP4Failure('MP4 filename already exists').title).toContain('conflict')
    expect(webMP4Failure('unclassified failure').title).toBe('File could not be processed')
    expect(videoName('/folder/clip.mp4')).toBe('clip.mp4')
    expect(videoName('')).toBe('')
  })
})
