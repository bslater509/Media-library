import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { AlertTriangle, ExternalLink } from 'lucide-react'
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  Spinner,
} from '../components/ui'

interface RoadmapPageProps {
  title: string
  description: string
  /** What the current app does today, shown so the page is useful, not empty. */
  available: string[]
  v1Path: string
}

/** Honest placeholder for a v1 feature whose v2 phase has not landed yet. */
export function RoadmapPage({ title, description, available, v1Path }: RoadmapPageProps) {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-6 sm:px-5">
      <div className="flex items-center gap-2">
        <h1 className="text-base font-semibold tracking-tight">{title}</h1>
        <Badge variant="warning" uppercase>
          coming in v2
        </Badge>
      </div>
      <p className="text-sm leading-6 text-fg-muted">{description}</p>

      <Card>
        <CardHeader>
          <CardTitle>Available in the current app</CardTitle>
        </CardHeader>
        <CardBody className="flex flex-col gap-3">
          <ul className="flex flex-col gap-2 text-sm text-fg-muted">
            {available.map((item) => (
              <li key={item} className="flex gap-2">
                <span className="text-accent">•</span>
                {item}
              </li>
            ))}
          </ul>
          <a
            href={v1Path}
            className="inline-flex w-fit items-center gap-1.5 text-sm font-medium text-accent hover:underline"
          >
            Open it in the current app
            <ExternalLink aria-hidden="true" className="size-3.5" />
          </a>
        </CardBody>
      </Card>

      <p className="text-xs leading-5 text-fg-subtle">
        The rewrite ships this area once it reaches parity. Until then the current app
        stays fully available — nothing is removed by the preview.
      </p>
    </div>
  )
}

export function NotFoundPage() {
  return (
    <div className="px-3 py-10 sm:px-5">
      <EmptyState
        icon={<AlertTriangle />}
        title="Page not found"
        description="That route does not exist in the v2 preview."
        action={
          <Link to="/library" className="text-sm text-accent hover:underline">
            Back to the library
          </Link>
        }
      />
    </div>
  )
}

export function PageLoading() {
  return (
    <div className="flex items-center gap-2 px-4 py-16 text-sm text-fg-subtle">
      <Spinner /> Loading…
    </div>
  )
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <p className="text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
      {children}
    </p>
  )
}
