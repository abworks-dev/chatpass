import { build } from 'esbuild';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { readFile, rm } from 'node:fs/promises';
const root = fileURLToPath(new URL('../', import.meta.url));
const output = path.resolve(root, 'lib');
// This directory contains only generated plugin artifacts; verify before deletion.
if (path.dirname(output) !== path.resolve(root) || path.basename(output) !== 'lib') throw new Error('Invalid build output directory');
await rm(output, { recursive: true, force: true });
const types = spawnSync(process.execPath, [path.join(root, 'node_modules/typescript/bin/tsc')], { cwd: root, stdio: 'inherit' });
if (types.error) throw types.error;
if (types.status !== 0) process.exit(types.status ?? 1);
await build({ absWorkingDir: root, entryPoints: ['src/index.ts'], outdir: 'lib', bundle: true, format: 'esm', platform: 'node', target: 'node22', packages: 'external', sourcemap: true });
// DSH loads a client module, not an HTML entry that would link a CSS sidecar.
const styles = { name: 'inline-plugin-css', setup(builder) {
  builder.onLoad({ filter: /\.css$/ }, async ({ path: filename }) => ({ loader: 'js', contents:
    `if (!document.querySelector('style[data-dsh-chatpass]')) { const style = document.createElement('style'); style.dataset.dshChatpass = ''; style.textContent = ${JSON.stringify(await readFile(filename, 'utf8'))}; document.head.append(style); }` }));
} };
const manifest = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
// The web host concatenates classic scripts. Register a factory with its loader;
// emitting ESM here passes Node import checks but crashes actual browser boot.
await build({ absWorkingDir: root, entryPoints: ['src/client.tsx'], outdir: 'lib', bundle: true, format: 'cjs', platform: 'browser', target: 'es2022', packages: 'external', sourcemap: true, plugins: [styles],
  banner: { js: `window.__ModuleLoader__.load({ id: ${JSON.stringify(manifest.name)}, factory: (require) => { var module = { exports: {} }; var exports = module.exports;` },
  footer: { js: 'return module.exports; } });' },
});
// Exercise the delivered script format on every build, not just source tests.
await import('./check-client-bundle.mjs');
