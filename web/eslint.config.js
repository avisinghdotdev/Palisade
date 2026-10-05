import js from "@eslint/js";
import eslintReact from "@eslint-react/eslint-plugin";
import query from "@tanstack/eslint-plugin-query";
import vitest from "@vitest/eslint-plugin";
import { defineConfig, globalIgnores } from "eslint/config";
import jsxA11yX from "eslint-plugin-jsx-a11y-x";
import perfectionist from "eslint-plugin-perfectionist";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig(
  globalIgnores([".react-router", "dist", "src/api/client"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.strictTypeChecked,
      tseslint.configs.stylisticTypeChecked,
      eslintReact.configs["strict-type-checked"],
      reactHooks.configs.flat["recommended-latest"],
      reactRefresh.configs.vite,
      jsxA11yX.configs.strict,
      query.configs["flat/recommended"],
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: { perfectionist },
    rules: {
      "perfectionist/sort-imports": "error",
      "@typescript-eslint/switch-exhaustiveness-check": "error",
      // eslint-plugin-react-hooks owns the React Compiler rules; these ESLint React copies would double-report.
      "@eslint-react/error-boundaries": "off",
      "@eslint-react/exhaustive-deps": "off",
      "@eslint-react/purity": "off",
      "@eslint-react/rules-of-hooks": "off",
      "@eslint-react/set-state-in-effect": "off",
      "@eslint-react/set-state-in-render": "off",
      "@eslint-react/static-components": "off",
      "@eslint-react/unsupported-syntax": "off",
      "@eslint-react/use-memo": "off",
    },
  },
  {
    files: ["src/root.tsx", "src/routes/**/*.tsx"],
    rules: {
      // React Router route-module exports; `action` and `headers` are omitted because `ssr: false` rejects them.
      "react-refresh/only-export-components": [
        "error",
        {
          allowExportNames: [
            "clientAction",
            "clientLoader",
            "clientMiddleware",
            "handle",
            "links",
            "loader",
            "meta",
            "middleware",
            "shouldRevalidate",
          ],
        },
      ],
    },
  },
  {
    files: ["**/*.test.{ts,tsx}"],
    extends: [vitest.configs.recommended],
  },
);
