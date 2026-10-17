import { describe, expect, it, vi } from 'vitest';
import { ChatPassClientStore, type CommandTransport } from '../src/client-store.js';
import { DEFAULT_BASE_URL, type PublicState } from '../src/contracts.js';
import type { ConnectionRpcResult } from '@deepseek-ai/dsh-client-connection/client';

const state = (revision = 0): PublicState => ({ revision, configured: false, settings: { baseURL: DEFAULT_BASE_URL, contextWindow: 0, maxTokens: 0 }, models: [], discoveredAt: null, stale: true, lastError: null, secretStorage: 'memory' });
describe('shared browser state owner', () => {
  it('loads status once across consumers and repulls only on reset or explicit action', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValue({ ok: true, value: state() });
    const store = new ChatPassClientStore(transport);
    const one = vi.fn(); const two = vi.fn();
    store.subscribe(one); store.subscribe(two);
    await Promise.all([store.start(), store.start()]);
    expect(transport.mock.calls).toEqual([[{ action: 'status' }]]);
    expect(one).toHaveBeenCalled(); expect(two).toHaveBeenCalled();
    await store.reset();
    expect(transport.mock.calls).toEqual([[{ action: 'status' }], [{ action: 'status' }]]);
  });
  it('ignores old generation responses and prevents duplicate mutations', async () => {
    let finish: (value: ConnectionRpcResult<unknown>) => void = () => {};
    const transport = vi.fn<CommandTransport>()
      .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }))
      .mockResolvedValue({ ok: true, value: state(2) });
    const store = new ChatPassClientStore(transport);
    const old = store.start();
    expect(await store.run({ action: 'remove', revision: 0 })).toBe(false);
    await store.reset(); finish({ ok: true, value: state(1) }); await old;
    expect(store.getSnapshot().state?.revision).toBe(2);
    expect(store.getSnapshot().busy).toBe(false);
  });
  it('never exposes arbitrary remote error text, preserves state on failure, and recovers manually', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValueOnce({ ok: true, value: state() })
      .mockResolvedValueOnce({ ok: false, error: { code: 'conflict', message: 'secret remote text', details: {} } })
      .mockRejectedValueOnce(new Error('sensitive text'))
      .mockResolvedValueOnce({ ok: true, value: state(3) });
    const store = new ChatPassClientStore(transport); await store.start();
    await store.run({ action: 'refresh', revision: 0 });
    expect(store.getSnapshot().error).toBe('conflict');
    expect(JSON.stringify(store.getSnapshot())).not.toContain('secret remote text');
    expect(store.getSnapshot().state?.revision).toBe(0);
    await store.run({ action: 'status' }); expect(store.getSnapshot().error).toBe('offline');
    await store.run({ action: 'status' }); expect(store.getSnapshot().error).toBe(null);
    expect(store.getSnapshot().state?.revision).toBe(3);
  });
  it('projects retained plugin drafts to nonsecret fields and marks them conflicted on reset', async () => {
    const store = new ChatPassClientStore(vi.fn<CommandTransport>().mockResolvedValue({ ok: true, value: state() }));
    const extra = { ...state().settings, apiKey: 'do-not-store-me' };
    store.retainDraft({ settings: extra, base: extra, revision: 0, generation: 0, conflict: false });
    expect(JSON.stringify(store.getRetainedDraft())).not.toContain('do-not-store-me');
    expect(store.getRetainedDraft()?.settings).not.toHaveProperty('apiKey');
    await store.reset(); expect(store.getRetainedDraft()?.conflict).toBe(true);
    store.retainDraft(null); expect(store.getRetainedDraft()).toBeNull();
  });
  it('accepts only validated public snapshots from failure details while preserving the failure code', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValueOnce({ ok: true, value: state() })
      .mockResolvedValueOnce({ ok: false, error: { code: 'offline', message: 'unsafe', details: { state: { ...state(1), stale: true, lastError: 'offline' } } } })
      .mockResolvedValueOnce({ ok: false, error: { code: 'timeout', message: 'unsafe', details: { state: { revision: 99 } } } });
    const store = new ChatPassClientStore(transport); await store.start();
    await store.run({ action: 'refresh', revision: 0 });
    expect(store.getSnapshot().state?.revision).toBe(1); expect(store.getSnapshot().error).toBe('offline');
    await store.run({ action: 'refresh', revision: 1 });
    expect(store.getSnapshot().state?.revision).toBe(1); expect(store.getSnapshot().error).toBe('timeout');
  });
  it('rejects malformed successful responses and unknown error codes', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValueOnce({ ok: true, value: { revision: 1 } })
      .mockResolvedValueOnce({ ok: false, error: { code: '__proto__', message: 'unsafe', details: {} } });
    const store = new ChatPassClientStore(transport); await store.start();
    expect(store.getSnapshot().error).toBe('protocol');
    await store.run({ action: 'status' }); expect(store.getSnapshot().error).toBe('internal');
  });
});
