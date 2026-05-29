import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Unit tests for plain TS logic (stores, IPC clients). Deliberately does NOT
// load the SvelteKit plugin — these tests exercise framework-free modules, and
// the Tauri bridge is dynamically imported only inside the webview, so node is
// the right environment. We resolve the SvelteKit `$lib` alias by hand (the
// plugin would normally provide it) so store modules — which import from
// "$lib/api" — load under vitest without dragging in SvelteKit.
export default defineConfig({
  resolve: {
    alias: {
      $lib: fileURLToPath(new URL("./src/lib", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.{test,spec}.ts"],
  },
});
