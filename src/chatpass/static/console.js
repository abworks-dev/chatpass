/* Plain same-origin JavaScript. No browser persistence and no inference probes. */
(() => {
  'use strict';

  // Remove the bootstrap capability before starting any management request.
  function takeBootstrapToken() {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    if (!fragment.has('token')) return null;
    let token = fragment.get('token');
    window.history.replaceState(null, '', window.location.pathname + window.location.search + '#overview');
    if (typeof token !== 'string' || token.length > 512) token = '';
    return token;
  }
  let initialToken;
  let fragmentRemovalFailed = false;
  try { initialToken = takeBootstrapToken(); } catch { fragmentRemovalFailed = true; }

  const byId = (id) => document.getElementById(id);
  const views = {
    overview: ['本地网关，一目了然。', '从账号授权到客户端接入，在这里完成每一步。', '概览'],
    authorization: ['授权由你确认，凭据留在本地。', '使用官方浏览器流程，连接你自己的账号。', '账号授权'],
    key: ['给客户端一把独立的钥匙。', '显式创建、按需轮换，不暴露官方账号凭据。', '网关密钥'],
    models: ['真实模型，不靠猜测。', '按需读取上游目录，选择实际返回的模型 ID。', '模型目录'],
    clients: ['准备好，接入你的工作流。', '使用本地地址和网关密钥，先确认 Responses 支持。', '客户端接入']
  };
  const state = {
    csrf: null, authenticated: false, generation: 0, status: null,
    view: 'overview', statusBusy: false, loginBusy: false, keyBusy: false,
    modelsBusy: false, selectedModel: null, baseUrl: null
  };
  let keyTimer = null;
  let keyVisibilityVersion = 0;
  let pollTimer = null;
  let pollStarted = 0;
  let noticeTimer = null;
  const safeMessages = {
    admin_session_required: '管理会话已失效，请用终端生成的新链接重新打开。',
    invalid_bootstrap: '启动链接无效、已使用或已过期，请重新运行 chatpass web，使用新链接打开。',
    invalid_csrf: '管理请求验证失败。请重新检查现有会话，确认状态后再操作。',
    invalid_host: '请求地址不符合本地安全要求。请使用本机终端生成的原始回环地址。',
    invalid_origin: '请求来源不受信任。请使用本机终端生成的原始地址，不要通过代理或嵌入页面访问。',
    invalid_client: '仅允许本机回环访问。请在运行 ChatPass 的本机打开控制台，不要使用隧道或端口转发。',
    invalid_action: '密钥操作无效。请刷新状态并检查运行中的 ChatPass 版本，不会自动重复提交。',
    invalid_json: '管理请求格式不符合预期。请检查运行中的 ChatPass 版本，不会自动重复提交。',
    unsupported_media_type: '管理请求格式不受支持。请检查运行中的 ChatPass 版本。',
    request_too_large: '管理请求超出大小限制。请检查运行中的 ChatPass 版本。',
    console_busy: '控制台操作仍在处理中。请等待完成后手动刷新状态，不要重复提交。',
    console_unavailable: '控制台正在关闭或暂不可用。请确认终端仍在运行，重新启动后使用新管理链接。',
    console_error: '控制台未能完成请求。请检查终端和系统安全存储，先刷新状态确认结果。',
    gateway_unavailable: '网关尚不可用。请确认 ChatPass 已正常启动，恢复后再手动查询。',
    login_required: '需要重新授权账号，请前往账号授权页面。',
    credentials_unavailable: '账号凭据暂不可用。请先刷新状态并检查系统安全存储；仅当状态提示需要授权时重新授权。',
    storage_unavailable: '系统安全存储暂不可用。请修复或等待系统安全存储恢复，再手动刷新状态；不会降级为明文保存。',
    gateway_key_exists: '网关密钥已存在，未创建或隐式轮换。若需要更换，请显式选择轮换并确认旧密钥失效。',
    login_already_running: '已有授权任务正在运行，请完成本机浏览器中的授权，不要重复发起。',
    login_timeout: '授权已超时。你可以手动重新发起授权。',
    authorization_failed: '授权流程未完成。请检查本机浏览器、网络和系统安全存储，确认状态后手动重试。',
    consent_denied: '你没有同意本次授权，控制台不会自动重试。若决定授权，可手动重新发起。',
    registration_incomplete: '客户端注册未完成。请检查本机网络和官方浏览器流程，确认状态后手动重新授权。',
    identity_verification_unavailable: '身份签名验证服务暂不可用，已收到的凭据保留在本地加密存储中。请等待网络或验证服务恢复，再手动查询模型以重新验证；不要重复发起授权。',
    upstream_authentication_failed: '上游授权失败，请重新授权账号。',
    upstream_access_denied: '上游拒绝访问，请确认账号资格和授权。',
    upstream_rate_limit: '上游额度或速率受限，请稍后手动重试。订阅不等于无限额度。',
    upstream_timeout: '上游响应超时，请稍后手动重试。',
    upstream_error: '上游服务暂不可用，请稍后手动重试。',
    upstream_invalid_request: '上游拒绝了请求；本控制台不会自动重试。',
    invalid_upstream_response: '上游返回的模型目录不符合预期。',
    invalid_response: '控制台响应格式不符合预期，请检查运行中的 ChatPass 版本。',
    network_error: '无法连接本地网关，请确认 ChatPass 终端仍在运行。',
    request_timeout: '本次请求等待超时。请先刷新状态确认结果，不要重复提交。'
  };
  const sessionCodes = new Set(['admin_session_required']);
  class ApiError extends Error {
    constructor(code, status = 0) { super(code); this.code = code; this.status = status; }
  }
  function safeCode(code) {
    return typeof code === 'string' && /^[a-z][a-z0-9_]{0,47}$/.test(code) && !code.startsWith('cp_') ? code : 'unknown_error';
  }
  function messageFor(error) {
    const code = safeCode(error && error.code);
    if (code === 'credentials_unavailable' && state.status && state.status.pending_token_validation) {
      return safeMessages.identity_verification_unavailable;
    }
    return safeMessages[code] || ('操作未完成（错误代码：' + code + '）。请检查状态后手动重试。');
  }
  function announce(message, error = false) {
    window.clearTimeout(noticeTimer);
    byId('notice').hidden = true;
    byId('error-notice').hidden = true;
    const target = byId(error ? 'error-notice' : 'notice');
    target.textContent = message;
    target.hidden = false;
    if (!error) noticeTimer = window.setTimeout(() => { target.hidden = true; }, 7000);
  }
  function setPill(id, label, tone) {
    const element = byId(id);
    element.textContent = label;
    element.className = 'status-pill ' + tone;
  }
  function setMetric(id, label, tone) {
    const element = byId(id);
    element.textContent = label;
    element.classList.remove('tone-success', 'tone-warning', 'tone-danger');
    if (tone) element.classList.add('tone-' + tone);
  }
  function clearKey(notify = false) {
    keyVisibilityVersion += 1;
    window.clearTimeout(keyTimer);
    keyTimer = null;
    const visible = !byId('key-reveal').hidden;
    byId('revealed-key').textContent = '';
    byId('key-reveal').hidden = true;
    if (notify && visible) announce('密钥显示已清除。已保存到客户端或剪贴板的副本不受影响。');
  }
  function stopPolling() { window.clearTimeout(pollTimer); pollTimer = null; pollStarted = 0; }
  function updateControls() {
    const ready = state.authenticated && state.status !== null && !state.statusBusy;
    byId('refresh-status').disabled = !state.authenticated || state.statusBusy || state.keyBusy || state.loginBusy;
    byId('refresh-status').querySelector('span').textContent = state.statusBusy ? '正在读取' : '刷新状态';
    const running = state.status && state.status.login.state === 'running';
    byId('start-login').disabled = !ready || state.loginBusy || running;
    byId('start-login').textContent = state.loginBusy || running ? '授权正在进行…' : '打开浏览器授权';
    byId('init-key').disabled = !ready || state.keyBusy || state.status.gateway_key_configured;
    byId('rotate-key').disabled = !ready || state.keyBusy || !state.status.gateway_key_configured;
    byId('confirm-rotate').disabled = !ready || state.keyBusy;
    byId('init-key').textContent = state.keyBusy ? '正在处理…' : '创建网关密钥';
    byId('rotate-key').textContent = state.keyBusy ? '正在处理…' : '轮换密钥';
    byId('load-models').disabled = !ready || state.modelsBusy;
    byId('load-models').textContent = state.modelsBusy ? '正在查询…' : '查询模型';
    document.querySelectorAll('[data-copy-base]').forEach((button) => { button.disabled = !state.baseUrl; });
  }
  function lock(message) {
    state.generation += 1;
    state.csrf = null;
    state.authenticated = false;
    state.status = null;
    state.baseUrl = null;
    state.selectedModel = null;
    state.statusBusy = false;
    state.loginBusy = false;
    state.keyBusy = false;
    state.modelsBusy = false;
    stopPolling();
    clearKey();
    byId('model-list').replaceChildren();
    byId('selected-model-id').textContent = '';
    byId('client-model-id').textContent = '请先查询并选择实际模型 ID';
    byId('console-content').hidden = true;
    byId('locked-screen').hidden = false;
    byId('locked-title').textContent = '需要新的管理会话';
    byId('locked-description').textContent = message;
    byId('locked-instructions').hidden = false;
    setPill('session-pill', '管理会话未连接', 'warning');
    if (byId('rotate-dialog').open) byId('rotate-dialog').close();
    updateControls();
  }
  async function request(path, options = {}) {
    const generation = state.generation;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 45000);
    const headers = { Accept: 'application/json' };
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    if (options.method === 'POST' && path !== '/admin/api/session') {
      if (!state.csrf) { window.clearTimeout(timeout); throw new ApiError('admin_session_required', 401); }
      headers['X-ChatPass-CSRF'] = state.csrf;
    }
    try {
      const response = await fetch(path, {
        method: options.method || 'GET', headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal
      });
      let data;
      try { data = await response.json(); } catch { throw new ApiError('invalid_response', response.status); }
      if (!response.ok) {
        const code = safeCode(data && (data.error && data.error.code || data.code));
        if (generation === state.generation && path !== '/admin/api/session' && sessionCodes.has(code)) {
          lock('管理会话已过期。请使用终端生成的新链接重新连接。');
        }
        throw new ApiError(code, response.status);
      }
      return data;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(error && error.name === 'AbortError' ? 'request_timeout' : 'network_error');
    } finally { window.clearTimeout(timeout); }
  }
  function validStatus(data) {
    const flags = ['gateway_key_configured', 'account_authorized', 'access_token_expired', 'pending_token_validation', 'login_required', 'registration_configured'];
    const counts = ['uptime_seconds', 'requests_total', 'requests_failed', 'requests_active'];
    if (!data || flags.some((key) => typeof data[key] !== 'boolean') ||
        counts.some((key) => !Number.isFinite(data[key]) || data[key] < 0) ||
        !data.login || !['idle', 'running', 'succeeded', 'failed'].includes(data.login.state) ||
        typeof data.base_url !== 'string') return false;
    try {
      const url = new URL(data.base_url);
      return url.origin === window.location.origin && url.pathname.replace(/\/$/, '') === '/v1' && !url.search && !url.hash && !url.username && !url.password;
    } catch { return false; }
  }
  function accountPresentation(data) {
    if (data.pending_token_validation) return ['待验证', '凭据已加密保留；恢复后手动查询模型验证', 'warning'];
    if (data.login_required) return ['需要授权', '请在官方浏览器中完成授权', 'warning'];
    if (!data.account_authorized) return ['尚未授权', '点击授权后由你确认', 'warning'];
    if (data.access_token_expired) return ['凭据已过期', '实际请求时尝试刷新，不保证可用', 'warning'];
    return ['已保存授权', '未验证账号资格、额度或模型可用性', 'success'];
  }
  function uptime(seconds) {
    const minutes = Math.floor(seconds / 60);
    if (minutes < 1) return Math.floor(seconds) + ' 秒';
    if (minutes < 60) return minutes + ' 分钟';
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + ' 小时 ' + minutes % 60 + ' 分';
    return Math.floor(hours / 24) + ' 天 ' + hours % 24 + ' 小时';
  }
  function renderStatus(data) {
    state.status = data;
    state.baseUrl = data.base_url;
    const [account, accountNote, accountTone] = accountPresentation(data);
    setMetric('metric-account', account, accountTone);
    byId('metric-account-note').textContent = accountNote;
    setPill('account-pill', account, accountTone);
    setPill('flow-account', account, accountTone);
    const keyLabel = data.gateway_key_configured ? '已配置' : '尚未创建';
    const keyTone = data.gateway_key_configured ? 'success' : 'warning';
    setMetric('metric-key', keyLabel, keyTone);
    byId('metric-key-note').textContent = data.gateway_key_configured ? '可在需要时显式轮换' : '显式创建后仅显示一次';
    setPill('key-pill', keyLabel, keyTone);
    setPill('flow-key', keyLabel, keyTone);
    byId('metric-uptime').textContent = uptime(data.uptime_seconds);
    byId('metric-requests').textContent = Math.floor(data.requests_total).toLocaleString('zh-CN');
    byId('metric-request-note').textContent = '失败 ' + Math.floor(data.requests_failed).toLocaleString('zh-CN') + ' · 进行中 ' + Math.floor(data.requests_active).toLocaleString('zh-CN');
    byId('overview-base-url').textContent = data.base_url;
    byId('client-base-url').textContent = data.base_url;
    byId('credential-state').textContent = data.pending_token_validation ? '待验证，不保证可用' : data.access_token_expired ? '访问凭据已过期' : account;
    byId('registration-state').textContent = data.registration_configured ? '已配置' : '尚未配置';
    const loginLabels = { idle: '未运行', running: '等待官方浏览器授权', succeeded: '授权流程已完成', failed: '授权流程失败' };
    byId('login-state').textContent = loginLabels[data.login.state];
    const result = byId('login-result');
    result.hidden = data.login.state === 'idle';
    if (data.login.state === 'running') result.textContent = '请在本机打开的官方浏览器页面中确认。这里只轮询授权任务状态，不会调用模型。';
    if (data.login.state === 'succeeded') result.textContent = '授权流程已完成。账号资格与模型可用性仍需实际请求确认。';
    if (data.login.state === 'failed') result.textContent = messageFor({ code: data.login.code || 'authorization_failed' });
    byId('status-updated').textContent = '状态读取于 ' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
    if (data.login.state === 'running') schedulePoll(); else stopPolling();
    updateControls();
  }
  function statusUnavailable() {
    state.status = null;
    state.baseUrl = null;
    stopPolling();
    setMetric('metric-account', '状态不可用', 'danger');
    byId('metric-account-note').textContent = '读取失败，不能判断账号状态';
    setMetric('metric-key', '状态不可用', 'danger');
    byId('metric-key-note').textContent = '读取失败，不能判断密钥状态';
    setPill('account-pill', '状态不可用', 'danger');
    setPill('flow-account', '状态不可用', 'danger');
    setPill('key-pill', '状态不可用', 'danger');
    setPill('flow-key', '状态不可用', 'danger');
    byId('metric-uptime').textContent = '—';
    byId('metric-requests').textContent = '—';
    byId('metric-request-note').textContent = '失败 — · 进行中 —';
    byId('login-state').textContent = '状态不可用';
    byId('credential-state').textContent = '状态不可用';
    byId('registration-state').textContent = '状态不可用';
    byId('login-result').hidden = true;
    byId('overview-base-url').textContent = '状态读取失败';
    byId('client-base-url').textContent = '状态读取失败';
    byId('status-updated').textContent = '状态读取失败 · 请手动刷新';
    updateControls();
  }
  async function refreshStatus(explicit = false) {
    if (!state.authenticated || state.statusBusy) return;
    const generation = state.generation;
    state.statusBusy = true;
    updateControls();
    byId('console-content').setAttribute('aria-busy', 'true');
    try {
      const data = await request('/admin/api/status');
      if (generation !== state.generation) return;
      if (!validStatus(data)) throw new ApiError('invalid_response');
      renderStatus(data);
      if (explicit) announce('已读取当前网关状态。未发起推理或模型查询。');
    } catch (error) {
      if (generation !== state.generation) return;
      statusUnavailable();
      announce(messageFor(error), true);
    } finally {
      if (generation === state.generation) {
        state.statusBusy = false;
        byId('console-content').setAttribute('aria-busy', 'false');
        updateControls();
      }
    }
  }
  function schedulePoll() {
    if (!state.authenticated || pollTimer !== null) return;
    if (!pollStarted) pollStarted = Date.now();
    if (Date.now() - pollStarted > 210000) {
      stopPolling();
      announce('授权状态等待较久，已停止自动轮询。请手动刷新确认结果，不会自动重发授权。', true);
      return;
    }
    pollTimer = window.setTimeout(async () => {
      pollTimer = null;
      if (!state.authenticated || !state.status || state.status.login.state !== 'running') return;
      if (state.statusBusy || state.keyBusy || state.loginBusy) { schedulePoll(); return; }
      // renderStatus owns rescheduling; a completed budget must not restart here.
      await refreshStatus();
    }, 2000);
  }
  function closeMenu() {
    byId('sidebar-navigation').classList.remove('open');
    byId('menu-toggle').setAttribute('aria-expanded', 'false');
    byId('menu-toggle').setAttribute('aria-label', '展开导航');
  }
  function navigate(view, focus = false) {
    if (!Object.hasOwn(views, view)) return;
    if (state.view === 'key' && view !== 'key') clearKey();
    if (byId('rotate-dialog').open) byId('rotate-dialog').close();
    state.view = view;
    const [title, description, name] = views[view];
    Object.keys(views).forEach((item) => { byId('view-' + item).hidden = item !== view; });
    document.querySelectorAll('[data-view]').forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
    });
    byId('page-title').textContent = title;
    byId('page-description').textContent = description;
    byId('breadcrumb-current').textContent = name;
    document.title = name + ' · ChatPass 本地控制台';
    closeMenu();
    if (focus) byId('main-content').focus({ preventScroll: true });
  }
  function go(view) {
    if (!Object.hasOwn(views, view)) return;
    window.location.hash = view;
    navigate(view, true);
  }
  async function establishSession(token = null) {
    lock('正在检查本地管理会话。');
    byId('locked-title').textContent = '正在验证管理会话';
    byId('locked-description').textContent = '仅已授权的本地管理会话可以查看网关状态。';
    byId('locked-instructions').hidden = true;
    setPill('session-pill', '正在验证管理会话', 'neutral');
    byId('session-retry').disabled = true;
    const generation = state.generation;
    try {
      // The token exists only in this request, never in app state or browser storage.
      const pending = token === null ? request('/admin/api/session') : request('/admin/api/session', { method: 'POST', body: { token } });
      token = null;
      const data = await pending;
      if (generation !== state.generation) return;
      if (!data || typeof data.csrf_token !== 'string' || !data.csrf_token || data.csrf_token.length > 512) throw new ApiError('invalid_response');
      state.csrf = data.csrf_token;
      state.authenticated = true;
      byId('locked-screen').hidden = true;
      byId('console-content').hidden = false;
      byId('error-notice').hidden = true;
      setPill('session-pill', '本地管理会话已连接', 'info');
      const hashView = window.location.hash.slice(1);
      navigate(Object.hasOwn(views, hashView) ? hashView : 'overview');
      await refreshStatus();
    } catch (error) {
      if (generation !== state.generation) return;
      lock(messageFor(error));
    } finally { token = null; byId('session-retry').disabled = false; }
  }
  async function startLogin() {
    if (state.loginBusy || !state.status || state.status.login.state === 'running') return;
    const generation = state.generation;
    state.loginBusy = true;
    updateControls();
    try {
      const data = await request('/admin/api/login', { method: 'POST' });
      if (generation !== state.generation) return;
      if (!data || data.state !== 'running') throw new ApiError('invalid_response');
      state.status.login = { state: 'running', code: null };
      byId('login-state').textContent = '等待官方浏览器授权';
      byId('login-result').textContent = '请在本机打开的官方浏览器页面中确认授权。';
      byId('login-result').hidden = false;
      announce('已发起官方授权。请在本机浏览器中确认，180 秒内完成。');
      schedulePoll();
    } catch (error) {
      if (generation !== state.generation) return;
      announce(messageFor(error), true);
      // Read status after a conflict or uncertain response; never resend login.
      await refreshStatus();
    } finally { if (generation === state.generation) { state.loginBusy = false; updateControls(); } }
  }
  async function changeKey(action) {
    if (state.keyBusy || state.statusBusy || !state.status || !state.authenticated) return;
    const generation = state.generation;
    state.keyBusy = true;
    clearKey();
    const visibilityVersion = keyVisibilityVersion;
    updateControls();
    try {
      const data = await request('/admin/api/key', { method: 'POST', body: { action } });
      if (generation !== state.generation) return;
      if (!data || typeof data.key !== 'string' || !/^cp_[A-Za-z0-9_-]{16,256}$/.test(data.key)) throw new ApiError('invalid_response');
      if (state.view === 'key' && !document.hidden && visibilityVersion === keyVisibilityVersion) {
        byId('revealed-key').textContent = data.key;
        byId('key-reveal').hidden = false;
        keyTimer = window.setTimeout(() => clearKey(true), 60000);
        announce(action === 'rotate' ? '密钥已轮换，旧密钥已失效。请现在保存新密钥。' : '密钥已创建。请现在保存，只显示一次。');
      } else {
        announce('密钥操作已完成。因你已离开密钥页面，新密钥没有显示；若未保存，需要显式轮换。', true);
      }
      data.key = null;
      await refreshStatus();
    } catch (error) {
      if (generation !== state.generation) return;
      announce(messageFor(error), true);
      // A timed-out mutation can have succeeded: only read its resulting status.
      await refreshStatus();
    } finally { if (generation === state.generation) { state.keyBusy = false; updateControls(); } }
  }
  async function copyText(text, successMessage) {
    if (!text) return;
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error();
      await navigator.clipboard.writeText(text);
      announce(successMessage);
    } catch { announce('浏览器未允许复制。请选中对应文本，手动复制；不会自动重试。', true); }
  }
  function modelState(title, description) {
    const target = byId('models-state');
    target.replaceChildren();
    const heading = document.createElement('h3');
    heading.textContent = title;
    const note = document.createElement('p');
    note.textContent = description;
    target.append(heading, note);
    target.hidden = false;
  }
  function selectModel(id) {
    state.selectedModel = id;
    byId('selected-model-id').textContent = id;
    byId('client-model-id').textContent = id;
    byId('selected-model').hidden = false;
    document.querySelectorAll('[data-model-choice]').forEach((button) => {
      const selected = button.dataset.modelChoice === id;
      button.setAttribute('aria-pressed', String(selected));
      button.textContent = selected ? '已选择' : '选择';
    });
    announce('已选择模型 ID，可在客户端接入页面查看。此操作没有调用模型。');
  }
  function renderModels(models) {
    const list = byId('model-list');
    list.replaceChildren();
    if (!models.length) { list.hidden = true; modelState('本次没有返回模型', '不推测或补充模型名称。请确认账号资格，或稍后手动查询。'); return; }
    for (const model of models) {
      const row = document.createElement('li');
      row.className = 'model-row';
      const info = document.createElement('div');
      info.className = 'model-info';
      const title = document.createElement('strong');
      title.textContent = typeof model.name === 'string' && model.name ? model.name : model.id;
      const id = document.createElement('code');
      id.textContent = model.id;
      info.append(title, id);
      const actions = document.createElement('div');
      actions.className = 'model-actions';
      const select = document.createElement('button');
      select.type = 'button';
      select.className = 'btn btn-outline-secondary';
      select.textContent = '选择';
      select.dataset.modelChoice = model.id;
      select.setAttribute('aria-pressed', 'false');
      select.setAttribute('aria-label', '选择模型 ' + model.id);
      select.addEventListener('click', () => selectModel(model.id));
      const copy = document.createElement('button');
      copy.type = 'button';
      copy.className = 'text-button';
      copy.textContent = '复制 ID';
      copy.setAttribute('aria-label', '复制模型 ID ' + model.id);
      copy.addEventListener('click', () => copyText(model.id, '已复制模型 ID。'));
      actions.append(select, copy);
      row.append(info, actions);
      list.append(row);
    }
    list.hidden = false;
    modelState('查询完成 · ' + models.length + ' 个模型', '目录来自本次上游响应，不代表所有模型都可实际调用。');
  }
  async function loadModels() {
    if (state.modelsBusy || !state.authenticated || !state.status) return;
    const generation = state.generation;
    state.modelsBusy = true;
    state.selectedModel = null;
    byId('selected-model').hidden = true;
    byId('selected-model-id').textContent = '';
    byId('client-model-id').textContent = '请先查询并选择实际模型 ID';
    byId('model-list').replaceChildren();
    byId('model-list').hidden = true;
    modelState('正在读取上游模型目录…', '不发送推理请求；请等待本次查询完成。');
    byId('view-models').setAttribute('aria-busy', 'true');
    updateControls();
    try {
      const data = await request('/admin/api/models');
      if (generation !== state.generation) return;
      if (!data || data.object !== 'list' || !Array.isArray(data.data) || data.data.some((model) => !model || typeof model.id !== 'string' || !model.id || model.id.length > 1024)) throw new ApiError('invalid_response');
      renderModels(data.data);
    } catch (error) {
      if (generation !== state.generation) return;
      modelState('模型查询未完成', messageFor(error));
      announce(messageFor(error), true);
    } finally {
      if (generation === state.generation) {
        state.modelsBusy = false;
        byId('view-models').setAttribute('aria-busy', 'false');
        updateControls();
      }
    }
  }

  document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => go(button.dataset.view)));
  document.querySelectorAll('[data-go]').forEach((button) => button.addEventListener('click', () => go(button.dataset.go)));
  document.querySelectorAll('[data-copy-base]').forEach((button) => button.addEventListener('click', () => copyText(state.baseUrl, '已复制本地 API 地址。')));
  byId('menu-toggle').addEventListener('click', () => {
    const open = byId('sidebar-navigation').classList.toggle('open');
    byId('menu-toggle').setAttribute('aria-expanded', String(open));
    byId('menu-toggle').setAttribute('aria-label', open ? '收起导航' : '展开导航');
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && byId('sidebar-navigation').classList.contains('open')) { closeMenu(); byId('menu-toggle').focus(); }
  });
  byId('refresh-status').addEventListener('click', () => refreshStatus(true));
  byId('session-retry').addEventListener('click', () => establishSession());
  byId('start-login').addEventListener('click', startLogin);
  byId('init-key').addEventListener('click', () => changeKey('init'));
  byId('rotate-key').addEventListener('click', () => {
    if (byId('rotate-key').disabled) return;
    byId('rotate-dialog').showModal();
  });
  byId('cancel-rotate').addEventListener('click', () => byId('rotate-dialog').close());
  byId('confirm-rotate').addEventListener('click', () => { byId('rotate-dialog').close(); changeKey('rotate'); });
  byId('copy-key').addEventListener('click', () => copyText(byId('revealed-key').textContent, '已复制密钥。请仅保存到你信任的客户端；剪贴板可能被其他本机应用读取。'));
  byId('clear-key').addEventListener('click', () => clearKey(true));
  byId('load-models').addEventListener('click', loadModels);
  byId('copy-selected-model').addEventListener('click', () => copyText(state.selectedModel, '已复制模型 ID。'));
  window.addEventListener('hashchange', () => {
    let token;
    try { token = takeBootstrapToken(); } catch { lock('无法安全移除启动链接中的密钥，因此没有发起管理请求。请关闭此页并重新打开新链接。'); return; }
    if (token !== null) { establishSession(token); token = null; return; }
    navigate(window.location.hash.slice(1), true);
  });
  window.addEventListener('pagehide', () => { clearKey(); stopPolling(); state.csrf = null; });
  window.addEventListener('pageshow', (event) => { if (event.persisted) establishSession(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden) clearKey(); });

  updateControls();
  if (fragmentRemovalFailed) {
    lock('无法安全移除启动链接中的密钥，因此没有发起管理请求。请关闭此页并重新打开新链接。');
  } else {
    const token = initialToken;
    initialToken = null;
    establishSession(token === undefined ? null : token);
  }
})();
