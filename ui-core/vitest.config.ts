import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

const sourceRoot = fileURLToPath(new URL('./src', import.meta.url));

export default defineConfig({
  resolve: {
    alias: [{ find: '@murder/ui-core', replacement: sourceRoot }],
  },
  test: {
    globals: true,
    include: ['test/**/*.test.{ts,tsx}'],
  },
});
