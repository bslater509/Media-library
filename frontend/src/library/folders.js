import { icon } from '../shared/icons.js';
import { els, persistExpandedFolders, state } from './state.js';
import { escapeHtml, showToast } from './utils.js';
import { applyFiltersAndSort } from './gallery.js';
import { gridGapPx, gridWidth, layoutJustifiedRows, subfolderColumnCount, targetRowHeight, tileRatio } from './layout.js';
import { attachLongPress, clearSelection, openContextMenu } from './management.js';
import { pollTranscodeStatus } from './tasks.js';

// Folder navigation: sidebar tree, breadcrumbs and subfolder grid.
// Extracted from the former monolithic library index.js.

// Mobile Sidebar Drawer
function setSidebarOpen(open) {
  if (!els.sidebarDrawer || !els.sidebarBackdrop) return;
  if (open) {
    els.sidebarDrawer.classList.remove('-translate-x-full');
    els.sidebarBackdrop.classList.remove('hidden');
  } else {
    els.sidebarDrawer.classList.add('-translate-x-full');
    els.sidebarBackdrop.classList.add('hidden');
  }
}
// Load Folder Contents
async function loadFolder(targetPath, pushHistory = true, forceRefresh = false, silent = false) {
  try {
    state.isFavoritesView = false;
    state.searchTruncated = false;
    if (els.searchTruncationBanner) els.searchTruncationBanner.classList.add('hidden');
    if (pushHistory) {
      state.indexRetryCount = 0;
      clearTimeout(state.indexRetryTimer);
      if (els.mainStage) {
        els.mainStage.scrollTop = 0;
      }
    }
    if (!silent) {
      els.emptyState.classList.add('hidden');
      els.mediaGrid.innerHTML = `
        <div class="w-full py-16 flex flex-col items-center justify-center gap-3 text-slate-400">
          <span class="w-7 h-7 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin"></span>
          <span class="text-xs">Loading media files...</span>
        </div>
      `;
    }

    let url = targetPath 
      ? `/api/backup/browse?path=${encodeURIComponent(targetPath)}` 
      : `/api/backup/browse`;
    if (forceRefresh) {
      url += (url.includes('?') ? '&' : '?') + 'refresh=true';
    }

    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) {
      throw new Error(`Failed to load directory (${res.status})`);
    }

    const data = await res.json();
    state.basePath = data.base_path;
    state.currentPath = data.current_path;
    state.isZip = data.is_zip || false;
    state.zipExtracting = data.zip_extracting || false;
    state.rootFolders = data.root_folders || [];
    state.subfolders = data.subfolders || [];
    state.breadcrumbs = data.breadcrumbs || [];
    state.allFiles = data.files || [];
    clearSelection();

    // Folder was not yet indexed: the server kicked off background indexing and
    // returned the cached view immediately. Re-poll (silently) until it's ready.
    if (data.indexing) {
      state.indexRetryCount += 1;
      clearTimeout(state.indexRetryTimer);
      if (state.indexRetryCount <= 20) {
        state.indexRetryTimer = setTimeout(() => {
          loadFolder(data.current_path, false, false, true);
        }, 700);
      }
    } else {
      state.indexRetryCount = 0;
      clearTimeout(state.indexRetryTimer);
    }

    // Update subtitle & badges
    els.headerPathSubtitle.textContent = data.current_path;
    els.headerPathSubtitle.title = data.current_path;

    // Update URL query string
    if (pushHistory) {
      const newUrl = data.is_root ? '/library' : `/library?folder=${encodeURIComponent(data.current_path)}`;
      history.pushState({ path: data.current_path }, '', newUrl);
    }

    // Handle zip extraction progress banner
    if (data.is_zip && data.zip_extracting) {
      if (els.zipExtractBanner) {
        els.zipExtractBanner.classList.remove('hidden');
        const pct = Math.round(data.zip_extract_percent || 0);
        els.zipExtractPercent.textContent = `${pct}%`;
        els.zipExtractProgressBar.style.width = `${pct}%`;
      }
    } else {
      if (els.zipExtractBanner) {
        els.zipExtractBanner.classList.add('hidden');
      }
    }

    // Render components
    syncTreeToCurrentPath();
    renderSidebarTree();
    renderBreadcrumbs();
    renderSubfolders();
    applyFiltersAndSort();

    // Trigger immediate transcode check
    pollTranscodeStatus();

    // Close mobile drawer after selecting
    if (window.innerWidth < 768) {
      setSidebarOpen(false);
    }
  } catch (err) {
    console.error('Error loading folder:', err);
    showToast(err.message || 'Error reading folder', 'error');
    els.mediaGrid.innerHTML = '';
    els.emptyState.classList.remove('hidden');
    els.emptyStateMsg.textContent = 'Could not access this folder or directory on pCloud.';
  }
}

// Load Global Favorites View
async function loadFavoritesView(pushHistory = true) {
  try {
    state.isFavoritesView = true;
    state.searchTruncated = false;
    if (els.searchTruncationBanner) els.searchTruncationBanner.classList.add('hidden');
    if (pushHistory) {
      state.indexRetryCount = 0;
      clearTimeout(state.indexRetryTimer);
      if (els.mainStage) els.mainStage.scrollTop = 0;
      history.pushState({ path: '__favorites__' }, '', '/library?view=favorites');
    }
    els.emptyState.classList.add('hidden');
    els.mediaGrid.innerHTML = `
      <div class="w-full py-16 flex flex-col items-center justify-center gap-3 text-slate-400">
        <span class="w-7 h-7 border-2 border-amber-500 border-t-transparent rounded-full animate-spin"></span>
        <span class="text-xs">Loading favorites...</span>
      </div>
    `;

    const res = await fetch('/api/backup/favorites', { cache: 'no-store' });
    if (!res.ok) throw new Error(`Failed to load favorites (${res.status})`);
    const data = await res.json();

    state.currentPath = '';
    state.isZip = false;
    state.zipExtracting = false;
    state.subfolders = [];
    state.breadcrumbs = [
      { name: 'Library', path: '' },
      { name: '⭐ Favorites', path: '__favorites__' },
    ];

    const baseDir = state.basePath || '';
    state.allFiles = (data.files || []).map((f) => {
      const fDir = f.folder_path || '';
      const rel = fDir.startsWith(baseDir) ? fDir.slice(baseDir.length).replace(/^[/\\]+/, '') : fDir;
      return {
        ...f,
        is_favorite: true,
        rel_folder: rel,
        folder_name: fDir.split(/[/\\]/).filter(Boolean).pop() || fDir,
      };
    });
    clearSelection();

    if (els.headerPathSubtitle) {
      els.headerPathSubtitle.textContent = '⭐ Favorites Collection';
      els.headerPathSubtitle.title = 'All favorited files across your library';
    }

    if (els.sidebarFavoritesBadge) {
      els.sidebarFavoritesBadge.textContent = data.counts ? data.counts.all : state.allFiles.length;
    }

    renderSidebarTree();
    renderBreadcrumbs();
    renderSubfolders();
    applyFiltersAndSort();

    if (window.innerWidth < 768) {
      setSidebarOpen(false);
    }
  } catch (err) {
    console.error('Error loading favorites:', err);
    showToast(err.message || 'Error loading favorites', 'error');
  }
}

async function fetchFolderChildren(path) {
  const res = await fetch(`/api/backup/folders?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`Failed to load subfolders (${res.status})`);
  return await res.json();
}
// Ensure a folder's children are loaded into state.folderChildren[path].
function ensureFolderChildren(path) {
  const entry = state.folderChildren[path] || { children: [], loaded: false, loading: false, retries: 0 };
  state.folderChildren[path] = entry;
  if (entry.loaded || entry.loading) return;

  entry.loading = true;
  (async () => {
    try {
      const data = await fetchFolderChildren(path);
      entry.children = data.subfolders || [];
      if (data.indexing && entry.retries < 20) {
        entry.retries += 1;
        entry.loading = false;
        setTimeout(() => ensureFolderChildren(path), 700);
      } else {
        entry.loaded = true;
        entry.loading = false;
      }
    } catch (err) {
      entry.loading = false;
      console.error('Error loading folder children for tree:', err);
    }
    renderSidebarTree();
  })();
}
function toggleFolderExpand(path) {
  if (state.expandedFolders.has(path)) {
    state.expandedFolders.delete(path);
  } else {
    state.expandedFolders.add(path);
    ensureFolderChildren(path);
  }
  persistExpandedFolders(state.expandedFolders);
  renderSidebarTree();
}
// Expand and lazy-load the ancestor chain of the folder currently shown.
function syncTreeToCurrentPath() {
  if (!state.basePath) return;

  // Roots live under the base path.
  const baseEntry = state.folderChildren[state.basePath] || { children: [], loaded: false, loading: false, retries: 0 };
  baseEntry.children = state.rootFolders || [];
  baseEntry.loaded = true;
  baseEntry.loading = false;
  state.folderChildren[state.basePath] = baseEntry;

  // The currently shown folder's children are already known from browse.
  if (state.currentPath && state.currentPath !== state.basePath) {
    state.folderChildren[state.currentPath] = {
      children: state.subfolders || [],
      loaded: true,
      loading: false,
      retries: 0,
    };
  }

  // Expand ancestors (skip index 0 = base; the last crumb is current, whose
  // children were set just above, so only intermediates need lazy loading).
  (state.breadcrumbs || []).forEach((crumb, idx) => {
    if (idx === 0) return;
    state.expandedFolders.add(crumb.path);
    if (idx < state.breadcrumbs.length - 1) {
      ensureFolderChildren(crumb.path);
    }
  });
  persistExpandedFolders(state.expandedFolders);
}
function hasMatchingDescendant(folder, q) {
  const entry = state.folderChildren[folder.path];
  if (!entry || !entry.loaded) return false;
  return entry.children.some(c =>
    c.name.toLowerCase().includes(q) || hasMatchingDescendant(c, q)
  );
}
function renderSidebarTree() {
  const container = els.rootFolderList;
  const base = state.basePath;
  const q = state.folderSearchQuery.toLowerCase().trim();

  const roots = (base && state.folderChildren[base] && state.folderChildren[base].loaded)
    ? state.folderChildren[base].children
    : (state.rootFolders || []);

  els.folderCountBadge.textContent = `${roots.length} folders`;

  if (roots.length === 0) {
    container.innerHTML = `<div class="text-center py-6 text-xs text-slate-500">No matching folders</div>`;
    return;
  }

  const currentNorm = state.currentPath.toLowerCase();
  const rows = [];

  const renderChildren = (list, depth) => {
    for (const folder of list) {
      const pathKey = folder.path;
      const entry = state.folderChildren[pathKey];
      const children = entry && entry.loaded ? entry.children : [];
      const childCount = folder.subfolder_count || 0;
      const hasChildren = childCount > 0 || (entry && entry.loaded && children.length > 0);

      const nameMatches = !q || folder.name.toLowerCase().includes(q);
      const descMatches = q ? hasMatchingDescendant(folder, q) : false;
      if (q && !nameMatches && !descMatches) continue;

      const isZip = folder.is_zip || (folder.path && folder.path.toLowerCase().endsWith('.zip'));
      const icon = isZip ? '📦' : '📁';
      const count = folder.file_count || 0;

      const isActive = currentNorm === folder.path.toLowerCase() || currentNorm.startsWith(folder.path.toLowerCase() + '/');
      const activeClass = isActive
        ? 'bg-emerald-600/20 text-emerald-300 font-semibold border-emerald-500/40 shadow-sm'
        : 'text-slate-300 hover:bg-slate-800/80 hover:text-white border-transparent';

      const isExpanded = q
        ? (descMatches ? true : state.expandedFolders.has(pathKey))
        : state.expandedFolders.has(pathKey);

      const chevron = hasChildren
        ? `<button
            data-toggle-path="${encodeURIComponent(pathKey)}"
            class="shrink-0 w-6 h-8 grid place-items-center rounded-md text-slate-500 hover:text-emerald-300 transition"
            title="${isExpanded ? 'Collapse' : 'Expand'}"
          >
            <svg class="w-3.5 h-3.5 transition-transform duration-150 ${isExpanded ? 'rotate-90' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7"></path></svg>
          </button>`
        : `<span class="shrink-0 w-6"></span>`;

      const countBadge = count > 0
        ? `<span class="text-[10px] font-mono px-1.5 py-0.5 rounded-full bg-slate-800 text-slate-400 group-hover:text-slate-300 border border-slate-700/60 shrink-0">${count}</span>`
        : '';

      rows.push(`
        <div class="flex items-center gap-0.5" style="padding-left:${depth * 14}px">
          ${chevron}
          <button
            data-path="${encodeURIComponent(pathKey)}"
            class="flex-1 min-w-0 text-left px-2 py-1.5 rounded-lg border text-sm flex items-center justify-between gap-2 transition group ${activeClass}"
          >
            <div class="flex items-center gap-2 min-w-0 flex-1">
              <span class="text-base shrink-0">${icon}</span>
              <span class="truncate">${escapeHtml(folder.name)}</span>
            </div>
            ${countBadge}
          </button>
        </div>
      `);

      if (hasChildren && isExpanded) {
        if (!entry || (!entry.loaded && !entry.loading)) {
          ensureFolderChildren(pathKey);
        }
        renderChildren(children, depth + 1);
      }
    }
  };

  renderChildren(roots, 0);
  container.innerHTML = rows.join('');
}
// Render Breadcrumb Navigation
function renderBreadcrumbs() {
  // Sync single-line mobile header title & back button
  if (els.mobileFolderTitleText) {
    if (!state.breadcrumbs || state.breadcrumbs.length === 0) {
      els.mobileFolderTitleText.textContent = 'Media Library';
      if (els.mobileBackBtn) els.mobileBackBtn.classList.add('hidden');
    } else {
      const currentCrumb = state.breadcrumbs[state.breadcrumbs.length - 1];
      els.mobileFolderTitleText.textContent = currentCrumb.name || 'Media Library';
      if (els.mobileBackBtn) {
        if (state.breadcrumbs.length > 1) {
          els.mobileBackBtn.classList.remove('hidden');
          const parentCrumb = state.breadcrumbs[state.breadcrumbs.length - 2];
          els.mobileBackBtn.onclick = (e) => {
            e.preventDefault();
            loadFolder(parentCrumb.path);
          };
        } else {
          els.mobileBackBtn.classList.add('hidden');
        }
      }
    }
  }

  if (!state.breadcrumbs || state.breadcrumbs.length === 0) {
    els.breadcrumbNav.innerHTML = `<span class="text-slate-400 font-mono text-xs">/</span>`;
    return;
  }

  els.breadcrumbNav.innerHTML = state.breadcrumbs.map((crumb, idx) => {
    const isLast = idx === state.breadcrumbs.length - 1;
    const isRoot = idx === 0;
    const isZip = crumb.is_zip || (crumb.path && crumb.path.toLowerCase().endsWith('.zip'));
    const icon = isZip ? '📦' : (isRoot ? '📁' : '');

    if (isLast) {
      return `
        <span class="text-white font-semibold flex items-center gap-1 shrink-0">
          ${icon} <span class="truncate max-w-[200px] sm:max-w-xs">${crumb.name}</span>
        </span>
      `;
    }

    return `
      <button 
        data-crumb-path="${encodeURIComponent(crumb.path)}"
        class="text-slate-400 hover:text-emerald-300 transition flex items-center gap-1 shrink-0 hover:underline"
      >
        ${icon} <span>${crumb.name}</span>
      </button>
      <span class="text-slate-600">/</span>
    `;
  }).join('');

  els.breadcrumbNav.querySelectorAll('button[data-crumb-path]').forEach(btn => {
    btn.addEventListener('click', () => {
      const targetPath = decodeURIComponent(btn.dataset.crumbPath);
      loadFolder(targetPath);
    });
  });
}
// One folder/ZIP cover tile. The justified layout fixes its cover height and
// width (via flexStyle) so covers in a row share one height and read in order.
function subfolderCardHtml(sub, coverHeight, flexStyle) {
  const isZip = sub.is_zip || (sub.path && sub.path.toLowerCase().endsWith('.zip'));
  const icon = isZip ? '📦' : '📁';
  const count = sub.file_count || 0;
  const countLabel = isZip
    ? `${count} ${count === 1 ? 'item' : 'items'}`
    : `${count} ${count === 1 ? 'file' : 'files'}`;
  const coverUrl = sub.thumb_url || '';
  const coverImg = coverUrl
    ? `<img src="${coverUrl}" loading="lazy" alt=""
         class="absolute inset-0 w-full h-full object-cover transition duration-200 group-hover:scale-105"
         onerror="this.style.display='none'">`
    : '';
  const zipBadge = isZip
    ? `<span class="absolute top-1.5 left-1.5 z-10 text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-900/80 text-amber-300 border border-amber-500/30">ZIP</span>`
    : '';
  return `
    <button
      data-sub-path="${encodeURIComponent(sub.path)}"
      data-sub-name="${escapeHtml(sub.name)}"
      data-sub-iszip="${sub.is_zip ? '1' : '0'}"
      class="group flex flex-col rounded-xl overflow-hidden bg-slate-800/80 hover:bg-slate-700/80 border border-slate-700/70 hover:border-emerald-500/40 text-slate-200 transition active:scale-[0.98] shadow-sm text-left min-w-0"
      style="${flexStyle}"
    >
      <div class="subfolder-cover relative w-full bg-slate-900/60 overflow-hidden flex items-center justify-center" style="height: ${Math.round(coverHeight)}px">
        <span class="text-3xl opacity-80 group-hover:scale-110 transition">${icon}</span>
        ${coverImg}
        ${zipBadge}
      </div>
      <div class="flex flex-col gap-0.5 p-2.5 w-full min-w-0">
        <span class="truncate text-sm font-medium text-slate-100">${escapeHtml(sub.name)}</span>
        <span class="truncate whitespace-nowrap min-w-0 text-[11px] font-mono text-slate-400">${countLabel}</span>
      </div>
    </button>
  `;
}
// Render Subfolders as a responsive card grid
function renderSubfolders() {
  if (!state.subfolders || state.subfolders.length === 0) {
    els.subfoldersContainer.classList.add('hidden');
    return;
  }

  els.subfoldersContainer.classList.remove('hidden');
  els.subfolderCountBadge.textContent = state.subfolders.length;

  const gap = gridGapPx();
  const width = els.subfolderChips.clientWidth || gridWidth();
  const targetHeight = targetRowHeight(width, gap, subfolderColumnCount(), state.tileScale);
  const rows = layoutJustifiedRows(state.subfolders, width, gap, targetHeight, s => tileRatio(s.width, s.height));

  els.subfolderChips.innerHTML = rows.map(row => {
    const cards = row.items.map((sub, i) => {
      const ratio = row.ratios[i] || 1;
      const cardWidth = Math.max(1, Math.round(ratio * row.height));
      const flexStyle = row.partial
        ? `flex: 0 0 ${cardWidth}px; width: ${cardWidth}px;`
        : `flex: ${ratio} 1 0; min-width: 0;`;
      return subfolderCardHtml(sub, row.height, flexStyle);
    }).join('');
    return `<div class="subfolder-row flex items-start w-full" style="gap: ${gap}px">${cards}</div>`;
  }).join('');

  els.subfolderChips.querySelectorAll('button[data-sub-path]').forEach(btn => {
    btn.addEventListener('click', () => {
      const subPath = decodeURIComponent(btn.dataset.subPath);
      loadFolder(subPath);
    });
    btn.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      openContextMenu(e.clientX, e.clientY, {
        path: decodeURIComponent(btn.dataset.subPath),
        name: btn.dataset.subName,
        kind: btn.dataset.subIszip === '1' ? 'zip' : 'folder',
      });
    });
    attachLongPress(btn, () => ({
      path: decodeURIComponent(btn.dataset.subPath),
      name: btn.dataset.subName,
      kind: btn.dataset.subIszip === '1' ? 'zip' : 'folder',
    }));
  });
}

export {
  ensureFolderChildren,
  fetchFolderChildren,
  hasMatchingDescendant,
  loadFavoritesView,
  loadFolder,
  renderBreadcrumbs,
  renderSidebarTree,
  renderSubfolders,
  setSidebarOpen,
  subfolderCardHtml,
  syncTreeToCurrentPath,
  toggleFolderExpand,
};
