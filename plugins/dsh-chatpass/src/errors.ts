import { ERROR_TEXT, type ErrorCode } from './contracts.js';
export class ChatPassError extends Error {
  constructor(public readonly code: ErrorCode) { super(ERROR_TEXT[code]); this.name = 'ChatPassError'; }
}
export function safeError(error: unknown): ChatPassError {
  return error instanceof ChatPassError ? error : new ChatPassError('internal');
}
export function statusError(status: number): ChatPassError {
  return new ChatPassError(status === 401 ? 'unauthorized' : status === 403 ? 'forbidden' : status === 429 ? 'rate_limited' : status >= 300 && status < 400 ? 'redirect' : 'protocol');
}
