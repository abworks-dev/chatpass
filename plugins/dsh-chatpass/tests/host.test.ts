import { describe, expect, it, vi } from 'vitest';
import type { Context } from '@deepseek-ai/cordis';
import { apply } from '../src/index.js';
import type { executeCommand } from '../src/host-api.js';

// Deliberately narrow SDK service fixture; real admission is owned by Connection,
// not simulated here. The real browser/authentication pass remains a separate gate.
describe('host wiring', () => {
  it('uses a private authenticated Connection channel and disposes its owner', async () => {
    type Reply = Awaited<ReturnType<typeof executeCommand>>;
    let command!: (endpoint: string, payload: unknown, signal: AbortSignal) => Promise<Reply>;
    const disposeAdapter = Object.assign(vi.fn(), { replace: vi.fn() });
    const removeChannel = vi.fn();
    const cleanups: (() => void)[] = [];
    const handle = vi.fn((channel, handler) => { expect(channel).toBe('/chatpass'); command = handler; return removeChannel; });
    const ctx = {
      llm: { registerAdapter: vi.fn(() => disposeAdapter) },
      connection: { rpc: { handle } },
      effect(factory: () => () => void) { cleanups.push(factory()); },
    };
    apply(ctx as unknown as Context);
    expect(ctx.llm.registerAdapter).toHaveBeenCalledWith(['chatpass'], expect.anything());
    const signal = new AbortController().signal;
    expect(await command('other', { action: 'remove', revision: 0 }, signal)).toMatchObject({ ok: false });
    expect(await command('command', { action: 'status' }, signal)).toMatchObject({ ok: true, value: { revision: 0 } });
    expect(disposeAdapter.replace).not.toHaveBeenCalled();
    expect(await command('command', { action: 'remove', revision: 0 }, signal)).toMatchObject({ ok: true, value: { revision: 1 } });
    expect(disposeAdapter.replace).toHaveBeenCalledOnce();
    expect(await command('command', { action: 'remove', revision: 0 }, signal)).toMatchObject({ ok: false, error: { code: 'conflict' } });
    expect(disposeAdapter.replace).toHaveBeenCalledOnce();
    cleanups.reverse().forEach(dispose => dispose());
    expect(disposeAdapter).toHaveBeenCalledOnce();
    expect(removeChannel).toHaveBeenCalledOnce();
  });
});
