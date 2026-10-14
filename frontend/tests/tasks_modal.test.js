import { describe, it, expect } from 'vitest';
import tasksModalHtml from '../../templates/partials/tasks_modal.html?raw';
import sorterTasksModalHtml from '../../templates/partials/sorter_tasks_modal.html?raw';
import { librarySource, sorterSource } from './sources.js';

const MODALS = [
  ['library tasks modal', tasksModalHtml],
  ['sorter tasks modal', sorterTasksModalHtml],
];

describe('Background Tasks modal — close handling', () => {
  it.each(MODALS)('%s wires the footer Close button through the shared close path', (_label, html) => {
    // The footer Close button must not hide only `#transferDropdown` inline:
    // that left the dimming `#transferBackdrop` overlay stuck on screen.
    expect(html).not.toMatch(/onclick="document\.getElementById\('transferDropdown'\)/);
    expect(html).toMatch(/data-tasks-modal-close/);
  });

  it('both pages bind the footer Close button to a handler that also hides the backdrop', () => {
    // Library uses setTransferDropdownOpen(false); the sorter hides both directly.
    expect(librarySource).toMatch(/\[data-tasks-modal-close\]/);
    expect(sorterSource).toMatch(/\[data-tasks-modal-close\]/);
    expect(librarySource).toMatch(/data-tasks-modal-close[\s\S]{0,200}setTransferDropdownOpen\(false\)/);
  });
});

describe('Background Tasks modal — accessibility contract', () => {
  it.each(MODALS)('%s exposes a labelled dialog and tablist/tabpanels', (_label, html) => {
    expect(html).toMatch(/role="dialog"/);
    expect(html).toMatch(/aria-modal="true"/);
    expect(html).toMatch(/aria-labelledby="tasksModalTitle"/);
    expect(html).toMatch(/id="tasksModalTitle"/);
    expect(html).toMatch(/role="tablist"/);
    expect(html).toMatch(/role="tab"/);
    expect(html).toMatch(/aria-selected=/);
    expect(html).toMatch(/role="tabpanel"/);
  });

  it('keeps tab aria-selected in sync with the active pane in both bundles', () => {
    [librarySource, sorterSource].forEach((src) => {
      expect(src).toMatch(/setAttribute\('aria-selected'/);
    });
  });

  it.each(MODALS)('%s shows tab labels only from md up, keeping names for assistive tech', (_label, html) => {
    // On phones the tab strip is icon-only so it never needs sideways scroll.
    ['Active & Queues', 'Background Workers', 'Tools & Maintenance'].forEach((name) => {
      expect(html).toContain(`aria-label="${name}"`);
      expect(html).toContain(`title="${name}"`);
    });
    const labelled = [...html.matchAll(/<span class="hidden md:inline">([^<]+)<\/span>/g)].map((m) => m[1]);
    expect(labelled).toEqual(['Active & Queues', 'Background Workers', 'Tools & Maintenance']);
  });
});

describe('Background Tasks modal — counts', () => {
  it('sorter Active-tab badge counts only the queue rows the pane renders', () => {
    // Previously this reused the whole task summary (which includes worker-only
    // sweeps), so the empty state hid with no matching row.
    expect(sorterSource).toMatch(/const activeQueueCount =/);
    expect(sorterSource).not.toMatch(/const activeQueueCount = tasksSummary\.count/);
  });

  it('sorter lights the Background Workers badge for duplicate hashing via the stored payload', () => {
    expect(sorterSource).toMatch(/state\.latestDupHashData/);
    expect(sorterSource).toMatch(/state\.latestThumbHashData/);
    expect(sorterSource).not.toMatch(/state\.latestHashData\b/);
    expect(sorterSource).not.toMatch(/status === "checking"/);
  });

  it('sorter updates the modal footer status line', () => {
    expect(sorterSource).toMatch(/getElementById\("tasksModalGlobalStatus"\)/);
  });
});

describe('Background Tasks modal — indexer honesty', () => {
  it.each(MODALS)('%s ships a hidden stale-index warning with a prune action', (_label, html) => {
    expect(html).toMatch(/id="indexStaleWarning"[^>]*class="hidden/);
    expect(html).toMatch(/id="indexStaleText"/);
    expect(html).toMatch(/id="indexForcePruneBtn"/);
  });

  it('both bundles warn instead of claiming "All folders indexed" when rows are stale', () => {
    [librarySource, sorterSource].forEach((src) => {
      // The stale state is driven by the payload, never by a hardcoded 100%.
      expect(src).toMatch(/stale_folders/);
      expect(src).toMatch(/const isStale =/);
      expect(src).toMatch(/Index stale/);
      expect(src).toMatch(/folders missing on mount/);
      expect(src).toMatch(/\/api\/backup\/index\/prune/);
    });
  });
});

describe('Background Tasks modal — analysis workers stay visible', () => {
  it.each(MODALS)('%s lists the metadata probe and visual hash worker cards', (_label, html) => {
    ['metaWorkerSummaryText', 'metaWorkerProgressBar', 'vhashSummaryText', 'vhashProgressBar'].forEach((id) => {
      expect(html).toContain(`id="${id}"`);
    });
  });

  it('both bundles render metadata and visual-hash progress from the status payload', () => {
    [librarySource, sorterSource].forEach((src) => {
      expect(src).toMatch(/metaWorkerPercentText/);
      expect(src).toMatch(/vhashPercentText/);
      // The metadata card surfaces the ZIP-member sweep separately.
      expect(src).toMatch(/zip_pending_count/);
    });
  });

  it('sorter header badge counts metadata probing and visual hashing', () => {
    expect(sorterSource).toMatch(/metadata: state\.latestMetadataData/);
    expect(sorterSource).toMatch(/thumbHash: state\.latestThumbHashData/);
  });
});

describe('Background Tasks modal — output escaping', () => {
  it('escapes transfer/transcode/zip/upload names before innerHTML', () => {
    expect(librarySource).toMatch(/escapeHtml\(q\.filename\)/);
    expect(librarySource).toMatch(/escapeHtml\(q\.destination_folder\)/);
    expect(librarySource).toMatch(/escapeHtml\(h\.filename\)/);
    expect(librarySource).toMatch(/escapeHtml\(t\.filename\)/);
    expect(librarySource).toMatch(/escapeHtml\(t\.message \|\| t\.status\)/);
    expect(librarySource).toMatch(/escapeHtml\(f\.relativePath\)/);
  });

  it('escapes ids embedded in inline handlers', () => {
    expect(librarySource).toMatch(/safeAttrEscape\(q\.id\)/);
    expect(librarySource).toMatch(/safeAttrEscape\(f\.id\)/);
  });
});
