import { defineConfig } from "@playwright/test";

// E2E: drives the real built SPA (vite preview) with the Python sidecar mocked at
// the HTTP boundary. Outside the Tauri webview the app boots straight to the Clone
// screen and talks to 127.0.0.1:3900, so no Tauri IPC mocking is needed — see
// e2e/clone-speak.spec.ts. Covers the clone→speak happy path the Testing Rules
// (CLAUDE.md) require. Run: `bun run test:e2e` (needs `bunx playwright install chromium`).
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: {
    command: "bun run build && bunx vite preview --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
