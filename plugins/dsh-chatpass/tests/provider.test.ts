import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createAssistantMessage, createToolResultMessage, type GenerateOptions, type StreamChunk } from '@deepseek-ai/dsh-llm';
import { createAdapter, PROVIDER_ID } from '../src/provider.js';
import { ConnectionOwner } from '../src/connection.js';
import type { ConnectionSnapshot } from '../src/connection.js';

const mock = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock('../src/transport.js', async (original) => ({
  ...await original<typeof import('../src/transport.js')>(),
  // Match the transport contract; the SDK may supply URL+init or a Request.
  gatewayFetch: (snapshot: ConnectionSnapshot) => (input: RequestInfo | URL, init?: RequestInit) => mock.fetch(snapshot, new Request(input, init)),
}));
const textItem = { type: 'message', id: 'msg_1', role: 'assistant', status: 'completed', content: [{ type: 'output_text', text: 'hello', annotations: [] }] };
const usage = { input_tokens: 20, output_tokens: 4, total_tokens: 24, input_tokens_details: { cached_tokens: 0 }, output_tokens_details: { reasoning_tokens: 2 } };
const terminal = (status = 'completed', extra = {}) => ({ type: `response.${status}`, response: { id: 'resp_1', status, output: [], usage, ...extra } });
function sse(events: unknown[]): Response {
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), { headers: { 'content-type': 'text/event-stream' } });
}
function textEvents(status = 'completed', extra = {}): unknown[] {
  return [
    { type: 'response.created', response: { id: 'resp_1' } },
    { type: 'response.output_item.added', output_index: 0, item: { ...textItem, content: [] } },
    { type: 'response.output_text.delta', output_index: 0, delta: 'hello' },
    { type: 'response.output_item.done', output_index: 0, item: textItem },
    terminal(status, { output: [textItem], ...extra }),
  ];
}
async function setup() {
  const owner = new ConnectionOwner(async () => ['fixture-model']);
  await owner.save({ revision: 0, baseURL: 'http://127.0.0.1:4343/v1', apiKey: 'test-key', contextWindow: 12345, maxTokens: 512 });
  return { owner, adapter: createAdapter(owner) };
}
const options = (extra: Partial<GenerateOptions> = {}): GenerateOptions => ({ provider: PROVIDER_ID, model: 'fixture-model', messages: [{ role: 'user', content: [{ type: 'text', text: 'question' }] }], ...extra });
async function collect(stream: AsyncIterable<StreamChunk>) { const chunks: StreamChunk[] = []; for await (const chunk of stream) chunks.push(chunk); return chunks; }
const finish = (chunks: StreamChunk[]) => chunks.findLast((chunk) => chunk.type === 'finish');

beforeEach(() => { mock.fetch.mockReset(); });
describe('real pi-ai Responses SDK / Harness adapter', () => {
  it('has an empty initial catalog and no invented model/capacity', async () => {
    const adapter = createAdapter(new ConnectionOwner());
    expect(adapter.providerInfo(PROVIDER_ID).name).toBe('ChatPass');
    expect(await adapter.listModels(PROVIDER_ID)).toEqual([]);
    expect(adapter.providerRetryPolicy(PROVIDER_ID)).toMatchObject({ mode: 'normal', maxRetries: 0 });
    await expect(adapter.resolveModel(PROVIDER_ID, 'invented')).rejects.toMatchObject({ code: 'UNKNOWN_MODEL' });
    expect(mock.fetch).not.toHaveBeenCalled();
  });
  it('uses the user-supplied capacities and real SDK request/body/chunks', async () => {
    const { adapter } = await setup();
    mock.fetch.mockResolvedValue(sse(textEvents()));
    expect(await adapter.resolveModel(PROVIDER_ID, 'fixture-model')).toMatchObject({ context: { contextWindow: 12345 }, defaultMaxTokens: 512 });
    const chunks = await collect(adapter.stream(options({ system: 'system', tools: [{ name: 'lookup', description: 'find', parameters: { type: 'object', properties: { q: { type: 'string' } } } }] })));
    expect(finish(chunks)).toMatchObject({ reason: { kind: 'stop' }, replayState: { response: { kind: 'pi-ai', version: 2 } } });
    expect(chunks).toContainEqual({ type: 'text-delta', index: 0, text: 'hello' });
    const [snapshot, request] = mock.fetch.mock.calls[0];
    expect(snapshot.apiKey).toBe('test-key');
    expect(request.url).toBe('http://127.0.0.1:4343/v1/responses');
    const body = await request.json();
    expect(body).toMatchObject({ model: 'fixture-model', stream: true, store: false, max_output_tokens: 512 });
    expect(body.input.map((item: { role?: string }) => item.role)).toEqual(['system', 'user']);
    expect(body.tools[0]).toMatchObject({ type: 'function', name: 'lookup' });
    expect(body.include).toContain('reasoning.encrypted_content');
    expect(body.reasoning).toBeUndefined();
    expect(mock.fetch).toHaveBeenCalledTimes(1);
  });
  it.each([
    ['empty', [terminal()], 'error'],
    ['failed', [terminal('failed', { error: { code: 'server_error', message: 'SENSITIVE test-key' } })], 'error'],
    ['truncated', textEvents().slice(0, -1), 'error'],
    ['incomplete/filter', textEvents('incomplete', { incomplete_details: { reason: 'content_filter' } }), 'error'],
    ['incomplete/tokens', textEvents('incomplete', { incomplete_details: { reason: 'max_output_tokens' } }), 'max-tokens'],
  ])('%s never fabricates a normal completed stop', async (_label, events, expected) => {
    const { adapter } = await setup();
    mock.fetch.mockResolvedValue(sse(events as unknown[]));
    const end = finish(await collect(adapter.stream(options())));
    expect(end).toMatchObject({ reason: { kind: expected } });
    expect(JSON.stringify(end)).not.toContain('SENSITIVE');
    expect(mock.fetch).toHaveBeenCalledTimes(1);
  });
  it('does not retry transient HTTP or transport failures', async () => {
    const { adapter } = await setup();
    for (const response of [new Response('private diagnostic test-key', { status: 503 }), new Response('private diagnostic test-key', { status: 429 })]) {
      mock.fetch.mockReset().mockResolvedValue(response);
      expect(finish(await collect(adapter.stream(options())))).toMatchObject({ reason: { kind: 'error' } });
      expect(mock.fetch).toHaveBeenCalledTimes(1);
    }
    mock.fetch.mockReset().mockRejectedValue(new Error('private diagnostic test-key'));
    const chunks = await collect(adapter.stream(options()));
    expect(finish(chunks)).toMatchObject({ reason: { kind: 'error' } });
    expect(JSON.stringify(chunks)).not.toContain('private diagnostic');
    expect(mock.fetch).toHaveBeenCalledTimes(1);
  });
  it('replays opaque reasoning, ordered tool call/result and complete history without converting it locally', async () => {
    const { adapter } = await setup();
    const reasoning = { type: 'reasoning', id: 'rs_1', summary: [{ type: 'summary_text', text: 'consider' }], encrypted_content: 'OPAQUE_REASONING' };
    const tool = { type: 'function_call', id: 'fc_1', call_id: 'call_1', name: 'lookup', arguments: '{"q":"x"}', status: 'completed' };
    mock.fetch.mockResolvedValueOnce(sse([
      { type: 'response.output_item.added', output_index: 0, item: reasoning },
      { type: 'response.reasoning_summary_text.delta', output_index: 0, delta: 'consider' },
      { type: 'response.output_item.done', output_index: 0, item: reasoning },
      { type: 'response.output_item.added', output_index: 1, item: { ...tool, arguments: '' } },
      { type: 'response.function_call_arguments.delta', output_index: 1, delta: '{"q":"x"}' },
      { type: 'response.output_item.done', output_index: 1, item: tool },
      terminal('completed', { output: [reasoning, tool] }),
    ]));
    const first = await collect(adapter.stream(options()));
    const end = finish(first);
    expect(end).toMatchObject({ reason: { kind: 'tool-calls' } });
    if (!end || end.type !== 'finish') throw new Error('missing finish');
    const blocks = first.flatMap((chunk) => chunk.type === 'block-end' ? [chunk.block] : []);
    const call = blocks.find((block) => block.type === 'tool-call');
    if (!call || call.type !== 'tool-call') throw new Error('missing tool');
    const assistant = createAssistantMessage({ content: blocks, source: { provider: PROVIDER_ID, model: 'fixture-model', replayState: end.replayState } });
    const result = createToolResultMessage({ callId: call.id, content: [{ type: 'text', text: 'tool answer' }], isError: false });
    mock.fetch.mockResolvedValueOnce(sse(textEvents()));
    await collect(adapter.stream(options({ messages: [...options().messages, assistant, result, { role: 'user', content: [{ type: 'text', text: 'followup' }] }] })));
    const body = await mock.fetch.mock.calls[1][1].json();
    expect(body.input[0]).toMatchObject({ role: 'user' });
    expect(body.input[1]).toMatchObject({ type: 'reasoning', encrypted_content: 'OPAQUE_REASONING' });
    expect(body.input[2]).toMatchObject({ type: 'function_call', name: 'lookup', arguments: '{"q":"x"}' });
    expect(body.input[3]).toMatchObject({ type: 'function_call_output', output: 'tool answer' });
    expect(body.input[4]).toMatchObject({ role: 'user' });
  });
  it('keeps the old prepared call credential/baseURL/profile after save and remove', async () => {
    const { owner, adapter } = await setup();
    const old = await adapter.prepareCall(PROVIDER_ID, 'fixture-model');
    await owner.save({ revision: 1, baseURL: 'http://127.0.0.1:5454/v1', apiKey: 'new-key', contextWindow: 23456, maxTokens: 1024 });
    mock.fetch.mockImplementation(async () => sse(textEvents()));
    await collect(old.stream(options()));
    expect(mock.fetch.mock.calls[0][0]).toMatchObject({ apiKey: 'test-key', settings: { baseURL: 'http://127.0.0.1:4343/v1' } });
    await collect(adapter.stream(options()));
    expect(mock.fetch.mock.calls[1][0]).toMatchObject({ apiKey: 'new-key', settings: { baseURL: 'http://127.0.0.1:5454/v1' } });
    owner.remove(2);
    expect(await adapter.listModels(PROVIDER_ID)).toEqual([]);
    await collect(old.stream(options()));
    expect(mock.fetch.mock.calls[2][0].apiKey).toBe('test-key');
  });
  it('cancels the upstream when a Harness consumer stops early', async () => {
    const { adapter } = await setup();
    let upstreamSignal: AbortSignal | undefined;
    mock.fetch.mockImplementation((_snapshot, request: Request) => {
      upstreamSignal = request.signal;
      const body = new ReadableStream<Uint8Array>({ start(controller) {
        controller.enqueue(new TextEncoder().encode(textEvents().slice(0, 3).map((event) => `data: ${JSON.stringify(event)}\n\n`).join('')));
        request.signal.addEventListener('abort', () => controller.error(new Error('cancelled')), { once: true });
      } });
      return Promise.resolve(new Response(body, { headers: { 'content-type': 'text/event-stream' } }));
    });
    const iterator = adapter.stream(options())[Symbol.asyncIterator]();
    expect((await iterator.next()).done).toBe(false);
    await iterator.return?.();
    expect(upstreamSignal?.aborted).toBe(true);
  });
  it('propagates caller abort instead of publishing success', async () => {
    const { adapter } = await setup();
    const controller = new AbortController();
    mock.fetch.mockImplementation((_snapshot, request: Request) => new Promise((_resolve, reject) => {
      request.signal.addEventListener('abort', () => reject(new Error('cancelled')), { once: true });
      controller.abort();
    }));
    try {
      const chunks = await collect(adapter.stream(options({ signal: controller.signal })));
      expect(finish(chunks)).toMatchObject({ reason: { kind: 'aborted' } });
    } catch (error) { expect(error).toMatchObject({ code: 'ABORTED' }); }
    expect(mock.fetch).toHaveBeenCalledTimes(1);
  });
});
