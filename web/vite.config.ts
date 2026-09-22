/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Vite + Vitest в одному конфігу (§8). `base: '/'` — GUI подається з кореня nginx.
// `mode: 'image'` (скрипт `build:image`, який виконує web/Dockerfile) вимикає sourcemap:
// production-образ — єдиний публічний сервіс стека, а `.map` містить повний оригінальний
// TS-код у `sourcesContent` і віддавався б анонімно з `expires 1y` (код-рев'ю M-2).
// Локальний `npm run build` мапи лишає — вони потрібні розробнику і в CI-артефактах.
export default defineConfig(({ mode }) => ({
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
    sourcemap: mode !== 'image',
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
    // Таймаути — не глобальні: підняті лише у файлі, який запускає ESLint програмно
    // (`tests/unit/eslint-no-browser-storage.test.ts`), щоб зависання будь-якого іншого
    // тесту коштувало 5 с, а не хвилину (код-рев'ю L-2).
  },
}));
