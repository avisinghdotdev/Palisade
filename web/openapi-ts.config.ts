import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "openapi.json",
  output: "src/api/client",
  plugins: [
    // No base URL: requests stay relative, so nginx or the Vite proxy routes /api on the same origin.
    { name: "@hey-api/client-fetch", baseUrl: false },
    { name: "@tanstack/react-query", queryOptions: true },
  ],
});
