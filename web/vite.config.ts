/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Vite + Vitest в одному конфігу (§8). `base: '/'` — GUI подається з кореня nginx.
export default defineConfig({
  plugins: [react()],
  base: '/',
  resolve: {
    alias: {
      '~': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    // Route-level code splitting (§7.7) робить React.lazy; окремі chunk-и мають бути видимі
    // у dist/assets — це перевіряє tests/unit/build-contract.test.ts через маніфест роутів.
    outDir: 'dist',
    sourcemap: true,
    target: 'es2023',
  },
  server: {
    // Dev-режим: same-origin `/api` як у production (nginx reverse proxy → api:8000).
    proxy: {
      '/api': {
        target: process.env.COLLECTOR_API_URL ?? 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  preview: {
    // Явно 127.0.0.1: `localhost` на Windows резолвиться у ::1, і Playwright/`curl` на
    // 127.0.0.1 не достукуються до preview-сервера.
    host: '127.0.0.1',
    port: 4173,
    strictPort: true,
  },
  test: {
    // Мережа у unit-тестах не потрібна: jsdom + Testing Library, без реального API.
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/unit/setup.ts'],
    include: ['tests/unit/**/*.test.{ts,tsx}'],
    css: false,
    restoreMocks: true,
    // `eslint-no-browser-storage.test.ts` запускає ESLint програмно з type-aware
    // конфігом — це десятки секунд на холодному кеші TS. Дефолтні 5/10 с замалі.
    testTimeout: 60_000,
    hookTimeout: 120_000,
  },
});
