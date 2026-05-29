import adapter from "@sveltejs/adapter-static";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    // Static SPA build — the Tauri WebView loads the prerendered shell.
    adapter: adapter({ fallback: "index.html" }),
  },
};

export default config;
