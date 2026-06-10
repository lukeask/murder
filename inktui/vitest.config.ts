import { defineConfig } from 'vitest/config';

// `globals: true` lets test files use describe/it/expect without importing them — it also
// requires "vitest/globals" in tsconfig.test.json's `types` so the compiler agrees. Tests
// live in test/ (Vitest auto-discovers *.test.ts / *.test.tsx).
export default defineConfig({
  test: {
    globals: true,
    include: ['test/**/*.test.{ts,tsx}'],
  },
});
