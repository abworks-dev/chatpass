import { request as httpRequest } from 'node:http';
import type { ConnectionSnapshot } from './connection.js';
import { ChatPassError, statusError } from './errors.js';

export const CATALOG_LIMIT = 256 * 1024;
const REQUEST_LIMIT = 16 * 1024 * 1024;
// Check the original spelling before URL normalization (127.1/hex/DNS are not grants).
export function validateBaseURL(value: unknown): string {
  if (typeof value !== 'string' || !/^http:\/\/127\.0\.0\.1(?::[1-9][0-9]{0,4})?\/v1$/.test(value)) throw new ChatPassError('invalid');
  const url = new URL(value);
  if (url.port && (Number(url.port) > 65535 || Number(url.port) === 0)) throw new ChatPassError('invalid');
  return url.origin + '/v1';
}

/** Native node:http deliberately bypasses global fetch and proxy environments.
 * No redirects, redirects-as-success, DNS aliases, retries, or arbitrary paths.
 * Authentication always comes from the captured host snapshot, never caller headers.
 */
export function gatewayFetch(snapshot: ConnectionSnapshot): typeof globalThis.fetch {
  const base = validateBaseURL(snapshot.settings.baseURL);
  return async (input, init) => {
    const incoming = new Request(input, init);
    const isCatalog = incoming.url === base + '/models' && incoming.method === 'GET';
    if (!isCatalog && !(incoming.url === base + '/responses' && incoming.method === 'POST')) throw new ChatPassError('invalid');
    if (incoming.signal.aborted) throw new ChatPassError('cancelled');
    // SDK emits a buffered JSON string; cap before copying it to a socket.
    const requestBody = incoming.body ? Buffer.from(await incoming.arrayBuffer()) : undefined;
    if (requestBody && requestBody.byteLength > REQUEST_LIMIT) throw new ChatPassError('invalid');
    return new Promise<Response>((resolve, reject) => {
      let timer: ReturnType<typeof setTimeout>;
      let finished = false;
      let controller: ReadableStreamDefaultController<Uint8Array> | undefined;
      const cleanup = () => { clearTimeout(timer); incoming.signal.removeEventListener('abort', abort); };
      const fail = (error: ChatPassError) => {
        if (finished) return;
        finished = true;
        cleanup();
        if (controller) controller.error(error); else reject(error);
        req.destroy();
      };
      const arm = (ms: number) => { clearTimeout(timer); timer = setTimeout(() => fail(new ChatPassError('timeout')), ms); timer.unref(); };
      const abort = () => fail(new ChatPassError('cancelled'));
      const req = httpRequest(incoming.url, {
        method: incoming.method,
        agent: false,
        headers: {
          authorization: `Bearer ${snapshot.apiKey}`,
          accept: isCatalog ? 'application/json' : 'text/event-stream',
          ...(requestBody ? { 'content-type': 'application/json', 'content-length': String(requestBody.byteLength) } : {}),
        },
      }, (res) => {
        if (finished) { res.destroy(); return; }
        const status = res.statusCode ?? 502;
        if (status < 200 || status >= 300) { res.destroy(); fail(statusError(status)); return; }
        const contentType = String(res.headers['content-type'] ?? '').split(';')[0].trim();
        if (contentType !== (isCatalog ? 'application/json' : 'text/event-stream')) { res.destroy(); fail(new ChatPassError('protocol')); return; }
        let bytes = 0;
        const body = new ReadableStream<Uint8Array>({
          start(c) {
            controller = c;
            res.on('data', (chunk: Buffer) => {
              if (finished) return;
              bytes += chunk.byteLength;
              if (isCatalog && bytes > CATALOG_LIMIT) { fail(new ChatPassError('protocol')); return; }
              if (!isCatalog) arm(300_000);
              c.enqueue(new Uint8Array(chunk));
              if ((c.desiredSize ?? 1) <= 0) res.pause();
            });
            res.on('end', () => { if (!finished) { finished = true; cleanup(); c.close(); } });
            res.on('error', () => fail(new ChatPassError('protocol')));
            res.on('aborted', () => fail(new ChatPassError('protocol')));
          },
          pull() { res.resume(); },
          cancel() { finished = true; cleanup(); res.destroy(); req.destroy(); },
        });
        // Catalog total timer stays bounded even for a slow trickle of bytes.
        if (!isCatalog) arm(300_000);
        resolve(new Response(body, { status, headers: { 'content-type': contentType } }));
      });
      req.on('error', () => fail(new ChatPassError('offline')));
      incoming.signal.addEventListener('abort', abort, { once: true });
      arm(isCatalog ? 10_000 : 30_000);
      if (incoming.signal.aborted) { abort(); return; }
      req.end(requestBody);
    });
  };
}

export async function discover(snapshot: ConnectionSnapshot, signal?: AbortSignal): Promise<readonly string[]> {
  const response = await gatewayFetch(snapshot)(snapshot.settings.baseURL + '/models', { signal });
  let value: unknown;
  try { value = await response.json(); } catch (error) {
    if (error instanceof ChatPassError) throw error;
    throw new ChatPassError('protocol');
  }
  if (!value || typeof value !== 'object' || !('data' in value) || !Array.isArray(value.data) || value.data.length > 512) throw new ChatPassError('protocol');
  const ids: string[] = [];
  for (const item of value.data) {
    if (!item || typeof item !== 'object' || typeof item.id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$/.test(item.id)) throw new ChatPassError('protocol');
    ids.push(item.id);
  }
  return Object.freeze([...new Set(ids)]);
}
