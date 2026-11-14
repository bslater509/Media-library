import type { WebMP4Status } from './api'

export function webMP4Progress(data: WebMP4Status) {
  // Fall back during a rolling deployment to the previous server's fields.
  const checked = data.checked ?? data.processed
  const unchecked = data.unchecked ?? Math.max(0, data.total - checked)
  const pending = data.conversion_pending ?? 0
  const phases: Record<string, string> = {
    preparing: 'Preparing replacement',
    converting: 'Encoding MP4',
    verifying: 'Verifying output (full decode)',
    committing: 'Saving replacement',
  }
  const phase = phases[data.phase] ?? data.phase
  return {
    checked,
    unchecked,
    pending,
    waiting: Math.max(0, pending - (data.current ? 1 : 0)),
    checkingDone: unchecked === 0 && data.total > 0,
    checkLabel: `${checked.toLocaleString()} / ${data.total.toLocaleString()} checked`,
    queueLabel: `${pending.toLocaleString()} awaiting replacement (includes the active file)`,
    outcomeLabel: `${data.converted.toLocaleString()} replaced · ${data.skipped.toLocaleString()} already compatible · ${data.failed.toLocaleString()} failed`,
    phase,
    showPhaseProgress: data.phase === 'converting' || data.phase === 'verifying',
  }
}

export function videoName(path: string) {
  return path.split(/[/\\]/).pop() || path
}

export function webMP4Failure(error: string) {
  const text = error.toLowerCase()
  if (text.includes('moov atom not found')) {
    return { title: 'Missing MP4 metadata', explanation: 'The file appears incomplete or damaged. Restore or re-download it before retrying.' }
  }
  if (text.includes('ffprobe') && (text.includes('non-zero exit') || text.includes('rejected') || text.includes('could not read'))) {
    return { title: 'Could not read video metadata', explanation: 'The file may be damaged or unreadable on the remote drive. It was skipped, not replaced.' }
  }
  if (text.includes('timed out') || text.includes('timeoutexpired')) {
    return { title: 'Reading the file timed out', explanation: 'The remote drive may have been slow or unavailable. This file can be retried later.' }
  }
  if (text.includes('verification failed')) {
    return { title: 'New MP4 did not pass verification', explanation: 'The new output was rejected; the original was retained.' }
  }
  if (text.includes('already exists') || text.includes('destination changed')) {
    return { title: 'Destination filename conflict', explanation: 'The converter refused to overwrite an existing or changed destination.' }
  }
  return { title: 'File could not be processed', explanation: 'This file was skipped. Open the technical details or download the failure log to investigate.' }
}
