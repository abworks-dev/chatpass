// Browser-safe values. No secret is returned from any host operation.
export const DEFAULT_BASE_URL = 'http://127.0.0.1:19840/v1';
export interface Capacity { contextWindow: number; maxTokens: number }
export interface Settings extends Capacity { baseURL: string }
export type ErrorCode = 'invalid' | 'conflict' | 'unconfigured' | 'offline' | 'timeout' | 'cancelled' | 'unauthorized' | 'forbidden' | 'rate_limited' | 'redirect' | 'protocol' | 'internal';
export interface PublicState {
  revision: number;
  configured: boolean;
  settings: Settings;
  models: readonly string[];
  discoveredAt: string | null;
  stale: boolean;
  lastError: ErrorCode | null;
  secretStorage: 'memory';
}
export interface SaveInput extends Settings { revision: number; apiKey?: string }
export type Command =
  | { action: 'status' }
  | ({ action: 'save' } & SaveInput)
  | { action: 'refresh' | 'remove'; revision: number };
export const ERROR_TEXT: Record<ErrorCode, string> = {
  invalid: '请检查地址、密钥和容量设置。只接受 http://127.0.0.1:端口/v1。',
  conflict: '连接已被其他页面更改，请重新载入当前设置后再保存。',
  unconfigured: '请先配置连接。重启宿主后需要重新输入网关密钥。',
  offline: '无法连接本机网关，请确认 ChatPass 已启动。',
  timeout: '连接超时，请检查网关状态后手动重试。',
  cancelled: '操作已取消，配置未保存。',
  unauthorized: '网关拒绝密钥；请确认使用的是 ChatPass 网关密钥，而非授权令牌。',
  forbidden: '网关或上游拒绝访问，请在 ChatPass 管理台检查授权与资格。',
  rate_limited: '网关或上游限流，请稍后手动重试；不会自动重放推理。',
  redirect: '网关返回重定向，已拒绝以防密钥泄漏。',
  protocol: '网关响应格式不兼容或流未成功结束。请检查 ChatPass 版本。',
  internal: '插件操作失败，请检查版本兼容性；诊断不包含远端原文。',
};
