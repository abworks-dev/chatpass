// Run with the installed desktop's Electron in ELECTRON_RUN_AS_NODE mode.
// Usage: <electron> --expose-internals scripts/check-installed-host.mjs <dsh-root> [manifest]
// Read-only: never installs, changes profiles, grants exemptions, or contacts a gateway.
import { readFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = process.argv[2];
if (!root) throw new Error('Pass the installed DSH root (the directory containing node_modules).');
const manifestPath = process.argv[3] ?? fileURLToPath(new URL('../package.json', import.meta.url));
const manifest = JSON.parse(readFileSync(resolve(manifestPath), 'utf8'));
const hostModules = join(resolve(root), 'node_modules');
const boot = await import(pathToFileURL(join(hostModules, '@deepseek-ai/dsh-app-boot/lib/index.js')).href);
const issue = boot.evaluatePluginCompatibility(manifest);
if (issue) throw new Error(boot.pluginCompatibilityWarning(issue));
for (const [name, range] of Object.entries(manifest.peerDependencies ?? {})) {
  if (!name.startsWith('@deepseek-ai/')) continue;
  const installed = JSON.parse(readFileSync(join(hostModules, name, 'package.json'), 'utf8'));
  const development = manifest.devDependencies?.[name];
  if (development !== installed.version) throw new Error(`${name}: build SDK ${development} differs from installed ${installed.version}`);
  console.log(`${name}: installed=${installed.version}, peer=${range}, build=${development}`);
}
// Negative control: prove the exact previous incompatibility is still rejected.
const incompatible = { ...manifest, peerDependencies: { ...manifest.peerDependencies, '@deepseek-ai/dsh-llm': '0.2.1-alpha.1' } };
if (!boot.evaluatePluginCompatibility(incompatible)) throw new Error('Negative compatibility control unexpectedly accepted.');
console.log(`PASS ${manifest.name}@${manifest.version}: official host compatibility check; matching build SDK; incompatible-peer negative control.`);
