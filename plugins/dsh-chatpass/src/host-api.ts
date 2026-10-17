import { ConnectionOwner } from './connection.js';
import type { Command } from './contracts.js';
import { ChatPassError, safeError } from './errors.js';

// The shared authenticated Connection carrier owns Host/Origin/cookie checks.
// Validate a closed, bounded shape before any socket operation; never echo payloads.
export function parseCommand(payload: unknown): Command {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new ChatPassError('invalid');
  const value = payload as Record<string, unknown>;
  const allowed = value.action === 'status' ? ['action'] : value.action === 'save'
    ? ['action', 'revision', 'baseURL', 'apiKey', 'contextWindow', 'maxTokens'] : ['action', 'revision'];
  if (Object.keys(value).some(key => !allowed.includes(key))) throw new ChatPassError('invalid');
  if (value.action === 'status') return { action: 'status' };
  if (!Number.isSafeInteger(value.revision) || typeof value.revision !== 'number' || value.revision < 0) throw new ChatPassError('invalid');
  if (value.action === 'refresh' || value.action === 'remove') return { action: value.action, revision: value.revision };
  if (value.action !== 'save' || typeof value.baseURL !== 'string' || value.baseURL.length > 100 ||
      typeof value.contextWindow !== 'number' || typeof value.maxTokens !== 'number' ||
      (value.apiKey !== undefined && (typeof value.apiKey !== 'string' || value.apiKey.length > 4096))) throw new ChatPassError('invalid');
  return { action: 'save', revision: value.revision, baseURL: value.baseURL,
    contextWindow: value.contextWindow, maxTokens: value.maxTokens,
    ...(typeof value.apiKey === 'string' ? { apiKey: value.apiKey } : {}) };
}
export async function executeCommand(owner: ConnectionOwner, payload: unknown, signal: AbortSignal) {
  try {
    if (signal.aborted) throw new ChatPassError('cancelled');
    const command = parseCommand(payload);
    const value = command.action === 'status' ? owner.state()
      : command.action === 'save' ? await owner.save(command, signal)
      : command.action === 'refresh' ? await owner.refresh(command.revision, signal)
      : owner.remove(command.revision);
    return { ok: true as const, value };
  } catch (error) {
    const safe = safeError(error);
    return { ok: false as const, error: { code: safe.code, message: safe.message, details: { state: owner.state() } } };
  }
}
