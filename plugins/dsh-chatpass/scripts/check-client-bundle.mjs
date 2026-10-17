// DSH serves plugins as classic scripts that register a CommonJS factory.
// An ESM import smoke is insufficient: static import/export must fail here.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
const filename = process.argv[2] ?? fileURLToPath(new URL('../lib/client.js', import.meta.url));
const manifest = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
const source = await readFile(filename, 'utf8');
const require = createRequire(import.meta.url);
const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', { runScripts: 'outside-only' });
try {
  const modules = [];
  dom.window.__ModuleLoader__ = { load(registration) {
    assert.equal(registration.id, manifest.name);
    assert.equal(typeof registration.factory, 'function');
    modules.push(registration.factory(require));
  } };
  dom.window.eval(source);
  assert.equal(modules.length, 1);
  assert.equal(typeof modules[0].apply, 'function');
  assert.equal(modules[0].name, manifest.name);
  assert.deepEqual(Array.from(modules[0].inject), ['connection', 'slots']);
  assert.equal(dom.window.document.querySelectorAll('style[data-dsh-chatpass]').length, 1);
  console.log('PASS classic-script plugin registration, factory exports, and inline CSS:', filename);
} finally { dom.window.close(); }
