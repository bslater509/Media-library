import { pollSystemStatus } from './tasks.js';

// Shared helpers for capped "show all" lists inside mobile sheets.
// Extracted from the former monolithic library index.js.

const expandedSheetLists = new Set();
function sheetListSlice(listKey, total, sliceCount) {
  return expandedSheetLists.has(listKey) ? total : Math.min(total, sliceCount);
}
function renderListExpander(listKey, total, sliceCount) {
  if (total <= sliceCount) return '';
  const expanded = expandedSheetLists.has(listKey);
  return `<button type="button" class="ml-list-expander" data-expand-list="${listKey}" data-total="${total}" data-slice="${sliceCount}" aria-expanded="${expanded}">${expanded ? 'Show fewer ↑' : `Show all ${total} ↓`}</button>`;
}
function toggleSheetList(listKey, total, sliceCount) {
  if (expandedSheetLists.has(listKey)) expandedSheetLists.delete(listKey);
  else expandedSheetLists.add(listKey);
  pollSystemStatus(true);
}

export { expandedSheetLists, renderListExpander, sheetListSlice, toggleSheetList };
