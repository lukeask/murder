/**
 * Ink's private per-stdout renderer registry (`ink/build/instances.js`).
 *
 * Not a public package export. Imported by relative path (not `createRequire`) so both tsx
 * (dev) and the esbuild wheel bundle share the **same** WeakMap Ink itself uses. A
 * `createRequire` into `../../node_modules/ink/...` breaks the packaged launch path: the
 * relative path is evaluated from `murder/_inktui/index.js` (module-not-found), and even a
 * path workaround would point at a second WeakMap while Ink is inlined in the bundle.
 *
 * Package `#imports` cannot target `node_modules` (Node `ERR_INVALID_PACKAGE_TARGET`), and
 * `ink`'s export map blocks `ink/build/instances.js`, so the relative path is the portable
 * reach-in that still dedupes under esbuild.
 */
import instances from '../../node_modules/ink/build/instances.js';

/** Untyped host object — callers narrow the private renderer fields they touch. */
export const inkInstances: WeakMap<NodeJS.WriteStream, object> = instances;
