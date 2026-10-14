import { describe, it, expect } from 'vitest';
import tasksModalHtml from '../../templates/partials/tasks_modal.html?raw';
import sorterTasksModalHtml from '../../templates/partials/sorter_tasks_modal.html?raw';
import librarySource from '../src/library/index.js?raw';
import sorterSource from '../src/app/index.js?raw';

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
