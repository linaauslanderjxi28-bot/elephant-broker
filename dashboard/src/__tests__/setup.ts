/**
 * Vitest global setup (referenced by `vite.config.ts` → test.setupFiles).
 *
 * Registers jest-dom matchers and polyfills the handful of browser APIs that
 * jsdom does not implement but MUI (DataGrid / responsive components) touches
 * during render — without these, mounting a page that uses the DataGrid throws.
 */

import "@testing-library/jest-dom";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Unmount React trees between tests so state/DOM does not leak across cases.
afterEach(() => {
  cleanup();
});

// jsdom has no ResizeObserver; MUI x-data-grid relies on it.
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).ResizeObserver = ResizeObserverStub;
}

// jsdom has no matchMedia; MUI's responsive utilities call it.
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}
