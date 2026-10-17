import type { Context } from '@deepseek-ai/cordis';
import type { ConnectionHandle } from '@deepseek-ai/dsh-client-connection/client';
import type {} from '@deepseek-ai/dsh-client-ui-renderer/client';
import type {} from '@deepseek-ai/dsh-client-ui-settings-plugins/client';
import type {} from '@deepseek-ai/dsh-client-ui-settings/client';
import type {} from '@deepseek-ai/dsh-client-ui-settings-models/client';
import React, { useEffect, useId, useRef, useState, useSyncExternalStore } from 'react';
import { DEFAULT_BASE_URL, ERROR_TEXT, type Settings } from './contracts.js';
import { ChatPassClientStore } from './client-store.js';
import './styles.css';

export const name = 'dsh-chatpass';
export const inject = ['connection', 'slots'];
let sharedStore: ChatPassClientStore | undefined;
export interface ChatPassProps { store: ChatPassClientStore }
const initialSettings: Settings = { baseURL: DEFAULT_BASE_URL, contextWindow: 0, maxTokens: 0 };
function useStore(store: ChatPassClientStore) {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  useEffect(() => { void store.start(); }, [store]);
  return snapshot;
}
function Status({ store }: ChatPassProps) {
  const { state, busy, error } = useStore(store);
  return <div aria-live="polite" aria-atomic="true">
    <p>{busy ? '正在读取或验证连接…' : state?.configured ? '连接已配置 · 网关密钥已配置' : state ? '尚未配置连接' : '尚未读取连接状态'}</p>
    {(error ?? state?.lastError) && <p role="alert">{ERROR_TEXT[error ?? state?.lastError ?? 'internal']}</p>}
    {!state && !busy && <button type="button" onClick={() => { void store.run({ action: 'status' }); }}>重试读取状态</button>}
  </div>;
}
function Catalogue({ store }: ChatPassProps) {
  const { state, busy } = useStore(store);
  const [, updateClock] = useState(0);
  const discoveredAt = state?.discoveredAt;
  const expiresAt = discoveredAt ? Date.parse(discoveredAt) + 300_000 : NaN;
  const stale = state?.stale || !Number.isFinite(expiresAt) || Date.now() >= expiresAt;
  useEffect(() => {
    if (state?.stale || !Number.isFinite(expiresAt)) return;
    const remaining = expiresAt - Date.now();
    if (remaining <= 0) { updateClock((clock) => clock + 1); return; }
    const timer = window.setTimeout(() => updateClock((clock) => clock + 1), remaining);
    return () => window.clearTimeout(timer);
  }, [expiresAt, state?.stale]);
  if (!state) return null;
  return <section aria-label="模型目录">
    <div className="chatpass-actions"><strong>模型目录（{state.models.length}）</strong>
      <button type="button" disabled={busy || !state.configured} onClick={() => { void store.run({ action: 'refresh', revision: state.revision }); }}>刷新模型</button></div>
    <p>{state.discoveredAt ? <>发现时间：<time dateTime={state.discoveredAt}>{state.discoveredAt}</time> · {stale ? '缓存已过期' : '最近发现的缓存'}</> : '尚未发现模型'}</p>
    {state.models.length > 0 ? <ul className="chatpass-models">{state.models.map((model) => <li key={model}>{model}</li>)}</ul> : <p>目录为空。配置连接或手动刷新后查看；不会自动发送推理。</p>}
    <small>目录可见不代表账号资格或额度。请使用 DSH 原生模型选择器；插件不会修改默认模型。</small>
  </section>;
}
interface FormProps extends ChatPassProps { onDirtyChange?: (dirty: boolean) => void; retainDraft?: boolean }
export function ChatPassForm({ store, onDirtyChange, retainDraft = false }: FormProps) {
  const { state, busy, error, generation } = useStore(store);
  const [retained] = useState(() => retainDraft ? store.getRetainedDraft() : null);
  const [draft, setDraft] = useState<Settings>(retained?.settings ?? state?.settings ?? initialSettings);
  const [revision, setRevision] = useState(retained?.revision ?? state?.revision ?? -1);
  const [apiKey, setApiKey] = useState('');
  const [conflict, setConflict] = useState(!!retained && (retained.conflict || retained.generation !== generation));
  const [notice, setNotice] = useState(retained ? '已恢复未保存的非秘密草稿。密钥未保存，请重新输入。' : '');
  const id = useId();
  const base = useRef(retained?.base ?? state?.settings ?? initialSettings);
  const changed = apiKey !== '' || draft.baseURL !== base.current.baseURL || draft.contextWindow !== base.current.contextWindow || draft.maxTokens !== base.current.maxTokens;
  const dirty = changed || conflict;
  const outdated = !!state && revision !== -1 && revision !== state.revision;
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  const previousGeneration = useRef(retained?.generation ?? generation);
  useEffect(() => {
    if (previousGeneration.current === generation) return;
    previousGeneration.current = generation;
    setApiKey('');
    if (dirty) setConflict(true);
  }, [generation, dirty]);
  useEffect(() => {
    if (state && !dirty) {
      base.current = state.settings;
      setDraft(state.settings);
      setRevision(state.revision);
    }
  }, [state, dirty]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  useEffect(() => {
    if (!retainDraft) return;
    store.retainDraft(dirty ? { settings: draft, base: base.current, revision, generation, conflict } : null);
  }, [retainDraft, store, dirty, draft, revision, generation, conflict]);
  const reload = async () => {
    if (changed && !window.confirm('重新载入将放弃未保存的更改，继续吗？')) return;
    setApiKey('');
    if (await store.run({ action: 'status' })) {
      const current = store.getSnapshot().state;
      if (current) {
        base.current = current.settings;
        setDraft(current.settings);
        setRevision(current.revision);
        setConflict(false);
        if (retainDraft) store.retainDraft(null);
        setNotice('已载入当前设置。请检查后再保存。');
      }
    }
  };
  const validCapacity = Number.isSafeInteger(draft.contextWindow) && draft.contextWindow > 0 && Number.isSafeInteger(draft.maxTokens) && draft.maxTokens > 0;
  return <div className="chatpass-ui">
    <h3>ChatPass 连接设置</h3>
    <Status store={store} />
    <p>请先自行启动 ChatPass。仅连接本机网关；账号授权、注销请使用核心启动时生成的私有管理入口或 CLI，此处不收集 OAuth 令牌或管理链接。</p>
    <p><strong>连接配置与密钥仅保存在宿主内存，重启后丢失，需重新设置。</strong></p>
    <form onSubmit={async (event) => {
      event.preventDefault();
      if (!state || busy || conflict || outdated || error === 'conflict' || !validCapacity) return;
      setNotice('');
      const success = await store.run({ action: 'save', revision, ...draft, ...(apiKey === '' ? {} : { apiKey }) });
      setApiKey('');
      if (success) {
        const saved = store.getSnapshot().state;
        if (saved) { base.current = saved.settings; setDraft(saved.settings); setRevision(saved.revision); }
        setConflict(false);
        if (retainDraft) store.retainDraft(null);
        setNotice('连接已验证并保存，密钥输入已清除。');
      } else {
        if (store.getSnapshot().error === 'conflict') setConflict(true);
        setNotice('未保存更改；非秘密草稿已保留，密钥输入已清除。请检查错误后手动重试。');
      }
    }}>
      <fieldset disabled={busy || !state}>
        <legend>连接与容量</legend>
        <label htmlFor={`${id}-url`}>本机 API 地址</label>
        <input id={`${id}-url`} type="url" required value={draft.baseURL} onChange={(event) => setDraft({ ...draft, baseURL: event.target.value })} spellCheck={false} autoComplete="off" />
        <label htmlFor={`${id}-key`}>网关密钥{state?.configured ? '（留空保留原密钥）' : ''}</label>
        <input id={`${id}-key`} type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="new-password" spellCheck={false} aria-describedby={`${id}-secret`} />
        <small id={`${id}-secret`}>仅使用 ChatPass 网关密钥；不是 OpenAI / OAuth 令牌。更换地址须重新输入密钥。保存成功或失败均清除此输入。</small>
        <p id={`${id}-capacity`}>目录未验证模型容量。以下仅为用户确认的容量，不代表上游能力；首版仅支持文本（text only），不声明图片能力。</p>
        <div className="chatpass-capacity">
          <label>上下文窗口（必填）<input type="number" min="1" step="1" required value={draft.contextWindow} aria-describedby={`${id}-capacity`} onChange={(event) => setDraft({ ...draft, contextWindow: Number(event.target.value) })} /></label>
          <label>最大输出 tokens（必填）<input type="number" min="1" step="1" required value={draft.maxTokens} aria-describedby={`${id}-capacity`} onChange={(event) => setDraft({ ...draft, maxTokens: Number(event.target.value) })} /></label>
        </div>
        <div className="chatpass-actions"><button type="submit" disabled={!validCapacity || conflict || outdated || error === 'conflict'}>验证并保存</button>
          <button type="button" onClick={() => { void reload(); }}>重新载入当前设置</button></div>
      </fieldset>
    </form>
    {(conflict || outdated || error === 'conflict') && <p role="alert">设置版本冲突。请显式“重新载入当前设置”，检查后再保存；不会自动覆盖。</p>}
    {notice && <p role="status">{notice}</p>}
    <Catalogue store={store} />
    <details><summary>高级设置与安全诊断</summary>
      <p>模式：连接已有服务 · provider：chatpass · 修订号：{state?.revision ?? '未知'} · 密钥存储：仅内存</p>
      <p>诊断类别：{error ?? state?.lastError ?? '无'}。不显示远端原文、密钥、令牌或账号信息。不扫描端口，不直连管理接口，不自动重试推理。</p>
      <button type="button" disabled={busy || !state?.configured} onClick={async () => {
        if (!state || !window.confirm('移除插件连接？这不会撤销 ChatPass 核心授权，也不会停止外部服务。未保存草稿将保留。')) return;
        setApiKey('');
        if (await store.run({ action: 'remove', revision: state.revision })) setNotice('插件连接已移除；核心授权和服务未更改。');
      }}>移除连接</button>
    </details>
  </div>;
}
export function ChatPassModelsCard({ store }: ChatPassProps) {
  const { state } = useStore(store);
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const [opened, setOpened] = useState(false);
  const [dirty, setDirty] = useState(false);
  const titleId = useId();
  const close = () => {
    if (dirty && !window.confirm('有未保存的更改，放弃并关闭吗？')) return;
    dialog.current?.close();
    setOpened(false);
    setDirty(false);
    opener.current?.focus();
  };
  useEffect(() => {
    if (!opened) return;
    dialog.current?.showModal();
    dialog.current?.querySelector<HTMLInputElement>('input')?.focus();
  }, [opened]);
  return <section className="chatpass-ui chatpass-card" aria-label="ChatPass 模型设置">
    <h3>ChatPass</h3><Status store={store} />
    {state && <p className="chatpass-address">{state.settings.baseURL}</p>}
    <Catalogue store={store} />
    <p>授权请使用核心启动时的私有管理入口或 CLI。密钥仅在内存中保存，重启需重新输入。</p>
    <button type="button" ref={opener} onClick={() => setOpened(true)}>{state?.configured ? '更多设置' : '配置连接'}</button>
    <dialog ref={dialog} className="chatpass-dialog chatpass-ui" aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); close(); }}>
      <div className="chatpass-actions"><h2 id={titleId}>ChatPass 设置</h2><button type="button" onClick={close}>关闭</button></div>
      {opened && <ChatPassForm store={store} onDirtyChange={setDirty} />}
    </dialog>
  </section>;
}
function ChatPassPluginTab({ store }: ChatPassProps) {
  return <ChatPassForm store={store} retainDraft />;
}
// The SDK declares Host's connection globally; use its actual browser face here.
export type ChatPassClientContext = Omit<Context, 'connection'> & { connection: ConnectionHandle };
export function apply(ctx: ChatPassClientContext): void {
  const store = sharedStore ??= new ChatPassClientStore((command) => ctx.connection.rpc.call('/chatpass', 'command', command));
  ctx.on('connection/reset', () => { void store.reset(); });
  ctx.slots.register({ name: 'settings.plugins.tab', id: 'chatpass', order: 50, label: () => 'ChatPass', inject: () => ({ store }) }, ChatPassPluginTab);
  ctx.slots.inject('settings.models.footer', () => ctx.slots.register({ name: 'settings.models.footer', id: 'dsh-chatpass', order: 110, inject: () => ({ store }) }, ChatPassModelsCard));
  ctx.effect(() => () => { if (sharedStore === store) sharedStore = undefined; }, 'chatpass shared client state');
}
