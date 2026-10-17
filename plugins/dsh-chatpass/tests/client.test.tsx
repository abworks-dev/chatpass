// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { Simulate } from 'react-dom/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatPassForm, ChatPassModelsCard } from '../src/client.js';
import { ChatPassClientStore, type CommandTransport } from '../src/client-store.js';
import { DEFAULT_BASE_URL, ERROR_TEXT, type PublicState } from '../src/contracts.js';

const state = (revision = 1): PublicState => ({ revision, configured: true, settings: { baseURL: DEFAULT_BASE_URL, contextWindow: 32000, maxTokens: 4000 }, models: ['example-model'], discoveredAt: '2026-10-14T00:00:00Z', stale: false, lastError: null, secretStorage: 'memory' });
let container: HTMLDivElement; let root: Root;
function input(scope: ParentNode, selector: string): HTMLInputElement {
  const element = scope.querySelector(selector);
  if (!(element instanceof HTMLInputElement)) throw new Error(`Missing input: ${selector}`);
  return element;
}
async function change(element: HTMLInputElement, value: string) {
  await act(async () => { element.value = value; Simulate.change(element); });
}
async function submit(scope: ParentNode = container) {
  const element = scope instanceof HTMLFormElement ? scope : scope.querySelector('form');
  if (!element) throw new Error('Missing form');
  await act(async () => { Simulate.submit(element); });
}
function button(scope: ParentNode, text: string): HTMLButtonElement {
  const element = Array.from(scope.querySelectorAll('button')).find((candidate) => candidate.textContent === text);
  if (!element) throw new Error(`Missing button: ${text}`); return element;
}
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
async function render(transport: CommandTransport, two = false) {
  const store = new ChatPassClientStore(transport);
  await act(async () => { root.render(<><ChatPassForm store={store} />{two && <ChatPassForm store={store} />}</>); });
  return store;
}
describe('ChatPass settings rendered interactions', () => {
  it('retains only the plugin tab nonsecret draft across unmounts and requires reload after a sibling revision change', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValueOnce({ ok: true, value: state() })
      .mockResolvedValue({ ok: true, value: state(2) });
    const store = new ChatPassClientStore(transport);
    await act(async () => root.render(<ChatPassForm store={store} retainDraft />));
    await change(input(container, 'input[type=url]'), 'http://127.0.0.1:19842/v1');
    await change(input(container, '.chatpass-capacity label:last-child input'), '6000');
    await change(input(container, 'input[type=password]'), 'never-retain-this-key');
    await act(async () => root.render(null));
    expect(JSON.stringify(store.getRetainedDraft())).not.toContain('never-retain-this-key');
    await act(async () => { await store.run({ action: 'refresh', revision: 1 }); });
    await act(async () => root.render(<ChatPassForm store={store} retainDraft />));
    expect(input(container, 'input[type=url]').value).toContain('19842');
    expect(input(container, '.chatpass-capacity label:last-child input').value).toBe('6000');
    expect(input(container, 'input[type=password]').value).toBe('');
    expect(container.textContent).toContain('密钥未保存，请重新输入');
    expect(button(container, '验证并保存').disabled).toBe(true);
    const count = transport.mock.calls.length; await submit(); expect(transport.mock.calls).toHaveLength(count);
    await act(async () => button(container, '重新载入当前设置').click());
    expect(input(container, 'input[type=url]').value).toBe(DEFAULT_BASE_URL);
    expect(store.getRetainedDraft()).toBeNull();
    expect(button(container, '验证并保存').disabled).toBe(false);
  });
  it('retains the plugin draft across a reset while unmounted but blocks saving against a reused revision', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValue({ ok: true, value: state() });
    const store = new ChatPassClientStore(transport);
    await act(async () => root.render(<ChatPassForm store={store} retainDraft />));
    await change(input(container, '.chatpass-capacity label:last-child input'), '6000');
    await act(async () => root.render(null));
    await act(async () => { await store.reset(); });
    await act(async () => root.render(<ChatPassForm store={store} retainDraft />));
    expect(input(container, '.chatpass-capacity label:last-child input').value).toBe('6000');
    expect(button(container, '验证并保存').disabled).toBe(true);
    expect(container.textContent).toContain('设置版本冲突');
  });
  it('clears a retained plugin draft after successful save', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValueOnce({ ok: true, value: state() })
      .mockResolvedValue({ ok: true, value: state(2) });
    const store = new ChatPassClientStore(transport);
    await act(async () => root.render(<ChatPassForm store={store} retainDraft />));
    await change(input(container, '.chatpass-capacity label:last-child input'), '6000');
    expect(store.getRetainedDraft()).not.toBeNull();
    await submit(); expect(store.getRetainedDraft()).toBeNull();
    await act(async () => root.render(null));
    await act(async () => root.render(<ChatPassForm store={store} retainDraft />));
    expect(input(container, '.chatpass-capacity label:last-child input').value).toBe('4000');
    expect(container.textContent).not.toContain('已恢复未保存');
  });
  it('marks retained models stale immediately after a failed refresh with a valid host snapshot', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-14T00:01:00Z'));
    const cached = state();
    const transport = vi.fn<CommandTransport>().mockResolvedValueOnce({ ok: true, value: cached })
      .mockResolvedValueOnce({ ok: false, error: { code: 'offline', message: 'unsafe remote text', details: { state: { ...cached, stale: true, lastError: 'offline' } } } });
    await render(transport);
    expect(container.textContent).toContain('最近发现的缓存');
    await act(async () => button(container, '刷新模型').click());
    expect(container.textContent).toContain('缓存已过期');
    expect(container.textContent).not.toContain('最近发现的缓存');
    expect(container.textContent).toContain('example-model');
    expect(container.textContent).toContain(ERROR_TEXT.offline);
    expect(container.textContent).not.toContain('unsafe remote text');
    expect(transport.mock.calls.map(([command]) => command.action)).toEqual(['status', 'refresh']);
  });
  it('marks an idle catalogue stale at five minutes without sending any background RPC', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-14T00:00:00Z'));
    const transport = vi.fn<CommandTransport>().mockResolvedValue({ ok: true, value: state() });
    await render(transport);
    expect(container.textContent).toContain('最近发现的缓存');
    await act(async () => { vi.advanceTimersByTime(299_999); });
    expect(container.textContent).toContain('最近发现的缓存');
    await act(async () => { vi.advanceTimersByTime(1); });
    expect(container.textContent).toContain('缓存已过期');
    expect(container.textContent).not.toContain('最近发现的缓存');
    await act(async () => { vi.advanceTimersByTime(300_000); });
    expect(transport.mock.calls).toEqual([[{ action: 'status' }]]);
  });
  it('shares state between two entries, omits blank keys, clears successful secret inputs', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValueOnce({ ok: true, value: state() })
      .mockResolvedValueOnce({ ok: true, value: { ...state(2), settings: { ...state().settings, maxTokens: 5000 } } })
      .mockResolvedValueOnce({ ok: true, value: state(3) });
    await render(transport, true);
    const forms = container.querySelectorAll('form');
    await change(input(forms[0], '.chatpass-capacity label:last-child input'), '5000');
    await submit(forms[0]);
    const command = transport.mock.calls[1][0];
    expect(command.action).toBe('save'); expect(command).not.toHaveProperty('apiKey');
    expect(input(forms[1], '.chatpass-capacity label:last-child input').value).toBe('5000');
    await change(input(forms[0], 'input[type=password]'), 'fake-key');
    await submit(forms[0]); expect(input(forms[0], 'input[type=password]').value).toBe('');
    expect(transport.mock.calls[2][0]).toMatchObject({ action: 'save', revision: 2, apiKey: 'fake-key' });
    expect(transport.mock.calls.filter(([command]) => command.action === 'status')).toHaveLength(1);
  });
  it('preserves nonsecret drafts and clears secrets after errors, with explicit retry', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValueOnce({ ok: true, value: state() })
      .mockResolvedValueOnce({ ok: false, error: { code: 'unauthorized', message: 'unsafe-secret', details: {} } })
      .mockResolvedValueOnce({ ok: true, value: state(2) });
    await render(transport);
    await change(input(container, 'input[type=url]'), 'http://127.0.0.1:19841/v1');
    await change(input(container, 'input[type=password]'), 'fake-key'); await submit();
    expect(input(container, 'input[type=password]').value).toBe('');
    expect(input(container, 'input[type=url]').value).toContain('19841');
    expect(container.textContent).toContain('网关拒绝密钥'); expect(container.textContent).not.toContain('unsafe-secret');
    await change(input(container, 'input[type=password]'), 'replacement'); await submit();
    expect(container.textContent).toContain('连接已验证并保存');
  });
  it('blocks silent overwrite when a sibling saves; explicit reload discards only on confirmation', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValueOnce({ ok: true, value: state() })
      .mockResolvedValue({ ok: true, value: state(2) });
    await render(transport, true); const forms = container.querySelectorAll('form');
    await change(input(forms[1], 'input[type=url]'), 'http://127.0.0.1:19842/v1');
    await change(input(forms[0], 'input[type=password]'), 'fake-key'); await submit(forms[0]);
    expect(button(forms[1], '验证并保存').disabled).toBe(true);
    expect(input(forms[1], 'input[type=url]').value).toContain('19842');
    const count = transport.mock.calls.length; await submit(forms[1]); expect(transport.mock.calls).toHaveLength(count);
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    await act(async () => button(forms[1], '重新载入当前设置').click()); expect(transport.mock.calls).toHaveLength(count);
    await act(async () => button(forms[1], '重新载入当前设置').click());
    expect(input(forms[1], 'input[type=url]').value).toBe(DEFAULT_BASE_URL);
    expect(button(forms[1], '验证并保存').disabled).toBe(false);
  });
  it('requires explicit reload after host conflict and does not replay save', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValueOnce({ ok: true, value: state() })
      .mockResolvedValueOnce({ ok: false, error: { code: 'conflict', message: '', details: {} } })
      .mockResolvedValueOnce({ ok: true, value: state(4) });
    await render(transport); await change(input(container, 'input[type=password]'), 'fake-key'); await submit();
    expect(button(container, '验证并保存').disabled).toBe(true);
    await act(async () => button(container, '重新载入当前设置').click());
    expect(button(container, '验证并保存').disabled).toBe(false);
    expect(transport.mock.calls.map(([command]) => command.action)).toEqual(['status', 'save', 'status']);
  });
  it('clears secrets on reset and requires explicit reload even if the host reuses a revision', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValue({ ok: true, value: state() });
    const store = await render(transport);
    await change(input(container, 'input[type=url]'), 'http://127.0.0.1:19842/v1');
    await change(input(container, 'input[type=password]'), 'fake-key');
    await act(async () => { await store.reset(); });
    expect(input(container, 'input[type=password]').value).toBe('');
    expect(input(container, 'input[type=url]').value).toContain('19842');
    expect(button(container, '验证并保存').disabled).toBe(true);
    await act(async () => button(container, '重新载入当前设置').click());
    expect(input(container, 'input[type=url]').value).toBe(DEFAULT_BASE_URL);
    expect(button(container, '验证并保存').disabled).toBe(false);
  });
  it('refreshes and removes using the current revision without clearing the host authorization', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValueOnce({ ok: true, value: state() })
      .mockResolvedValueOnce({ ok: true, value: state(2) })
      .mockResolvedValueOnce({ ok: true, value: { ...state(3), configured: false, models: [] } });
    await render(transport);
    await act(async () => button(container, '刷新模型').click());
    await act(async () => button(container, '移除连接').click());
    expect(transport.mock.calls.map(([command]) => command)).toEqual([{ action: 'status' }, { action: 'refresh', revision: 1 }, { action: 'remove', revision: 2 }]);
    expect(container.textContent).toContain('核心授权和服务未更改');
  });
  it('starts unknown capacities at zero and rejects nonpositive/fractional capacities', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValue({ ok: true, value: { ...state(), configured: false, settings: { baseURL: DEFAULT_BASE_URL, contextWindow: 0, maxTokens: 0 } } });
    await render(transport); expect(input(container, 'input[type=number]').value).toBe('0');
    expect(button(container, '验证并保存').disabled).toBe(true);
    await change(input(container, 'input[type=number]'), '1.5'); expect(button(container, '验证并保存').disabled).toBe(true);
  });
  it('confirms unsaved dialog cancellation, restores focus and does not reload status on reopen', async () => {
    const transport = vi.fn<CommandTransport>().mockResolvedValue({ ok: true, value: state() });
    const store = new ChatPassClientStore(transport);
    await act(async () => root.render(<ChatPassModelsCard store={store} />));
    const open = button(container, '更多设置'); await act(async () => open.click());
    const dialog = container.querySelector('dialog'); if (!dialog) throw new Error('Missing dialog');
    expect(dialog.open).toBe(true); expect(document.activeElement).toBe(input(dialog, 'input[type=url]'));
    await change(input(dialog, 'input[type=password]'), 'fake-key');
    expect(store.getRetainedDraft()).toBeNull();
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    await act(async () => dialog.dispatchEvent(new Event('cancel', { cancelable: true, bubbles: true })));
    expect(dialog.open).toBe(true);
    await act(async () => button(dialog, '关闭').click()); expect(dialog.open).toBe(false); expect(document.activeElement).toBe(open);
    await act(async () => open.click()); expect(input(dialog, 'input[type=password]').value).toBe('');
    expect(transport).toHaveBeenCalledTimes(1);
  });
});
