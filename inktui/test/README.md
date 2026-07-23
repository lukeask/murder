# test

Vitest suites. Store/selector/logic tests use plain Vitest driven through `FakeApplicationClient`;
component tests use `ink-testing-library` and assert on the painted frame. Every chunk ships
its own tests here; mirror the source path (e.g. `test/store/roster.test.ts`).
