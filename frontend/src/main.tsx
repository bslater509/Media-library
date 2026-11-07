import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router'
import { Toaster } from 'sonner'
import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono'
import './styles/tailwind.css'
import { router } from './app/router'
import { AuthGate } from './components/shell/AuthGate'
import { TooltipProvider } from './components/ui'
import { initTheme, useThemeStore } from './lib/stores'
import { getSystemTheme, type ResolvedTheme } from './lib/theme'

initTheme()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 20_000,
    },
  },
})

/** Keeps the sonner toaster in step with the app theme. */
function ThemedToaster() {
  const mode = useThemeStore((state) => state.mode)
  const resolved: ResolvedTheme =
    mode === 'system' ? getSystemTheme() : (mode as ResolvedTheme)
  return (
    <Toaster
      position="bottom-right"
      theme={resolved}
      toastOptions={{ className: 'text-sm' }}
    />
  )
}

const container = document.getElementById('root')
if (!container) {
  throw new Error('Missing #root element in index.html')
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <AuthGate>
          <RouterProvider router={router} />
          <ThemedToaster />
        </AuthGate>
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
)
