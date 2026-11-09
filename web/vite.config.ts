import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

import pkg from "./package.json" with { type: "json" };

export default defineConfig({
  plugins: [tailwindcss(), reactRouter()],
  resolve: { tsconfigPaths: true },
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  // The dev server only runs inside Compose, where the API service is reachable as `api`.
  server: {
    proxy: { "/api": "http://api:8000", "/.well-known": "http://api:8000" },
  },
});
