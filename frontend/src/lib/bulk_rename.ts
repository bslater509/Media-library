export type RenameMode = 'prefix' | 'suffix' | 'replace'

/** Apply a rename pattern to the base name, preserving the extension. */
export function bulkName(
  name: string,
  mode: RenameMode,
  value: string,
  find: string,
  replace: string,
): string {
  const dot = name.lastIndexOf('.')
  const base = dot > 0 ? name.slice(0, dot) : name
  const ext = dot > 0 ? name.slice(dot) : ''
  let next = base
  if (mode === 'prefix') next = `${value}${base}`
  if (mode === 'suffix') next = `${base}${value}`
  if (mode === 'replace' && find) next = base.split(find).join(replace)
  return `${next}${ext}`
}
