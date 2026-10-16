import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  build: {
    outDir: 'static/dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        library: resolve(__dirname, 'frontend/src/library/index.js'),
        app: resolve(__dirname, 'frontend/src/app/index.js'),
        downloads: resolve(__dirname, 'frontend/src/downloads/index.js'),
        analytics: resolve(__dirname, 'frontend/src/analytics/index.js')
      },
      output: {
        entryFileNames: '[name].js',
        chunkFileNames: '[name]-[hash].js',
        // The app links /static/dist/styles.css directly, so every extracted
        // CSS chunk (which Vite may attribute to a shared JS chunk) must keep
        // that stable filename regardless of where it lands in the graph.
        assetFileNames: (assetInfo) =>
          assetInfo.name && assetInfo.name.endsWith('.css')
            ? 'styles.css'
            : '[name][extname]',
      }
    }
  }
});