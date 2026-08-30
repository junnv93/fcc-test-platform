import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactPlugin from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import importPlugin from 'eslint-plugin-import';
import globals from 'globals';

/**
 * Flat ESLint config (v9+). Strict TS + a11y + import order.
 *
 * Sprint S1 baseline rules — Sprint S8 (production checklist) may tighten further.
 */
export default tseslint.config(
  {
    ignores: [
      'dist',
      'coverage',
      'playwright-report',
      'src/api/generated/**',
      'node_modules',
      '.vite',
      // tsc -b emits `.d.ts` alongside composite-project entrypoints. We've
      // migrated away from project refs (tsconfig.node.json is now standalone
      // -p invocation) but keep this ignore so a stale local artefact does
      // not break CI on the developer's next push.
      '**/*.d.ts',
      'tsconfig.tsbuildinfo',
      '**/tsconfig.*.tsbuildinfo',
      // `public/` is served verbatim by nginx — runtime-config.js is plain
      // JS injected at deploy time, never type-checked.
      'public/**',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,
  {
    files: ['src/**/*.{ts,tsx}', 'tests/**/*.{ts,tsx}'],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      globals: { ...globals.browser },
    },
    plugins: {
      react: reactPlugin,
      'react-hooks': reactHooks,
      'jsx-a11y': jsxA11y,
      import: importPlugin,
    },
    settings: {
      react: { version: '18.3' },
      // Sprint S2-α — `eslint-import-resolver-typescript` is the
      // industry-standard resolver that understands tsconfig path aliases
      // (`@/*`) and the `paths`/`baseUrl` mapping. Required for
      // `import/order` and `import/no-unresolved` to classify
      // `@/auth/...` imports correctly.
      'import/resolver': {
        typescript: { project: './tsconfig.json' },
        node: true,
      },
    },
    rules: {
      ...reactPlugin.configs.recommended.rules,
      ...reactPlugin.configs['jsx-runtime'].rules,
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.configs.recommended.rules,
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      '@typescript-eslint/consistent-type-imports': ['error', { prefer: 'type-imports' }],
      '@typescript-eslint/no-non-null-assertion': 'error',
      '@typescript-eslint/no-explicit-any': 'error',
      // tsconfig's `noUncheckedIndexedAccess: true` requires bracket access
      // for index-signature properties (`process.env['X']`); dot-notation
      // then complains the OTHER way. Allow brackets for index sigs only.
      '@typescript-eslint/dot-notation': ['error', { allowIndexSignaturePropertyAccess: true }],
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      'import/order': [
        'error',
        {
          groups: ['builtin', 'external', 'internal', 'parent', 'sibling', 'index', 'type'],
          'newlines-between': 'always',
          alphabetize: { order: 'asc', caseInsensitive: true },
        },
      ],
      'react/prop-types': 'off',
    },
  },
  {
    files: [
      'vite.config.ts',
      'playwright.config.ts',
      'playwright.global-setup.ts',
      'playwright.global-setup.cjs',
      'playwright.grid-poc.config.ts',
      'scripts/**/*.{mjs,ts}',
      'vite/**/*.{mjs,ts}',
      'eslint.config.js',
    ],
    languageOptions: {
      globals: { ...globals.node },
    },
    rules: {
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
    },
  },
  // Per https://typescript-eslint.io/getting-started/typed-linting — disable
  // type-aware lint rules for Node config/scripts. Their complete program is
  // checked by tsconfig.node.json; ESLint still applies syntax-level rules.
  {
    files: [
      'eslint.config.js',
      'scripts/**/*.mjs',
      // The live-lane registry ships a declaration beside its `.mjs` so the e2e
      // fixture can consume it without widening every id to `any`. It belongs to
      // the same Node-scripts program (`tsconfig.node.json`), not to the app
      // program typed linting is configured against.
      'scripts/**/*.d.mts',
      'vite.config.ts',
      'vite/**/*.{mjs,ts}',
      'playwright.config.ts',
      'playwright.global-setup.ts',
      'playwright.global-setup.cjs',
      'playwright.grid-poc.config.ts',
    ],
    ...tseslint.configs.disableTypeChecked,
  },
  {
    files: ['playwright.global-setup.cjs'],
    rules: {
      '@typescript-eslint/no-require-imports': 'off',
    },
  },
  // This contract test intentionally consumes executable `.mjs` helpers. The
  // helpers remain the runtime SSOT and are checked by tsconfig.node.json;
  // avoid duplicating their signatures in hand-maintained ambient declarations.
  {
    files: ['tests/harness-browser-runtime.test.ts'],
    rules: {
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
    },
  },
);
