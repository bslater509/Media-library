import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  build: {
    outDir: 'static/dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        library: resolve(__dirname, 'frontend/src/library/index.js'),
        app: resolve(__dirname, 'frontend/src/app/index.js')
      },
      output: {
        entryFileNames: '[name].js',
        chunkFileNames: '[name]-[hash].js',
        assetFileNames: '[name][extname]'
      }
    }
  }
});
