import { defineConfig } from 'vite';
export default defineConfig({ build: { target: 'esnext', copyPublicDir: false }, worker: { format: 'es' } });
