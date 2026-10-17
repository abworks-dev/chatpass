// Read-only import smoke with the desktop's real profile module resolver.
// Run under its Electron with ELECTRON_RUN_AS_NODE=1 and --expose-internals.
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const [rootArg, profileArg] = process.argv.slice(2);
if (!rootArg || !profileArg) throw new Error('Pass installed DSH root and profile directory.');
const root = resolve(rootArg), dir = resolve(profileArg);
const host = name => pathToFileURL(join(root, 'node_modules', name, 'lib/index.js')).href;
const boot = await import(host('@deepseek-ai/dsh-app-boot'));
const { Context } = await import(host('@deepseek-ai/cordis'));
const packageDir = join(dir, 'node_modules/dsh-chatpass');
const profile = { dir, layers: [{ packageName: 'dsh-chatpass', packageDir }], skippedBundles: [] };
const resolution = await boot.createRuntimeResolution({ installAnchor: join(root, 'package.json'), profile });
const ctx = new Context();
try {
  const resolver = ctx.plugin(boot.PluginPackages, { resolution });
  await resolver.await();
  const { ModuleLoader } = await import(host('@deepseek-ai/cordis-plugin-loader'));
  const loader = ModuleLoader.fromInternal();
  if (!loader) throw new Error('Official module loader unavailable.');
  const plugin = await loader.import('dsh-chatpass', pathToFileURL(join(dir, 'package.json')).href, {});
  if (typeof plugin.apply !== 'function') throw new Error('Missing host apply export');
  console.log('PASS installed host import using official profile resolver:', plugin.name);
} catch (error) {
  console.error(error.stack ?? error);
  process.exitCode = 1;
}
// The resolver belongs only to this short-lived diagnostic process. No host
// services, gateway requests, or profile/config writes are started here.
