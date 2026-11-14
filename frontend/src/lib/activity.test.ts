import { describe, expect, it } from 'vitest'
import type { ActivityItem, ActivitySummary } from './api'
import { activityGroups, activityInterval, isActiveActivity } from './activity'

function item(state: string, extra: Partial<ActivityItem> = {}): ActivityItem {
  return {
    id: state, label: state, href: '/tools', state, current: null,
    detail: '', error: null, percent: null, controls: [], job_id: null, updated_at: null, ...extra,
  }
}

describe('global activity', () => {
  it('keeps paused and deferred work active, and separates outcomes from idle workers', () => {
    const groups = activityGroups([
      item('running'), item('downloading'), item('queued'), item('deferred'), item('paused'),
      item('failed'), item('completed'), item('cancelled'),
      item('idle', { error: 'Probe failed' }),
      item('idle', { detail: 'generated: 2' }), item('idle'),
    ])
    expect(groups.active).toHaveLength(5)
    expect(groups.recent).toHaveLength(5)
    expect(groups.idle).toHaveLength(1)
    expect(isActiveActivity(item('completed'))).toBe(false)
  })

  it('polls quickly when open, slowly when idle', () => {
    const busy = { active_count: 1 } as ActivitySummary
    const idle = { active_count: 0 } as ActivitySummary
    expect(activityInterval(true, idle)).toBe(2_000)
    expect(activityInterval(false, busy)).toBe(5_000)
    expect(activityInterval(false, idle)).toBe(30_000)
    expect(activityInterval(false)).toBe(30_000)
  })
})
