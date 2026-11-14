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
    checkingDone: unchecked === 0 && data.total > 0,
    checkLabel: `${checked.toLocaleString()} / ${data.total.toLocaleString()} checked`,
    queueLabel: `${pending.toLocaleString()} awaiting replacement (includes the active file)`,
    outcomeLabel: `${data.converted.toLocaleString()} replaced · ${data.skipped.toLocaleString()} already compatible · ${data.failed.toLocaleString()} failed`,
    phase,
    showPhaseProgress: data.phase === 'converting' || data.phase === 'verifying',
  }
}
