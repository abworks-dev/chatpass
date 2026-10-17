import { createServer, type Server, type IncomingMessage, type ServerResponse } from 'node:http';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { discover, gatewayFetch, validateBaseURL } from '../src/transport.js';
import type { ConnectionSnapshot } from '../src/connection.js';
const servers: Server[] = [];
afterEach(async () => { await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => { server.closeAllConnections(); server.close(() => resolve()); }))); });
async function fixture(handler: (req: IncomingMessage, res: ServerResponse) => void): Promise<ConnectionSnapshot> {
  const server = createServer(handler); servers.push(server);
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('fixture address');
  return { settings: { baseURL: `http://127.0.0.1:${address.port}/v1`, contextWindow: 100, maxTokens: 10 }, apiKey: 'fixture-key', models: ['m'] };
}
describe('restricted loopback transport', () => {
  it('allows only exact IPv4 HTTP loopback and v1 path', () => {
    expect(validateBaseURL('http://127.0.0.1:19431/v1')).toBe('http://127.0.0.1:19431/v1');
    for (const url of ['https://127.0.0.1/v1', 'http://localhost/v1', 'http://127.1/v1', 'http://2130706433/v1', 'http://[::1]/v1', 'http://127.0.0.1:0/v1', 'http://127.0.0.1:65536/v1', 'http://x@127.0.0.1/v1', 'http://127.0.0.1/v1/', 'http://127.0.0.1/v1?x', 'http://127.0.0.1/v1#x', 'http://192.168.1.1/v1']) expect(() => validateBaseURL(url)).toThrow();
  });
  it('discovers real IDs without guessing metadata or exposing key', async () => {
    let authorization: string | undefined;
    const snapshot = await fixture((req, res) => { authorization = req.headers.authorization; res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ data: [{ id: 'actual' }, { id: 'actual' }, { id: 'second' }] })); });
    expect(await discover(snapshot)).toEqual(['actual', 'second']);
    expect(authorization).toBe('Bearer fixture-key');
  });
  it('never follows redirects and classifies auth/status safely', async () => {
    for (const [status, code] of [[302, 'redirect'], [401, 'unauthorized'], [403, 'forbidden'], [500, 'protocol']] as const) {
      let requests = 0;
      const snapshot = await fixture((_req, res) => { ++requests; res.writeHead(status, { location: 'http://example.com', 'content-type': 'application/json' }); res.end('private-body'); });
      await expect(discover(snapshot)).rejects.toMatchObject({ code });
      expect(requests).toBe(1);
    }
  });
  it('bounds and validates catalog schema, content type and size', async () => {
    for (const body of ['not json', '{}', '{"data":[{"id":"invalid\\nmodel"}]}', JSON.stringify({ data: Array(513).fill({ id: 'm' }) }), ' '.repeat(256 * 1024 + 1)]) {
      const snapshot = await fixture((_req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(body); });
      await expect(discover(snapshot)).rejects.toMatchObject({ code: 'protocol' });
    }
    const snapshot = await fixture((_req, res) => { res.writeHead(200, { 'content-type': 'text/plain' }); res.end('{}'); });
    await expect(discover(snapshot)).rejects.toMatchObject({ code: 'protocol' });
  });
  it('rejects caller paths and headers cannot replace captured auth', async () => {
    let auth: string | undefined;
    const snapshot = await fixture((req, res) => { auth = req.headers.authorization; res.writeHead(200, { 'content-type': 'text/event-stream' }); res.end('data: fixture\n\n'); });
    const fetch = gatewayFetch(snapshot);
    await expect(fetch(snapshot.settings.baseURL + '/chat/completions', { method: 'POST' })).rejects.toMatchObject({ code: 'invalid' });
    const response = await fetch(snapshot.settings.baseURL + '/responses', { method: 'POST', body: '{}', headers: { authorization: 'Bearer injected' } });
    expect(await response.text()).toContain('fixture');
    expect(auth).toBe('Bearer fixture-key');
  });
  it('bounds catalog total time even after a partial response', async () => {
    let received!: () => void;
    const partial = new Promise<void>(resolve => { received = resolve; });
    const snapshot = await fixture((_req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.write('{"data":'); received(); });
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    try {
      const result = expect(discover(snapshot)).rejects.toMatchObject({ code: 'timeout' });
      await partial;
      await vi.advanceTimersByTimeAsync(10_000);
      await result;
    } finally { vi.useRealTimers(); }
  });
  it('bounds Responses headers wait and buffered request size', async () => {
    let received!: () => void;
    const accepted = new Promise<void>(resolve => { received = resolve; });
    let requests = 0;
    const snapshot = await fixture(() => { ++requests; received(); });
    const fetch = gatewayFetch(snapshot);
    await expect(fetch(snapshot.settings.baseURL + '/responses', { method: 'POST', body: 'x'.repeat(16 * 1024 * 1024 + 1) })).rejects.toMatchObject({ code: 'invalid' });
    expect(requests).toBe(0);
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    try {
      const result = expect(fetch(snapshot.settings.baseURL + '/responses', { method: 'POST', body: '{}' })).rejects.toMatchObject({ code: 'timeout' });
      await accepted;
      await vi.advanceTimersByTimeAsync(30_000);
      await result;
      expect(requests).toBe(1);
    } finally { vi.useRealTimers(); }
  });
  it('aborts an open stream rather than leaving socket in flight', async () => {
    const snapshot = await fixture((_req, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': open\n\n'); });
    const controller = new AbortController();
    const response = await gatewayFetch(snapshot)(snapshot.settings.baseURL + '/responses', { method: 'POST', body: '{}', signal: controller.signal });
    controller.abort();
    await expect(response.text()).rejects.toMatchObject({ code: 'cancelled' });
  });
});
