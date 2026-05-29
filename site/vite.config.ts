import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Static marketing/download page. Builds to dist/ for any static host
// (GitHub Pages / Cloudflare Pages).
export default defineConfig({
  // Relative base so assets resolve under a GitHub Pages project subpath (/parrot/).
  base: "./",
  plugins: [tailwindcss()],
  build: { outDir: "dist", emptyOutDir: true },
});
