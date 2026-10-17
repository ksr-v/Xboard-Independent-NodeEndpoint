/* NodeEndpoint 1.0.3: reversible native machine installation choices.
 * Observe only native, same-origin machine responses. Never read auth storage,
 * modify React state, or replace the browser clipboard implementation. */
(() => {
  'use strict';
  const RUNTIME = '__NODE_ENDPOINT_INSTALL_UI__';
  if (window[RUNTIME]) return;

  const OWNER = 'node_endpoint';
  const CARD = '[data-ne-install="card"]';
  const HIDDEN = 'data-ne-install-hidden';
  const STATUS_URL = '/node-endpoint/status';
  const LIMIT = 20;
  // Native React Query may reopen a cached command without another HTTP call.
  // Retain a bounded memory-only copy across ordinary Admin navigation.
  const TTL = 600000;
  const MAX_COMMAND = 65536;
  const MAX_RESPONSE = 163840;
  const entries = new Map();
  const cards = new Map();
  const removers = [];
  const fetchBefore = typeof window.fetch === 'function' ? window.fetch : null;
  const xhrPrototype = window.XMLHttpRequest?.prototype;
  const sendBefore = xhrPrototype?.send;
  let enabled = false, stopped = false, queued = false, serial = 0, generation = 0;
  let statusBusy = false, statusController = null;
  let fetchWrapper = null, sendWrapper = null;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function listen(target, event, listener, options) {
    target.addEventListener(event, listener, options);
    removers.push(() => target.removeEventListener(event, listener, options));
  }

  function machineResponse(value) {
    try {
      const url = new URL(value, location.href);
      if (url.origin !== location.origin || url.pathname.length > 512 ||
          !url.pathname.startsWith('/api/v2/')) return '';
      return /\/server\/machine\/(installCommand|save)\/?$/.exec(url.pathname)?.[1] || '';
    } catch { return ''; }
  }

  function validCommand(value) {
    return typeof value === 'string' && value.length > 0 && value.length <= MAX_COMMAND &&
      !value.includes('\u0000');
  }
  function authenticationRoute() {
    return /(?:^|\/)(?:login|auth|logout|sign-in|sign-up|sign-out)(?:[/?#]|$)/i.test(
      location.pathname + location.hash.slice(1));
  }

  function hide(node) {
    const previous = node.getAttribute(HIDDEN);
    node.setAttribute(HIDDEN, OWNER);
    return {node, previous};
  }

  function restore(marker) {
    if (marker.node.getAttribute(HIDDEN) !== OWNER) return;
    if (marker.previous === null) marker.node.removeAttribute(HIDDEN);
    else marker.node.setAttribute(HIDDEN, marker.previous);
  }

  function removeCard(record) {
    record.preview.value = '';
    record.message.textContent = '';
    record.address.textContent = '';
    for (const marker of record.hidden) restore(marker);
    record.node.remove();
    cards.delete(record.code);
  }

  function forget(key) {
    for (const record of [...cards.values()]) if (record.key === key) removeCard(record);
    entries.delete(key);
  }

  function clearCommands() {
    generation++;
    for (const record of [...cards.values()]) removeCard(record);
    entries.clear();
  }

  function remember(payload, kind) {
    if (stopped || !payload || typeof payload !== 'object') return;
    const data = payload.data;
    const options = data && typeof data === 'object' ? data.install_options : null;
    const nativeCommand = data?.[kind === 'save' ? 'install_command' : 'command'];
    if (!options || options.owner !== OWNER || options.schema !== 1 ||
        options.domain?.available !== true || !validCommand(options.domain.command) ||
        nativeCommand !== options.domain.command) return;
    const endpoint = options.endpoint;
    if (!endpoint || typeof endpoint.available !== 'boolean' ||
        (endpoint.available && !validCommand(endpoint.command))) return;

    const key = options.domain.command;
    const entry = {
      domain: options.domain.command,
      endpoint: endpoint.available ? endpoint.command : '',
      available: endpoint.available,
      message: endpoint.available ? '' : typeof endpoint.message === 'string' ?
        endpoint.message.slice(0, 500) : 'IP 对接尚未就绪，请先配置独立入口并导入公共 CA。',
      expires: Date.now() + TTL,
      matched: entries.get(key)?.matched === true,
      mode: entries.get(key)?.mode === 'endpoint' && endpoint.available ? 'endpoint' : 'domain',
    };
    entries.delete(key);
    entries.set(key, entry);
    while (entries.size > LIMIT) forget(entries.keys().next().value);
    for (const record of cards.values()) if (record.key === key) render(record, entry);
    schedule();
  }

  function observePayload(status, text, kind, revision) {
    if (stopped) return;
    if (status === 401 || status === 403) { clearCommands(); return; }
    if (revision !== generation) return;
    if (status !== 200 || typeof text !== 'string' || text.length > MAX_RESPONSE) return;
    try { remember(JSON.parse(text), kind); } catch { /* The native response is untouched. */ }
  }

  async function observeFetch(response, revision) {
    if (stopped || !response) return;
    const kind = machineResponse(response.url);
    if (!kind) return;
    if (response.status === 401 || response.status === 403) { clearCommands(); return; }
    if (revision !== generation) return;
    if (response.status !== 200) return;
    let reader;
    try {
      const length = Number(response.headers.get('content-length') || '0');
      if (length > MAX_RESPONSE) return;
      const clone = response.clone();
      if (!clone.body?.getReader) return;
      reader = clone.body.getReader();
      const chunks = [];
      let lengthRead = 0;
      while (!stopped) {
        const next = await reader.read();
        if (next.done) break;
        lengthRead += next.value.byteLength;
        if (lengthRead > MAX_RESPONSE) { reader.cancel().catch(() => {}); return; }
        chunks.push(next.value);
      }
      if (stopped) { reader.cancel().catch(() => {}); return; }
      const bytes = new Uint8Array(lengthRead);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      observePayload(response.status, new TextDecoder().decode(bytes), kind, revision);
    } catch { /* A failed observation must not affect the native request. */ }
    finally { try { reader?.releaseLock(); } catch {} }
  }

  if (fetchBefore) {
    fetchWrapper = function (...args) {
      const revision = generation;
      const result = Reflect.apply(fetchBefore, this, args);
      if (!stopped) Promise.resolve(result).then(response => observeFetch(response, revision)).catch(() => {});
      return result;
    };
    window.fetch = fetchWrapper;
  }
  if (typeof sendBefore === 'function') {
    sendWrapper = function (...args) {
      const revision = generation;
      if (!stopped) this.addEventListener('loadend', () => {
        if (stopped) return;
        try {
          const kind = machineResponse(this.responseURL);
          if (!kind) return;
          if (this.status === 401 || this.status === 403) { clearCommands(); return; }
          if (revision !== generation) return;
          if (this.status !== 200) return;
          if (this.responseType === 'json') {
            // JSON response mode is already parsed by the browser. Bound its
            // relevant strings in remember(), without changing the response.
            remember(this.response, kind);
          } else if (this.responseType === '' || this.responseType === 'text') {
            observePayload(this.status, this.responseText, kind, revision);
          }
        } catch { /* Unsupported native response types are ignored. */ }
      }, {once: true});
      return Reflect.apply(sendBefore, this, args);
    };
    xhrPrototype.send = sendWrapper;
  }

  function panelAddress(command) {
    const match = /(?:^|\s)(?:--panel|'--panel'|"--panel")\s+(?:'([^']*)'|"([^"]*)"|([^\s]+))/.exec(command);
    const value = match && (match[1] ?? match[2] ?? match[3]);
    if (!value || value.length > 1024) return '地址由安装命令指定';
    try {
      const url = new URL(value);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
          url.search || url.hash) return '地址由安装命令指定';
      return url.origin + (url.pathname === '/' ? '' : url.pathname);
    } catch { return '地址由安装命令指定'; }
  }

  function render(record, entry) {
    record.mode = entry.available && entry.mode === 'endpoint' ? 'endpoint' : 'domain';
    record.domain.checked = record.mode === 'domain';
    record.endpoint.checked = record.mode === 'endpoint';
    record.endpoint.disabled = !entry.available;
    record.node.setAttribute('data-ne-install-mode', record.mode);
    record.preview.value = record.mode === 'endpoint' ? entry.endpoint : entry.domain;
    record.address.textContent = '对接地址：' + panelAddress(record.preview.value);
    record.unavailable.textContent = entry.available ? '' : entry.message;
    record.unavailable.hidden = entry.available;
    record.message.textContent = '';
  }

  function nativeParts(code) {
    const container = code.parentElement;
    if (!container || container.closest(CARD)) return null;
    const children = [...container.children];
    if (children.filter(node => node.tagName === 'CODE').length !== 1 ||
        children.filter(node => node.tagName === 'BUTTON').length !== 1) return null;
    if (container.classList.contains('group') && container.classList.contains('relative')) {
      const footer = container.nextElementSibling;
      if (!footer || footer.tagName !== 'DIV' || footer.querySelectorAll('button').length !== 1 ||
          !footer.querySelector('p') || footer.querySelector('code,input,textarea')) return null;
      return {container, footer};
    }
    if (container.classList.contains('flex') && container.classList.contains('items-start')) {
      return {container, footer: null};
    }
    return null;
  }

  function mount(code, key, entry) {
    const parts = nativeParts(code);
    if (!parts) return;
    const node = element('section', 'ne-install-card');
    node.setAttribute('data-ne-install', 'card');
    const fieldset = element('fieldset', 'ne-install-options');
    fieldset.append(element('legend', 'ne-install-legend', '选择节点对接方式'));
    const group = 'ne-install-mode-' + (++serial);
    function choice(mode, title, hint) {
      const label = element('label', 'ne-install-choice');
      const input = element('input');
      input.type = 'radio'; input.name = group; input.value = mode;
      const text = element('span', 'ne-install-choice-text');
      text.append(element('strong', '', title), element('small', '', hint));
      label.append(input, text); fieldset.append(label);
      return input;
    }
    const domain = choice('domain', '域名对接（独立部署）', '使用面板域名和独立部署安装命令');
    const endpoint = choice('endpoint', '新版：IP + 私有证书', '使用独立 IP 入口并自动配置公共 CA 信任');
    const address = element('p', 'ne-install-address');
    const unavailable = element('p', 'ne-install-unavailable');
    const preview = element('textarea', 'ne-install-preview');
    preview.readOnly = true; preview.rows = 5; preview.spellcheck = false;
    preview.setAttribute('aria-label', '当前选中的节点安装命令');
    const footer = element('div', 'ne-install-footer');
    const message = element('p', 'ne-install-message');
    message.setAttribute('role', 'status'); message.setAttribute('aria-live', 'polite');
    const copy = element('button', 'ne-install-copy', '复制安装命令');
    copy.type = 'button';
    footer.append(message, copy);
    node.append(fieldset, address, unavailable, preview, footer);
    const record = {code, key, node, mode: 'domain', domain, endpoint, address, unavailable, preview, message, hidden: []};
    for (const input of [domain, endpoint]) input.addEventListener('change', () => {
      const current = entries.get(record.key);
      if (!current || !record.node.isConnected || record.code.textContent !== record.key) { forget(record.key); return; }
      current.expires = Date.now() + TTL;
      if (!input.checked || input.disabled) return;
      record.mode = input.value;
      current.mode = input.value;
      render(record, current);
    });
    copy.addEventListener('click', async () => {
      const current = entries.get(record.key);
      if (!enabled || stopped || !current ||
          !record.node.isConnected || record.code.textContent !== record.key) {
        forget(record.key); return;
      }
      current.expires = Date.now() + TTL;
      const command = record.mode === 'endpoint' ? current.endpoint : current.domain;
      if (!command) return;
      try {
        await navigator.clipboard.writeText(command);
        if (record.node.isConnected && record.preview.value === command) record.message.textContent = '已复制安装命令。';
      } catch {
        if (!record.node.isConnected) return;
        record.preview.focus(); record.preview.select();
        record.message.textContent = '自动复制不可用，已选中命令，请手动复制。';
      }
    });
    render(record, entry);
    parts.container.after(node);
    record.hidden.push(hide(parts.container));
    if (parts.footer) record.hidden.push(hide(parts.footer));
    cards.set(code, record);
    entry.matched = true;
  }

  function reconcile() {
    queued = false;
    if (stopped) return;
    // React Router can use pushState for logout without a hashchange event.
    if (authenticationRoute()) {
      if (entries.size || cards.size) clearCommands();
      return;
    }
    const now = Date.now();
    for (const [key, entry] of entries) if (entry.expires <= now) {
      // The original Admin already keeps this command in its open dialog.
      // Retain visible choices rather than silently reverting to domain mode.
      if ([...cards.values()].some(record => record.key === key && record.node.isConnected &&
          record.code.isConnected && record.code.textContent === key)) entry.expires = now + TTL;
      else forget(key);
    }
    for (const record of [...cards.values()]) {
      if (!enabled || !record.code.isConnected || !record.node.isConnected ||
          record.code.textContent !== record.key || !entries.has(record.key)) removeCard(record);
    }
    const nativeCodes = [...document.querySelectorAll('code')].slice(0, 1000).filter(code => !code.closest(CARD));
    if (!enabled || !entries.size) return;
    for (const code of nativeCodes) {
      if (cards.has(code)) continue;
      const key = code.textContent;
      const entry = entries.get(key);
      if (entry) mount(code, key, entry);
    }
  }

  function schedule() {
    if (stopped || queued) return;
    queued = true;
    queueMicrotask(reconcile);
  }

  const observer = new MutationObserver(records => {
    if (records.some(record => {
      const target = record.target.nodeType === 1 ? record.target : record.target.parentElement;
      if (target?.closest(CARD)) return false;
      if (record.type === 'childList' && [...record.addedNodes, ...record.removedNodes].every(node =>
        node.nodeType === 1 && node.matches(CARD))) return false;
      return true;
    })) schedule();
  });
  observer.observe(document.documentElement, {subtree: true, childList: true, characterData: true});

  function destroy() {
    if (stopped) return;
    stopped = true; enabled = false;
    observer.disconnect();
    clearInterval(maintenanceTimer); clearInterval(statusTimer);
    statusController?.abort();
    for (const remove of removers.splice(0)) remove();
    clearCommands();
    if (fetchWrapper && window.fetch === fetchWrapper) window.fetch = fetchBefore;
    if (sendWrapper && xhrPrototype.send === sendWrapper) xhrPrototype.send = sendBefore;
    if (window[RUNTIME]?.destroy === destroy) delete window[RUNTIME];
  }

  async function checkStatus() {
    if (stopped || statusBusy || !fetchBefore) return;
    statusBusy = true;
    const controller = new AbortController();
    statusController = controller;
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await Reflect.apply(fetchBefore, window, [STATUS_URL, {
        method: 'GET', credentials: 'omit', cache: 'no-store', redirect: 'error',
        headers: {Accept: 'application/json'}, signal: controller.signal,
      }]);
      if (stopped) return;
      if ([401, 403, 404, 410].includes(response.status)) { destroy(); return; }
      if (!response.ok) return;
      const text = await response.text();
      if (stopped || text.length > 1024) return;
      const data = JSON.parse(text);
      if (data?.enabled === false) { destroy(); return; }
      if (data?.enabled === true) { enabled = true; schedule(); }
    } catch { /* Network failures preserve the current UI and native behavior. */ }
    finally {
      clearTimeout(timeout);
      if (statusController === controller) statusController = null;
      statusBusy = false;
    }
  }

  let routeKey = location.pathname + location.hash.split('?')[0];
  function routeChanged() {
    const current = location.pathname + location.hash.split('?')[0];
    // Native details update only the hash query. Their concurrent request may
    // already have completed; let exact command matching reconcile that case.
    if (current !== routeKey) {
      routeKey = current;
      if (authenticationRoute()) clearCommands();
    }
    schedule();
  }
  listen(window, 'hashchange', routeChanged);
  listen(window, 'popstate', routeChanged);
  listen(window, 'pagehide', destroy);
  listen(window, 'storage', event => {
    // Reacting to the key does not inspect or retain any stored Token value.
    if (event.key === null || event.key === 'XBOARD_ACCESS_TOKEN') clearCommands();
  });
  listen(window, 'logout', clearCommands);
  const maintenanceTimer = setInterval(schedule, 5000);
  const statusTimer = setInterval(checkStatus, 10000);
  window[RUNTIME] = Object.freeze({destroy});
  checkStatus();
})();
