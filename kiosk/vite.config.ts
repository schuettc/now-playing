import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Dev-only: when VITE_DEV_PROXY is set (e.g. http://<pi>:8080), forward the
// kiosk's relative /api and /control calls to that backend so a local dev
// server can drive a real orchestrator. Inert in production builds.
const devProxyTarget = process.env.VITE_DEV_PROXY;

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: true,
    port: 5173,
    ...(devProxyTarget
      ? {
          proxy: {
            '/api': { target: devProxyTarget, changeOrigin: true },
            '/control': { target: devProxyTarget, changeOrigin: true },
            '/art': { target: devProxyTarget, changeOrigin: true },
          },
        }
      : {}),
  },
});
