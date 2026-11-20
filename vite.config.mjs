import { defineConfig } from 'vite';
import { resolve } from 'node:path';
import { viteSingleFile } from 'vite-plugin-singlefile';

const entry = process.env.KONTROL_EXPORT_ENTRY;
if (entry && !['index', 'prototype'].includes(entry)) throw new Error('Invalid export entry');

export default defineConfig({
  root: 'web',
  esbuild: { jsx: 'automatic' },
  plugins: entry ? [viteSingleFile()] : [],
  build: {
    outDir: process.env.KONTROL_EXPORT_DIR || 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: entry
        ? resolve(import.meta.dirname, `web/${entry === 'index' ? 'index' : 'prototype'}.html`)
        : {
            index: resolve(import.meta.dirname, 'web/index.html'),
            prototype: resolve(import.meta.dirname, 'web/prototype.html'),
          },
    },
  },
  server: { proxy: { '/api': 'http://127.0.0.1:8000', '/docs': 'http://127.0.0.1:8000' } },
});
