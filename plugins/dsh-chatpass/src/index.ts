import type { Context } from '@deepseek-ai/cordis';
import '@deepseek-ai/dsh-client-connection';
import '@deepseek-ai/dsh-llm';
import { ConnectionOwner } from './connection.js';
import { executeCommand } from './host-api.js';
import { createAdapter } from './provider.js';

export const name = 'dsh-chatpass';
export const inject = ['llm', 'connection'];
export function apply(ctx: Context): void {
  const owner = new ConnectionOwner();
  const registration = ctx.llm.registerAdapter(['chatpass'], createAdapter(owner));
  ctx.effect(() => registration);
  ctx.effect(() => ctx.connection.rpc.handle('/chatpass',
    async (endpoint, payload, signal) => {
      if (endpoint !== 'command') return { ok: false as const, error: { code: 'invalid', message: 'Unknown ChatPass command', details: {} } };
      const previous = owner.state().revision;
      const result = await executeCommand(owner, payload, signal);
      if (result.ok && result.value.revision !== previous) registration.replace(['chatpass']);
      return result;
    }));
  ctx.effect(() => () => { owner.remove(owner.state().revision); });
}
