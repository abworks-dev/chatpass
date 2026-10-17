import type { ConnectionRpcResult } from '@deepseek-ai/dsh-client-connection/client';
import { ERROR_TEXT, type Command, type ErrorCode, type PublicState, type Settings } from './contracts.js';

export type CommandTransport = (command: Command) => Promise<ConnectionRpcResult<unknown>>;
export interface RetainedDraft {
  settings: Settings;
  base: Settings;
  revision: number;
  generation: number;
  conflict: boolean;
}
export interface ClientSnapshot {
  state: PublicState | null;
  busy: boolean;
  error: ErrorCode | null;
  generation: number;
}
function errorCode(value: string): ErrorCode {
  return Object.prototype.hasOwnProperty.call(ERROR_TEXT, value)
    ? Object.keys(ERROR_TEXT).find((key): key is ErrorCode => key === value) ?? 'internal'
    : 'internal';
}
function publicState(value: unknown): value is PublicState {
  if (typeof value !== 'object' || value === null) return false;
  if (!('revision' in value) || !Number.isSafeInteger(value.revision) || typeof value.revision !== 'number' || value.revision < 0) return false;
  if (!('settings' in value) || typeof value.settings !== 'object' || value.settings === null) return false;
  const settings = value.settings;
  return 'baseURL' in settings && typeof settings.baseURL === 'string'
    && 'contextWindow' in settings && typeof settings.contextWindow === 'number'
    && 'maxTokens' in settings && typeof settings.maxTokens === 'number'
    && 'configured' in value && typeof value.configured === 'boolean'
    && 'models' in value && Array.isArray(value.models) && value.models.every((model: unknown) => typeof model === 'string')
    && 'discoveredAt' in value && (value.discoveredAt === null || typeof value.discoveredAt === 'string')
    && 'stale' in value && typeof value.stale === 'boolean'
    && 'lastError' in value && (value.lastError === null || (typeof value.lastError === 'string' && Object.prototype.hasOwnProperty.call(ERROR_TEXT, value.lastError)))
    && 'secretStorage' in value && value.secretStorage === 'memory';
}

/** One host-state owner; never stores a secret or performs model discovery on status. */
export class ChatPassClientStore {
  private snapshot: ClientSnapshot = { state: null, busy: false, error: null, generation: 0 };
  private listeners = new Set<() => void>();
  private started = false;
  private sequence = 0;
  private retainedDraft: RetainedDraft | null = null;
  constructor(private readonly transport: CommandTransport) {}
  getRetainedDraft(): RetainedDraft | null { return this.retainedDraft; }
  retainDraft(draft: RetainedDraft | null): void {
    // Explicit projection: never retain a password even if a caller has extra fields.
    const settings = (value: Settings): Settings => ({ baseURL: value.baseURL, contextWindow: value.contextWindow, maxTokens: value.maxTokens });
    this.retainedDraft = draft ? {
      settings: settings(draft.settings), base: settings(draft.base), revision: draft.revision,
      generation: draft.generation, conflict: draft.conflict,
    } : null;
  }
  getSnapshot = (): ClientSnapshot => this.snapshot;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  private publish(update: Partial<ClientSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...update };
    this.listeners.forEach((listener) => listener());
  }
  start(): Promise<boolean> {
    if (this.started) return Promise.resolve(true);
    this.started = true;
    return this.run({ action: 'status' });
  }
  reset(): Promise<boolean> {
    this.started = true;
    ++this.sequence;
    if (this.retainedDraft) this.retainedDraft = { ...this.retainedDraft, conflict: true };
    this.publish({ busy: false, state: null, error: null, generation: this.snapshot.generation + 1 });
    return this.run({ action: 'status' });
  }
  async run(command: Command): Promise<boolean> {
    if (this.snapshot.busy) return false;
    const sequence = ++this.sequence;
    this.publish({ busy: true, error: null });
    try {
      const result = await this.transport(command);
      if (sequence !== this.sequence) return false;
      if (!result.ok) {
        const details = result.error.details;
        const failureState = 'state' in details && publicState(details.state) ? details.state : null;
        this.publish({ error: errorCode(result.error.code), ...(failureState ? { state: failureState } : {}) });
        return false;
      }
      if (!publicState(result.value)) {
        this.publish({ error: 'protocol' });
        return false;
      }
      this.publish({ state: result.value });
      return true;
    } catch {
      if (sequence === this.sequence) this.publish({ error: 'offline' });
      return false;
    } finally {
      if (sequence === this.sequence) this.publish({ busy: false });
    }
  }
}
