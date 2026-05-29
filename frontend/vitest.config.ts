import { defineConfig } from "vitest/config";

// Unit tests for plain TS logic (stores, IPC clients). Deliberately does NOT
// load the SvelteKit plugin — these tests exercise framework-free modules, and
// the Tauri bridge is dynamically imported only inside the webview, so node is
// the right environment.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.{test,spec}.ts"],
  },
});
