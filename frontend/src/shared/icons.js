/**
 * Shared inline SVG icon set.
 *
 * Icons come from `lucide-static` (ISC) as raw SVG files, which Vite inlines at
 * build time via the `?raw` suffix — no runtime dependency, no icon font, no CDN
 * request, and only the icons imported here end up in the bundle.
 *
 * Usage:
 *   icon('camera', 'w-4 h-4')      -> SVG markup string (safe for innerHTML)
 *   hydrateIcons(document)         -> replaces every `[data-lb-icon]` placeholder
 *
 * Templates that are static HTML (the mobile controls sheet, the two Background
 * Tasks modals) carry a `data-lb-icon="name"` attribute plus an optional
 * `data-lb-icon-class` on the placeholder element; `hydrateIcons()` fills it in
 * at init, so a placeholder can keep its old glyph as a no-JS fallback.
 *
 * Shared by both frontend entrypoints: `library.js` (Media Library) and
 * `app.js` (Video Sorter).
 */

import archive from 'lucide-static/icons/archive.svg?raw';
import arrowRightLeft from 'lucide-static/icons/arrow-right-left.svg?raw';
import ban from 'lucide-static/icons/ban.svg?raw';
import camera from 'lucide-static/icons/camera.svg?raw';
import check from 'lucide-static/icons/check.svg?raw';
import circleCheck from 'lucide-static/icons/circle-check.svg?raw';
import circleX from 'lucide-static/icons/circle-x.svg?raw';
import clapperboard from 'lucide-static/icons/clapperboard.svg?raw';
import clock from 'lucide-static/icons/clock.svg?raw';
import cog from 'lucide-static/icons/cog.svg?raw';
import copyIcon from 'lucide-static/icons/copy.svg?raw';
import database from 'lucide-static/icons/database.svg?raw';
import download from 'lucide-static/icons/download.svg?raw';
import fileArchive from 'lucide-static/icons/file-archive.svg?raw';
import fileUp from 'lucide-static/icons/file-up.svg?raw';
import fileVideo from 'lucide-static/icons/file-video.svg?raw';
import film from 'lucide-static/icons/film.svg?raw';
import folder from 'lucide-static/icons/folder.svg?raw';
import folderInput from 'lucide-static/icons/folder-input.svg?raw';
import folderPlus from 'lucide-static/icons/folder-plus.svg?raw';
import folderTree from 'lucide-static/icons/folder-tree.svg?raw';
import folderUp from 'lucide-static/icons/folder-up.svg?raw';
import gauge from 'lucide-static/icons/gauge.svg?raw';
import globe from 'lucide-static/icons/globe.svg?raw';
import hardDrive from 'lucide-static/icons/hard-drive.svg?raw';
import image from 'lucide-static/icons/image.svg?raw';
import images from 'lucide-static/icons/images.svg?raw';
import info from 'lucide-static/icons/info.svg?raw';
import link from 'lucide-static/icons/link.svg?raw';
import loaderCircle from 'lucide-static/icons/loader-circle.svg?raw';
import monitor from 'lucide-static/icons/monitor.svg?raw';
import packageOpen from 'lucide-static/icons/package-open.svg?raw';
import palette from 'lucide-static/icons/palette.svg?raw';
import pencil from 'lucide-static/icons/pencil.svg?raw';
import refreshCw from 'lucide-static/icons/refresh-cw.svg?raw';
import repeat2 from 'lucide-static/icons/repeat-2.svg?raw';
import rocket from 'lucide-static/icons/rocket.svg?raw';
import search from 'lucide-static/icons/search.svg?raw';
import settings from 'lucide-static/icons/settings.svg?raw';
import slidersHorizontal from 'lucide-static/icons/sliders-horizontal.svg?raw';
import sparkles from 'lucide-static/icons/sparkles.svg?raw';
import star from 'lucide-static/icons/star.svg?raw';
import trash2 from 'lucide-static/icons/trash-2.svg?raw';
import upload from 'lucide-static/icons/upload.svg?raw';
import video from 'lucide-static/icons/video.svg?raw';
import wrench from 'lucide-static/icons/wrench.svg?raw';
import x from 'lucide-static/icons/x.svg?raw';
import zap from 'lucide-static/icons/zap.svg?raw';

export const ICONS = Object.freeze({
  archive,
  'arrow-right-left': arrowRightLeft,
  ban,
  camera,
  check,
  'circle-check': circleCheck,
  'circle-x': circleX,
  clapperboard,
  clock,
  cog,
  copy: copyIcon,
  database,
  download,
  'file-archive': fileArchive,
  'file-up': fileUp,
  'file-video': fileVideo,
  film,
  folder,
  'folder-input': folderInput,
  'folder-plus': folderPlus,
  'folder-tree': folderTree,
  'folder-up': folderUp,
  gauge,
  globe,
  'hard-drive': hardDrive,
  image,
  images,
  info,
  link,
  'loader-circle': loaderCircle,
  monitor,
  'package-open': packageOpen,
  palette,
  pencil,
  'refresh-cw': refreshCw,
  'repeat-2': repeat2,
  rocket,
  search,
  settings,
  'sliders-horizontal': slidersHorizontal,
  sparkles,
  star,
  'trash-2': trash2,
  upload,
  video,
  wrench,
  x,
  zap,
});

export const DEFAULT_ICON_CLASS = 'lb-icon';

/**
 * Return the SVG markup for `name`, classed as `className` and hidden from
 * assistive tech (the surrounding button carries the accessible name).
 * Returns an empty string for an unknown icon so callers can degrade quietly.
 */
export function icon(name, className = DEFAULT_ICON_CLASS) {
  const svg = ICONS[name];
  if (!svg) return '';
  return svg
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\swidth="\d+"|\sheight="\d+"/g, '')
    .replace(/class="lucide lucide-[^"]*"/, `class="${className}" aria-hidden="true" focusable="false"`)
    .trim();
}

/**
 * Replace every `[data-lb-icon]` placeholder inside `root` with its SVG.
 * Placeholders keep their emoji/text as a no-JS fallback until this runs.
 * Returns the number of placeholders that rendered an icon.
 */
export function hydrateIcons(root = typeof document === 'undefined' ? null : document) {
  if (!root || typeof root.querySelectorAll !== 'function') return 0;
  let count = 0;
  root.querySelectorAll('[data-lb-icon]').forEach((el) => {
    const html = icon(el.dataset.lbIcon, el.dataset.lbIconClass || DEFAULT_ICON_CLASS);
    if (!html) return;
    el.innerHTML = html;
    count += 1;
  });
  return count;
}
