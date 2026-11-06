import type { ReactNode } from 'react'
import { Link } from 'react-router'
import {
  Archive,
  Copy,
  DatabaseBackup,
  FolderX,
  Gauge,
  ImageDown,
  Wrench,
} from 'lucide-react'
import { useDedupeStatus } from '../lib/queries'
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
} from '../components/ui'

interface Tool {
  title: string
  description: string
  icon: ReactNode
  to?: string
  href?: string
  cta: string
  badge?: string
  badgeVariant?: 'accent' | 'outline' | 'warning'
}

export function ToolsPage() {
  const status = useDedupeStatus()

  const tools: Tool[] = [
    {
      title: 'Duplicates',
      description:
        'Find identical files by content and near-identical images/videos by visual hash, then keep one and remove the rest.',
      icon: <Copy />,
      to: '/tools/dedupe',
      cta: 'Review duplicates',
      badge: status.data
        ? `${status.data.content.candidate_files.toLocaleString()} candidates`
        : undefined,
      badgeVariant: 'accent',
    },
    {
      title: 'Empty folders',
      description:
        'Prune leaf folders with no media left behind by moves and deletions.',
      icon: <FolderX />,
      to: '/tools/empty',
      cta: 'Review empty folders',
    },
    {
      title: 'WebP optimizer',
      description:
        'Convert loose images to WebP atomically, with a running space-saved total. Archive members stay with the current app.',
      icon: <ImageDown />,
      to: '/tools/images',
      cta: 'Optimize images',
    },
    {
      title: 'ZIP archives',
      description:
        'Compress leaf image folders (one or all), unpack archives (one or all), convert archive images to WebP, and browse them as virtual folders in the Library.',
      icon: <Archive />,
      to: '/tools/zip',
      cta: 'Compress or unpack',
    },
    {
      title: 'Library backup',
      description:
        'Snapshot the index and cached posters into the library folder, verify the copy, download it, or restore it with the service stopped.',
      icon: <DatabaseBackup />,
      to: '/tools/backup',
      cta: 'Manage backups',
    },
    {
      title: 'Analytics',
      description:
        'Storage, media mix, quality and reclaimable space, computed from the index alone.',
      icon: <Gauge />,
      to: '/analytics',
      cta: 'Open analytics',
    },
  ]

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 px-3 py-4 sm:px-5">
      <div>
        <h1 className="flex items-center gap-2 text-base font-semibold tracking-tight">
          <Wrench aria-hidden="true" className="size-4 text-fg-subtle" />
          Tools
        </h1>
        <p className="text-xs text-fg-muted">
          Maintenance that changes the library. Dedupe is native to v2; the rest
          still runs in the current app until its phase lands.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {tools.map((tool) => (
          <Card key={tool.title}>
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="flex items-center gap-2">
                  <span className="text-fg-subtle [&_svg]:size-4">{tool.icon}</span>
                  {tool.title}
                </CardTitle>
                {tool.badge ? (
                  <Badge variant={tool.badgeVariant ?? 'outline'}>{tool.badge}</Badge>
                ) : null}
              </div>
            </CardHeader>
            <CardBody className="flex flex-col gap-3">
              <p className="text-xs leading-5 text-fg-muted">{tool.description}</p>
              {tool.to ? (
                <Link
                  to={tool.to}
                  className="w-fit text-sm font-medium text-accent hover:underline"
                >
                  {tool.cta} →
                </Link>
              ) : (
                <a
                  href={tool.href}
                  className="w-fit text-sm font-medium text-accent hover:underline"
                >
                  {tool.cta} →
                </a>
              )}
            </CardBody>
          </Card>
        ))}
      </div>
    </div>
  )
}
