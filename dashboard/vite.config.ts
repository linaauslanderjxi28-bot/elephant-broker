/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// EB runtime serves the built dashboard at `/ui/*` (same-origin, no CORS) in
// production (AD-11). During dev the Vite server runs at the root so SuperTokens
// redirects and Refine routing behave like a normal SPA.
export default defineConfig(({ mode }) => ({
  base: mode === "production" ? "/ui/" : "/",
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    host: true,
  },
  preview: {
    port: 4173,
  },
  build: {
    // Vite emits the production static bundle here; the EB runtime mounts this
    // directory at `/ui/*` (see EB_DASHBOARD_STATIC_DIR).
    outDir: "dist",
    sourcemap: true,
    emptyOutDir: true,
  },
  test: {
    globals: true,
    environment: "jsdom",
    // The frontend-tests agent owns this setup file (jest-dom matchers, etc.).
    setupFiles: ["./src/__tests__/setup.ts"],
    css: false,
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    server: {
      deps: {
        // @refinedev/mui's ESM build (dist/index.mjs) uses directory imports
        // like `@mui/material/Box`. Node's native ESM resolver (used when a dep
        // is externalized) rejects directory imports, breaking suite collection
        // for any test that imports a page pulling in @refinedev/mui (e.g.
        // MemoryBrowser.test.tsx). Inlining forces the package through Vite's
        // own resolver, which resolves the directory imports correctly.
        inline: [/@refinedev\/mui/],
      },
    },
  },
}));
