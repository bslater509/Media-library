import type { ActivityItem, ActivitySummary } from './api'

const ACTIVE = new Set(['running', 'downloading', 'working', 'queued', 'deferred', 'paused'])

export function isActiveActivity(item: ActivityItem) {
  return ACTIVE.has(item.state)
}

export function activityGroups(items: ActivityItem[]) {
  return {
    active: items.filter(isActiveActivity),
    recent: items.filter((item) => !isActiveActivity(item) && (item.state !== 'idle' || item.error || item.detail)),
    idle: items.filter((item) => !isActiveActivity(item) && item.state === 'idle' && !item.error && !item.detail),
  }
}

export function activityInterval(open: boolean, data?: ActivitySummary) {
  return open ? 2_000 : data?.active_count ? 5_000 : 30_000
}
