import { DEFAULT_BASE_URL, type PublicState, type SaveInput, type Settings } from './contracts.js';
import { ChatPassError, safeError } from './errors.js';
import { discover, validateBaseURL } from './transport.js';

export interface ConnectionSnapshot {
  readonly settings: Readonly<Settings>;
  readonly apiKey: string;
  readonly models: readonly string[];
}
type Discover = (snapshot: ConnectionSnapshot, signal?: AbortSignal) => Promise<readonly string[]>;

function settingsOf(input: SaveInput): Readonly<Settings> {
  const baseURL = validateBaseURL(input.baseURL);
  if (!Number.isSafeInteger(input.contextWindow) || input.contextWindow < 1 || input.contextWindow > 10_000_000 ||
      !Number.isSafeInteger(input.maxTokens) || input.maxTokens < 1 || input.maxTokens > input.contextWindow) throw new ChatPassError('invalid');
  return Object.freeze({ baseURL, contextWindow: input.contextWindow, maxTokens: input.maxTokens });
}

/** Sole runtime connection owner. All state/credentials are process-memory only.
 * A save validates before publishing; conflicts never silently replace another edit.
 */
export class ConnectionOwner {
  private revision = 0;
  private snapshot: ConnectionSnapshot | undefined;
  private settings: Settings = Object.freeze({ baseURL: DEFAULT_BASE_URL, contextWindow: 0, maxTokens: 0 });
  private discoveredAt: string | null = null;
  private lastError: PublicState['lastError'] = null;
  constructor(private readonly discoverModels: Discover = discover) {}
  state(): PublicState {
    return {
      revision: this.revision, configured: !!this.snapshot,
      settings: { ...this.settings }, models: [...(this.snapshot?.models ?? [])],
      discoveredAt: this.discoveredAt,
      stale: this.lastError !== null || this.discoveredAt === null || Date.now() - Date.parse(this.discoveredAt) > 300_000,
      lastError: this.lastError, secretStorage: 'memory',
    };
  }
  capture(): ConnectionSnapshot {
    if (!this.snapshot) throw new ChatPassError('unconfigured');
    return this.snapshot;
  }
  private check(revision: number) {
    if (!Number.isSafeInteger(revision) || revision !== this.revision) throw new ChatPassError('conflict');
  }
  async save(input: SaveInput, signal?: AbortSignal): Promise<PublicState> {
    this.check(input.revision);
    const settings = settingsOf(input);
    // No implicit reuse when changing endpoint: sending a previous key elsewhere needs explicit input.
    const apiKey = input.apiKey ?? (this.snapshot?.settings.baseURL === settings.baseURL ? this.snapshot.apiKey : undefined);
    if (typeof apiKey !== 'string' || !/^[\x21-\x7e]{1,4096}$/.test(apiKey)) throw new ChatPassError('invalid');
    const candidate = Object.freeze({ settings, apiKey, models: Object.freeze([] as string[]) });
    // An unsuccessful candidate never changes the active configuration or its catalog status.
    const models = await this.discoverModels(candidate, signal);
    if (signal?.aborted) throw new ChatPassError('cancelled');
    this.check(input.revision);
    this.snapshot = Object.freeze({ settings, apiKey, models: Object.freeze([...models]) });
    this.settings = settings;
    this.discoveredAt = new Date().toISOString();
    this.lastError = null;
    ++this.revision;
    return this.state();
  }
  async refresh(revision: number, signal?: AbortSignal): Promise<PublicState> {
    this.check(revision);
    const snapshot = this.capture();
    try {
      const models = await this.discoverModels(snapshot, signal);
      if (signal?.aborted) throw new ChatPassError('cancelled');
      this.check(revision);
      this.snapshot = Object.freeze({ ...snapshot, models: Object.freeze([...models]) });
      this.discoveredAt = new Date().toISOString();
      this.lastError = null;
      ++this.revision;
      return this.state();
    } catch (error) {
      if (this.revision === revision) this.lastError = safeError(error).code;
      throw error;
    }
  }
  remove(revision: number): PublicState {
    this.check(revision);
    this.snapshot = undefined;
    this.settings = Object.freeze({ baseURL: DEFAULT_BASE_URL, contextWindow: 0, maxTokens: 0 });
    this.discoveredAt = null;
    this.lastError = null;
    ++this.revision;
    return this.state();
  }
}
