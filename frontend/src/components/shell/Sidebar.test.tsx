import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import { MobileSidebarContent, Sidebar } from './Sidebar'

vi.mock('../../lib/queries', () => ({
  useInfo: () => ({ data: { counts: { files: 1200 } } }),
  useRootFolders: () => ({
    data: {
      folders: Array.from({ length: 200 }, (_, index) => ({
        path: `/folder-${index}`,
        name: `Folder ${index}`,
        file_count: 1,
      })),
    },
    isLoading: false,
  }),
}))

describe('sidebar scroll layout', () => {
  it('constrains the desktop sidebar and contains folder scroll chaining', () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )
    expect(html).toMatch(/<aside class="[^"]*\bhidden\b[^"]*\bmd:sticky\b/)
    expect(html).toContain('md:top-0')
    expect(html).toContain('md:h-dvh')
    expect(html).toContain('md:self-start')
    expect(html).toContain('min-h-0 flex-1 overflow-y-auto overscroll-y-contain')
    // Navigation and Settings are outside the scrolling folder list.
    expect(html.indexOf('</nav>')).toBeLessThan(html.indexOf('overscroll-y-contain'))
    expect(html).toMatch(/Folder 199[\s\S]*<\/div><div class="shrink-0 border-t/)
    expect(html).toContain('Settings')
  })

  it('leaves the mobile drawer without desktop pinning or scroll containment', () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <MobileSidebarContent />
      </MemoryRouter>,
    )
    expect(html).toContain('min-h-0 flex-1 overflow-y-auto')
    expect(html).not.toContain('md:sticky')
    expect(html).not.toContain('md:h-dvh')
    expect(html).not.toContain('overscroll-y-contain')
  })
})
