import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '../ui'

interface ShortcutsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

const GROUPS: Array<{ title: string; rows: Array<[keys: string, action: string]> }> = [
  {
    title: 'Everywhere',
    rows: [
      ['⌘K / Ctrl-K', 'Open the command palette'],
      ['?', 'Show this reference'],
      ['Esc', 'Close the palette or the viewer'],
    ],
  },
  {
    title: 'Viewer',
    rows: [
      ['← / →', 'Previous or next file'],
      ['Space', 'Play or pause a video'],
      ['L', 'Toggle loop'],
      ['I', 'Show or hide details'],
    ],
  },
]

/** Keyboard reference. Only lists bindings that actually exist in v2. */
export function ShortcutsDialog({ open, onOpenChange }: ShortcutsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined} className="max-w-md">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-5">
          {GROUPS.map((group) => (
            <section key={group.title} className="flex flex-col gap-2">
              <h3 className="text-[10px] font-semibold tracking-wider text-fg-subtle uppercase">
                {group.title}
              </h3>
              <dl className="flex flex-col gap-1.5">
                {group.rows.map(([keys, action]) => (
                  <div
                    key={keys}
                    className="flex items-baseline justify-between gap-4 text-sm"
                  >
                    <dt className="shrink-0">
                      <kbd className="rounded-sm border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-fg-muted">
                        {keys}
                      </kbd>
                    </dt>
                    <dd className="text-right text-fg-muted">{action}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}

/** Small text button that opens the reference, used in Settings and More. */
export function ShortcutsButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center justify-between rounded-sm border border-border px-3 py-2.5 text-sm text-fg-muted hover:bg-surface-2"
    >
      Keyboard shortcuts
      <kbd className="rounded-sm border border-border px-1.5 py-0.5 font-mono text-[10px]">
        ?
      </kbd>
    </button>
  )
}
