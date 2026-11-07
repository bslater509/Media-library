import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  root: 'frontend',
  plugins: [react(), tailwindcss()],
  base: '/static/dist/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./frontend/src', import.meta.url)),
    },
  },
  build: {
    outDir: '../static/dist',
    emptyOutDir: true,
    sourcemap: false,
    target: 'es2022',
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8888',
        changeOrigin: true,
      },
      '/static': {
        target: 'http://127.0.0.1:8888',
        changeOrigin: true,
      },
    },
  },
})
