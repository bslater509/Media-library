import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { formatBytes } from '../lib/format'
import * as echarts from 'echarts/core'
import type { EChartsCoreOption } from 'echarts/core'
import { BarChart, PieChart, TreemapChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { LegacyGridContainLabel } from 'echarts/features'
import { CanvasRenderer } from 'echarts/renderers'
import {
  AlertTriangle,
  Clock,
  ExternalLink,
  FolderOpen,
  Gauge,
  HardDrive,
  Image as ImageIcon,
  RefreshCw,
  Video,
} from 'lucide-react'
import type { AnalyticsOverview, AnalyticsTreeNode, FileEntry } from '../lib/api'
import { cn } from '../lib/cn'
import { useAnalyticsOverview } from '../lib/queries'
import { useThemeStore } from '../lib/stores'
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  Progress,
  Skeleton,
  Spinner,
} from '../components/ui'

echarts.use([
  BarChart,
  PieChart,
  TreemapChart,
  GridComponent,
  TooltipComponent,
  LegacyGridContainLabel,
  CanvasRenderer,
])

function useChartColors() {
  const mode = useThemeStore((state) => state.mode)
  return useMemo(() => {
    void mode
    const styles =
      typeof window === 'undefined'
        ? null
        : getComputedStyle(document.documentElement)
    const read = (name: string, fallback: string) =>
      styles?.getPropertyValue(name).trim() || fallback
    return {
      accent: read('--accent', '#2563eb'),
      surface: read('--surface', '#ffffff'),
      fg: read('--fg', '#17181b'),
      muted: read('--fg-muted', '#5b5f68'),
      subtle: read('--fg-subtle', '#8b8f98'),
      border: read('--border', '#e6e7ea'),
      info: read('--info', '#0369a1'),
      warning: read('--warning', '#b45309'),
      success: read('--success', '#15803d'),
    }
  }, [mode])
}

interface ChartProps {
  option: EChartsCoreOption
  height?: number
  onNode?: (data: Record<string, unknown>) => void
}

function Chart({ option, height = 320, onNode }: ChartProps) {
  const ref = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null)

  useEffect(() => {
    const element = ref.current
    if (!element) return
    const chart = echarts.init(element)
    chartRef.current = chart
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(element)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, true)
  }, [option])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !onNode) return
    const handler = (params: unknown) => {
      const data = (params as { data?: Record<string, unknown> }).data
      if (data) onNode(data)
    }
    chart.on('click', handler)
    return () => {
      chart.off('click', handler)
    }
  }, [onNode])

  return <div ref={ref} className="w-full" style={{ height }} />
}

export function AnalyticsPage() {
  const navigate = useNavigate()
  const colors = useChartColors()
  const [nonce, setNonce] = useState(0)
  const query = useAnalyticsOverview(nonce)
  const data = query.data

  const openFolder = (path: unknown) => {
    if (typeof path === 'string' && path) {
      navigate(`/library?path=${encodeURIComponent(path)}`)
    }
  }

  // ECharts tooltip formatters are injected as HTML; names come from the
  // filesystem, so escape them before they enter a tooltip string.
  const escapeHtml = (value: string) =>
    value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')

  const treemapOption = useMemo(() => {
    const nodes = data?.folders.top ?? []
    return {
      backgroundColor: 'transparent',
      tooltip: {
        formatter: (params: { name: string; value: number }) =>
          `${escapeHtml(params.name)}<br/>${formatBytes(params.value)}`,
        backgroundColor: colors.surface,
        borderColor: colors.border,
        textStyle: { color: colors.fg, fontSize: 11 },
      },
      series: [
        {
          type: 'treemap',
          roam: true,
          scaleLimit: { min: 0.6, max: 8 },
          breadcrumb: { show: false },
          nodeClick: false,
          width: '100%',
          height: '100%',
          label: {
            show: true,
            formatter: '{b}',
            color: '#ffffff',
            fontSize: 11,
            textBorderColor: 'rgba(0,0,0,0.45)',
            textBorderWidth: 2,
          },
          upperLabel: { show: false },
          itemStyle: {
            borderColor: colors.surface,
            borderWidth: 2,
            gapWidth: 2,
          },
          levels: [
            { itemStyle: { borderWidth: 0, gapWidth: 2 } },
            { colorSaturation: [0.32, 0.55] },
          ],
          data: nodes.map((node: AnalyticsTreeNode) => ({
            name: node.name,
            value: node.bytes,
            path: node.is_group ? '' : node.path,
            is_group: node.is_group,
            children: (node.children ?? []).map((child) => ({
              name: child.name,
              value: child.bytes,
              path: child.path,
            })),
          })),
        },
      ],
    }
  }, [data?.folders.top, colors])

  const mixOption = useMemo(() => {
    const items = data?.by_type ?? []
    const palette = [colors.accent, colors.info, colors.warning, colors.success]
    return {
      backgroundColor: 'transparent',
      tooltip: {
        formatter: (params: { name: string; value: number; percent: number }) =>
          `${escapeHtml(params.name)}: ${formatBytes(params.value)} (${params.percent}%)`,
        backgroundColor: colors.surface,
        borderColor: colors.border,
        textStyle: { color: colors.fg, fontSize: 11 },
      },
      series: [
        {
          type: 'pie',
          radius: ['58%', '82%'],
          center: ['50%', '50%'],
          label: { show: false },
          itemStyle: { borderColor: colors.surface, borderWidth: 2 },
          data: items.map((item, index) => ({
            name: item.type,
            value: item.bytes,
            itemStyle: { color: palette[index % palette.length] },
          })),
        },
      ],
    }
  }, [data?.by_type, colors])

  const extensionsOption = useMemo(() => {
    const items = (data?.by_extension.items ?? []).slice(0, 9)
    return {
      backgroundColor: 'transparent',
      grid: { left: 8, right: 18, top: 8, bottom: 8, containLabel: true },
      tooltip: {
        formatter: (params: { name: string; value: number }) =>
          `${escapeHtml(params.name)}: ${formatBytes(params.value)}`,
        backgroundColor: colors.surface,
        borderColor: colors.border,
        textStyle: { color: colors.fg, fontSize: 11 },
      },
      xAxis: { type: 'value', show: false },
      yAxis: {
        type: 'category',
        data: items.map((item) => item.ext || 'none').reverse(),
        axisLabel: { color: colors.muted, fontSize: 10 },
        axisTick: { show: false },
        axisLine: { show: false },
      },
      series: [
        {
          type: 'bar',
          data: items.map((item) => item.bytes).reverse(),
          barWidth: '58%',
          itemStyle: { color: colors.accent, borderRadius: [0, 3, 3, 0] },
        },
      ],
    }
  }, [data?.by_extension.items, colors])

  if (query.isLoading) {
    return (
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-3 py-4 sm:px-5">
        <Skeleton className="h-9 w-64" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
          {[0, 1, 2, 3, 4, 5].map((item) => (
            <Skeleton key={item} className="h-20" />
          ))}
        </div>
        <Skeleton className="h-80" />
      </div>
    )
  }

  if (query.isError || !data) {
    return (
      <div className="px-3 py-10 sm:px-5">
        <EmptyState
          icon={<AlertTriangle />}
          title="Analytics could not load"
          description={
            query.error instanceof Error ? query.error.message : 'The index did not answer.'
          }
          action={
            <Button variant="secondary" onClick={() => setNonce((value) => value + 1)}>
              Try again
            </Button>
          }
        />
      </div>
    )
  }

  const totalBytes = data.totals.bytes || 1
  const maxQuality = Math.max(1, ...data.quality.buckets.map((bucket) => bucket.count))

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-3 py-4 sm:px-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-base font-semibold tracking-tight">Analytics</h1>
          <p className="text-xs text-fg-muted">
            Index-only overview of {data.totals.folders.toLocaleString()} folders ·{' '}
            {data.totals.files.toLocaleString()} files · updated{' '}
            {new Date(data.generated_at * 1000).toLocaleTimeString()}
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => setNonce((value) => value + 1)}
          disabled={query.isFetching}
        >
          <RefreshCw className={cn(query.isFetching && 'animate-spin')} /> Refresh
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        <Kpi label="Library size" value={data.totals.size_formatted} icon={<HardDrive />} />
        <Kpi label="Files" value={data.totals.files.toLocaleString()} icon={<FolderOpen />} />
        <Kpi label="Videos" value={data.totals.videos.toLocaleString()} icon={<Video />} />
        <Kpi label="Images" value={data.totals.images.toLocaleString()} icon={<ImageIcon />} />
        <Kpi label="Folders" value={data.totals.folders.toLocaleString()} icon={<FolderOpen />} />
        <Kpi
          label="Runtime"
          value={data.totals.runtime_formatted}
          icon={<Clock />}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <div className="flex items-center justify-between gap-2">
              <CardTitle>Storage by folder</CardTitle>
              <span className="text-[11px] text-fg-subtle">
                pinched/scroll to zoom · click to open
              </span>
            </div>
          </CardHeader>
          <CardBody>
            <Chart
              option={treemapOption}
              height={380}
              onNode={(node) => openFolder(node.path)}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Largest folders</CardTitle>
          </CardHeader>
          <CardBody className="flex flex-col gap-2.5">
            {data.folders.largest.slice(0, 10).map((folder) => (
              <button
                key={folder.path}
                type="button"
                onClick={() => openFolder(folder.path)}
                className="group flex flex-col gap-1 text-left"
              >
                <div className="flex items-baseline justify-between gap-2 text-sm">
                  <span className="min-w-0 truncate text-fg group-hover:text-accent">
                    {folder.name}
                  </span>
                  <span className="shrink-0 font-mono text-[11px] text-fg-subtle tabular-nums">
                    {formatBytes(folder.bytes)}
                  </span>
                </div>
                <Progress value={(folder.bytes / totalBytes) * 100} />
              </button>
            ))}
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Media mix</CardTitle>
          </CardHeader>
          <CardBody className="flex flex-col gap-3">
            <Chart option={mixOption} height={200} />
            <div className="flex flex-col gap-1.5">
              {data.by_type.map((item) => (
                <div
                  key={item.type}
                  className="flex items-center justify-between gap-2 text-xs"
                >
                  <span className="capitalize text-fg-muted">{item.type}</span>
                  <span className="font-mono text-[11px] text-fg-subtle tabular-nums">
                    {item.files.toLocaleString()} · {formatBytes(item.bytes)} ·{' '}
                    {item.share}%
                  </span>
                </div>
              ))}
            </div>
          </CardBody>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Top extensions by size</CardTitle>
          </CardHeader>
          <CardBody>
            <Chart option={extensionsOption} height={260} />
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Reclaimable space</CardTitle>
        </CardHeader>
        <CardBody className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Metric
            label="Duplicate candidates"
            value={data.reclaimable.duplicates.size_formatted}
            hint={`${data.reclaimable.duplicates.groups.toLocaleString()} groups · ${data.reclaimable.duplicates.files.toLocaleString()} files`}
          />
          <Metric
            label="Saved by WebP"
            value={data.reclaimable.webp.saved_formatted}
            hint={`${data.reclaimable.webp.converted.toLocaleString()} converted · ${data.reclaimable.webp.pending.toLocaleString()} still to go`}
          />
          <Metric
            label="Empty folders"
            value={data.reclaimable.empty_folders.toLocaleString()}
            hint="No files and no subfolders"
          />
        </CardBody>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Largest files</CardTitle>
          </CardHeader>
          <CardBody className="flex flex-col gap-1">
            {data.largest_files.map((file) => (
              <LargestFile
                key={file.path}
                file={file}
                onOpen={() =>
                  navigate(
                    `/library?path=${encodeURIComponent(file.folder_path)}&open=${encodeURIComponent(file.path)}`,
                  )
                }
              />
            ))}
          </CardBody>
        </Card>

        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle>Quality &amp; runtime</CardTitle>
            </CardHeader>
            <CardBody className="flex flex-col gap-2">
              {data.quality.buckets.map((bucket) => (
                <div key={bucket.label} className="flex items-center gap-2 text-xs">
                  <span className="w-16 shrink-0 text-fg-muted">{bucket.label}</span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface-3">
                    <div
                      className="h-full rounded-full bg-accent"
                      style={{ width: `${(bucket.count / maxQuality) * 100}%` }}
                    />
                  </div>
                  <span className="w-10 shrink-0 text-right font-mono text-[11px] text-fg-subtle tabular-nums">
                    {bucket.count.toLocaleString()}
                  </span>
                </div>
              ))}
              <p className="pt-1 text-[11px] leading-5 text-fg-subtle">
                {data.quality.probed.toLocaleString()} probed ·{' '}
                {data.quality.high_fps.toLocaleString()} at 50 fps or higher ·{' '}
                {data.quality.runtime_formatted} total runtime
              </p>
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Gauge aria-hidden="true" className="size-4 text-fg-subtle" />
                Health
              </CardTitle>
            </CardHeader>
            <CardBody className="flex flex-wrap gap-1.5">
              <HealthPill label="Missing thumbnails" value={data.health.missing_thumbnails} />
              <HealthPill label="Unprobed" value={data.health.unprobed} />
              <HealthPill label="No visual hash" value={data.health.missing_visual_hashes} />
              <HealthPill label="No animated preview" value={data.health.missing_animated} />
              <HealthPill label="Needs transcode" value={data.health.needs_transcode} />
              <HealthPill label="Ignored pairs" value={data.health.ignored_pairs} />
            </CardBody>
          </Card>
        </div>
      </div>

      <p className="pb-2 text-center text-[11px] text-fg-subtle">
        Every number comes from the SQLite index — no folder walk, no remote stat.{' '}
        <a href="/tools" className="inline-flex items-center gap-1 text-accent hover:underline">
          Clean up in Tools <ExternalLink className="size-3" />
        </a>
      </p>
    </div>
  )
}

function Kpi({
  label,
  value,
  icon,
}: {
  label: string
  value: string
  icon: ReactNode
}) {
  return (
    <div className="flex flex-col gap-1 rounded-md border border-border bg-surface px-3 py-2.5">
      <span className="flex items-center gap-1.5 text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
        <span className="[&_svg]:size-3.5">{icon}</span>
        {label}
      </span>
      <span className="font-mono text-sm font-semibold text-fg tabular-nums">{value}</span>
    </div>
  )
}

function Metric({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="rounded-md border border-border bg-surface-2 px-3 py-2.5">
      <p className="text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
        {label}
      </p>
      <p className="font-mono text-sm font-semibold">{value}</p>
      <p className="text-[11px] text-fg-subtle">{hint}</p>
    </div>
  )
}

function LargestFile({ file, onOpen }: { file: FileEntry; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex items-center gap-2 rounded-sm px-1.5 py-1.5 text-left hover:bg-surface-2"
    >
      <span className="size-9 shrink-0 overflow-hidden rounded-sm bg-surface-3">
        {file.has_thumb ? (
          <img
            src={file.thumb_url}
            alt=""
            loading="lazy"
            className="h-full w-full object-cover"
          />
        ) : (
          <span className="flex h-full w-full items-center justify-center text-fg-subtle">
            {file.type === 'video' ? (
              <Video className="size-4" />
            ) : (
              <ImageIcon className="size-4" />
            )}
          </span>
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs text-fg">{file.name}</span>
        <span className="block truncate text-[10px] text-fg-subtle">
          {file.size_formatted} · {file.width && file.height ? `${file.width}×${file.height}` : 'unprobed'} ·{' '}
          {file.folder_path}
        </span>
      </span>
    </button>
  )
}

function HealthPill({ label, value }: { label: string; value: number }) {
  return (
    <Badge variant={value > 0 ? 'warning' : 'success'}>
      {label}: {value.toLocaleString()}
    </Badge>
  )
}
