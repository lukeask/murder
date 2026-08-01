import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

const coreSrc = fileURLToPath(new URL('../ui-core/src', import.meta.url));

// `globals: true` lets test files use describe/it/expect without importing them — it also
// requires "vitest/globals" in tsconfig.test.json's `types` so the compiler agrees. Tests
// live in test/ (Vitest auto-discovers *.test.ts / *.test.tsx).
export default defineConfig({
  resolve: {
    alias: {
      '@murder/ui-core': coreSrc,
    },
  },
  test: {
    globals: true,
    include: ['test/**/*.test.{ts,tsx}'],
  },
});
