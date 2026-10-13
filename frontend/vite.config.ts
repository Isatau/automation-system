import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Билд собирается прямо в app/static — FastAPI раздаёт его как SPA (см. app/main.py).
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: { outDir: '../app/static', emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
});
