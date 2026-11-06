/** Theme + tile-density stores shared across the shell and Settings. */

import { create } from 'zustand'
import { getStoredTheme, setStoredTheme, watchSystemTheme, type ThemeMode } from './theme'

interface ThemeState {
  mode: ThemeMode
  setMode: (mode: ThemeMode) => void
}

export const useThemeStore = create<ThemeState>((set) => ({
  mode: getStoredTheme(),
  setMode: (mode) => {
    setStoredTheme(mode)
    set({ mode })
  },
}))

let stopWatching: (() => void) | null = null

/** Call once from main.tsx: applies the stored theme and follows the OS. */
export function initTheme(): void {
  setStoredTheme(useThemeStore.getState().mode)
  stopWatching?.()
  stopWatching = watchSystemTheme()
}

export type TileScale = 's' | 'm' | 'l'

export const TILE_ROW_HEIGHT: Record<TileScale, number> = {
  s: 150,
  m: 210,
  l: 290,
}

const TILE_STORAGE_KEY = 'v2_tile_scale'

function readTileScale(): TileScale {
  try {
    const stored = window.localStorage.getItem(TILE_STORAGE_KEY)
    if (stored === 's' || stored === 'm' || stored === 'l') return stored
  } catch {
    /* ignore unavailable storage */
  }
  return 'm'
}

interface DensityState {
  scale: TileScale
  setScale: (scale: TileScale) => void
}

export const useDensityStore = create<DensityState>((set) => ({
  scale: readTileScale(),
  setScale: (scale) => {
    try {
      window.localStorage.setItem(TILE_STORAGE_KEY, scale)
    } catch {
      /* ignore */
    }
    set({ scale })
  },
}))

interface PaletteState {
  open: boolean
  setOpen: (open: boolean) => void
}

export const usePaletteStore = create<PaletteState>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}))
