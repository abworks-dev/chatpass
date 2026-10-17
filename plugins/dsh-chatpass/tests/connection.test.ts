import { describe, it, expect, vi } from 'vitest';
import { ConnectionOwner } from '../src/connection.js';
import { ChatPassError } from '../src/errors.js';
import { executeCommand } from '../src/host-api.js';
const settings = { baseURL: 'http://127.0.0.1:19431/v1', contextWindow: 10000, maxTokens: 1000 };
const input = { ...settings, revision: 0, apiKey: 'fake-key-never-real' };

describe('atomic connection owner', () => {
  it('starts empty and returns no secret in public state', async () => {
    const owner = new ConnectionOwner(async () => ['model-a']);
    expect(owner.state().configured).toBe(false);
    expect(() => owner.capture()).toThrow();
    await owner.save(input);
    expect(owner.state()).toMatchObject({ revision: 1, configured: true, models: ['model-a'], stale: false, secretStorage: 'memory' });
    expect(JSON.stringify(owner.state())).not.toContain(input.apiKey);
    expect(owner.capture()).toBe(owner.capture());
  });
  it('failed candidate preserves previous complete state and snapshot', async () => {
    const discover = vi.fn().mockResolvedValueOnce(['old']).mockRejectedValueOnce(new ChatPassError('unauthorized'));
    const owner = new ConnectionOwner(discover);
    await owner.save(input);
    const before = owner.state(), captured = owner.capture();
    await expect(owner.save({ ...input, revision: 1, apiKey: 'bad-key' })).rejects.toMatchObject({ code: 'unauthorized' });
    expect(owner.state()).toEqual(before);
    expect(owner.capture()).toBe(captured);
  });
  it('keeps old request snapshots frozen on save/remove', async () => {
    const owner = new ConnectionOwner(async () => ['m']);
    await owner.save(input);
    const old = owner.capture();
    await owner.save({ ...input, revision: 1, apiKey: 'new-fake-key' });
    expect(old.apiKey).toBe(input.apiKey);
    expect(owner.capture().apiKey).toBe('new-fake-key');
    owner.remove(2);
    expect(old.models).toEqual(['m']);
    expect(() => owner.capture()).toThrow();
  });
  it('rejects stale edits including discovery racing with remove', async () => {
    let release!: (models: string[]) => void;
    const owner = new ConnectionOwner(() => new Promise(resolve => { release = resolve; }));
    const save = owner.save(input);
    owner.remove(0);
    release(['m']);
    await expect(save).rejects.toMatchObject({ code: 'conflict' });
    expect(owner.state().configured).toBe(false);
    await expect(owner.save(input)).rejects.toMatchObject({ code: 'conflict' });
  });
  it('does not implicitly send an old key to a changed endpoint', async () => {
    const discover = vi.fn(async () => ['m']);
    const owner = new ConnectionOwner(discover);
    await owner.save(input);
    await expect(owner.save({ ...settings, baseURL: 'http://127.0.0.1:19432/v1', revision: 1 })).rejects.toMatchObject({ code: 'invalid' });
    expect(discover).toHaveBeenCalledTimes(1);
    await owner.save({ ...settings, revision: 1 });
    expect(owner.capture().apiKey).toBe(input.apiKey);
  });
  it('refresh failure retains catalog but marks stale with safe category', async () => {
    const owner = new ConnectionOwner(vi.fn().mockResolvedValueOnce(['old']).mockRejectedValueOnce(new ChatPassError('offline')));
    await owner.save(input);
    await expect(owner.refresh(1)).rejects.toThrow();
    expect(owner.state()).toMatchObject({ models: ['old'], revision: 1, stale: true, lastError: 'offline' });
  });
  it('rejects capacities, empty/newline/oversize keys, and aborted commit', async () => {
    const owner = new ConnectionOwner(async () => ['m']);
    for (const apiKey of ['', 'bad\nkey', 'x'.repeat(4097)]) await expect(owner.save({ ...input, apiKey })).rejects.toMatchObject({ code: 'invalid' });
    for (const capacity of [0, -1, 1.5, Infinity, 10_000_001]) await expect(owner.save({ ...input, contextWindow: capacity })).rejects.toMatchObject({ code: 'invalid' });
    await expect(owner.save({ ...input, maxTokens: 10001 })).rejects.toThrow();
    await expect(owner.save(input, AbortSignal.abort())).rejects.toMatchObject({ code: 'cancelled' });
    expect(owner.state().revision).toBe(0);
  });
});

describe('command parser seam (host admission tested separately)', () => {
  it('publishes safe retained state on a failed refresh', async () => {
    const owner = new ConnectionOwner(vi.fn().mockResolvedValueOnce(['old']).mockRejectedValueOnce(new ChatPassError('offline')));
    await owner.save(input);
    const result = await executeCommand(owner, { action: 'refresh', revision: 1 }, new AbortController().signal);
    expect(result).toMatchObject({ ok: false, error: { code: 'offline', details: { state: { models: ['old'], revision: 1, stale: true, lastError: 'offline' } } } });
    expect(JSON.stringify(result)).not.toContain(input.apiKey);
  });
  it('accepts closed bounded shapes and never leaks untrusted errors', async () => {
    const owner = new ConnectionOwner(async () => { throw new Error('secret-upstream-body'); });
    const signal = new AbortController().signal;
    for (const payload of [null, [], { action: 'status', apiKey: 'injected' }, { action: 'other' }, { action: 'remove', revision: -1 }, { action: 'save', ...input, apiKey: 'x'.repeat(4097) }]) {
      expect(await executeCommand(owner, payload, signal)).toMatchObject({ ok: false, error: { code: 'invalid' } });
    }
    const failed = await executeCommand(owner, { action: 'save', ...input }, signal);
    expect(failed).toMatchObject({ ok: false, error: { code: 'internal' } });
    expect(JSON.stringify(failed)).not.toContain('secret-upstream-body');
    expect(await executeCommand(owner, { action: 'status' }, signal)).toMatchObject({ ok: true, value: { configured: false } });
  });
});
