import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({command}) => ({
  base: command === 'serve' ? '/' : '/static/',
  plugins: [react()],
  build: { outDir: '../web', emptyOutDir: false, rollupOptions: {output: {entryFileNames:'guest.js',assetFileNames:'app.css'}} },
  server: {
    port: 5173,
    host: true,
    proxy: {'/api': {target: 'http://127.0.0.1:8000', changeOrigin: true, ws: true},
            '/static': {target: 'http://127.0.0.1:8000', changeOrigin: true}},
  },
}));
