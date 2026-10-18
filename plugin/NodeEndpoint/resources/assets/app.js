'use strict';
(() => {
  const el = id => document.getElementById(id);
  let token = '', generated = '', caPassphrase = '', caPassphraseTimer = null, caRevealEpoch = 0, caRevealController = null;
  let caPassphraseMetadata = [];
  function clearCaPassphrase() {
    caRevealEpoch++;
    if (caRevealController) { caRevealController.abort(); caRevealController = null; }
    if (caPassphraseTimer !== null) { clearTimeout(caPassphraseTimer); caPassphraseTimer = null; }
    caPassphrase = '';
    el('ca-passphrase-value').value = '';
    el('ca-passphrase-display').hidden = true;
    el('copy-ca-passphrase').disabled = true;
  }
  function updateCaPassphraseSelection() {
    const available = caPassphraseMetadata.some(row => row.fingerprint_sha256 === el('ca-passphrase-fingerprint').value && row.available);
    el('show-ca-passphrase').disabled = !available || location.protocol !== 'https:';
    el('ca-passphrase-status').textContent = location.protocol !== 'https:' && available
      ? '显示口令需要 HTTPS，请通过面板的 HTTPS 地址打开此页。'
      : available ? '当前 CA 的口令已加密保存。点击显示后可查看或复制。'
        : caPassphraseMetadata.length ? '当前 CA 没有自动保存的口令，请继续使用原有手动口令。'
          : '导入当前根 CA 后可查看自动保存状态。';
  }
  function renderCaPassphrases(metadata) {
    clearCaPassphrase();
    caPassphraseMetadata = Array.isArray(metadata) ? metadata.filter(row => /^[a-f0-9]{64}$/.test(row?.fingerprint_sha256) && typeof row.available === 'boolean') : [];
    const select = el('ca-passphrase-fingerprint'); select.replaceChildren();
    for (const row of caPassphraseMetadata) {
      const option = document.createElement('option'); option.value = row.fingerprint_sha256;
      option.textContent = row.fingerprint_sha256 + (row.available ? ' · 已保存' : ' · 手动口令');
      select.append(option);
    }
    if (!caPassphraseMetadata.length) { const option = document.createElement('option'); option.value = ''; option.textContent = '尚未导入 CA'; select.append(option); }
    else { select.value = (caPassphraseMetadata.find(row => row.available) || caPassphraseMetadata[0]).fingerprint_sha256; }
    updateCaPassphraseSelection();
  }
  function clearCommand() { generated = ''; el('command-result').textContent = '请根据当前已保存设置重新生成命令。'; }
  function message(text, error = false) { el('notice').textContent = text; el('notice').className = error ? 'error' : ''; }
  async function request(path, body, raw = false, signal) {
    const headers = { Accept: 'application/json', Authorization: token };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch('/api/v1/node-endpoint/' + path, { method: body === undefined ? 'GET' : 'POST', headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: 'omit', cache: 'no-store', redirect: 'error', signal });
    if (response.status === 401 || response.status === 403) {
      clearCaPassphrase(); token = ''; generated = '';
      el('workspace').hidden = true; el('login-panel').hidden = false;
    }
    if (raw && response.ok) return response.blob();
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || '请求失败（' + response.status + '）');
    return data.data;
  }
  const pretty = value => JSON.stringify(value, null, 2);
  async function load() {
    const data = await request('settings');
    const s = data.settings;
    el('enabled').checked = s.enabled; el('host').value = s.host; el('port').value = s.port;
    el('https').checked = s.https; el('http-ack').checked = s.http_acknowledged; el('ws-mode').value = s.ws_mode;
    el('cert-status').textContent = data.ca.length ? pretty(data.ca) : '尚未导入根 CA。';
    renderCaPassphrases(data.ca_passphrases);
    el('probe-result').textContent = s.last_probe ? pretty(s.last_probe) : '尚未测试。REST / WSS: NOT TESTED';
    el('root-status').textContent = s.root_status ? pretty({ imported_at: s.status_imported_at, snapshot: s.root_status }) : '尚未导入。';
    el('compatibility').textContent = pretty(data.compatibility) + '\n' + data.note;
    el('enabled-badge').textContent = s.enabled ? '已保存 · 等待实际入口验证' : '独立入口未启用';
    el('login-panel').hidden = true; el('workspace').hidden = false; updateUrls();
    clearCommand();
  }
  function updateUrls() {
    const host = el('host').value.trim(), port = el('port').value;
    const authority = (host.includes(':') ? '[' + host + ']' : host) + ':' + port;
    el('url').textContent = host ? (el('https').checked ? 'https://' : 'http://') + authority : '—';
    el('ws-url').textContent = host ? (el('https').checked ? 'wss://' : 'ws://') + authority + '/ws' : '—';
    el('http-risk').hidden = el('https').checked;
  }
  function task(id, event, fn) {
    el(id).addEventListener(event, async e => {
      e.preventDefault(); const button = e.currentTarget.tagName === 'BUTTON' ? e.currentTarget : e.currentTarget.querySelector('button');
      if (button) button.disabled = true;
      try { await fn(e); } catch (error) { if (error.name !== 'AbortError') message(error.message, true); }
      finally { if (button) button.disabled = false; }
    });
  }
  task('login-form', 'submit', async () => {
    const password = el('password').value; el('password').value = '';
    const res = await fetch('/api/v1/passport/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify({ email: el('email').value, password }), credentials: 'omit', redirect: 'error' });
    if (res.status === 401 || res.status === 403) { clearCaPassphrase(); token = ''; }
    const data = await res.json();
    if (!res.ok || !data.data?.auth_data) throw new Error(data.message || '登录失败；启用验证码时请使用管理员 Token 验证。');
    token = data.data.auth_data;
    if (!/^Bearer /i.test(token)) token = 'Bearer ' + token;
    try { await load(); message('管理员验证成功。'); } catch (error) { token = ''; throw error; }
  });
  task('token-form', 'submit', async () => {
    token = el('admin-token').value.trim(); el('admin-token').value = '';
    if (!/^Bearer /i.test(token)) token = 'Bearer ' + token;
    try { await load(); message('管理员验证成功。'); } catch (error) { token = ''; throw error; }
  });
  el('logout').addEventListener('click', () => { clearCaPassphrase(); token = ''; generated = ''; location.reload(); });
  window.addEventListener('pagehide', clearCaPassphrase);
  document.addEventListener('visibilitychange', () => { if (document.hidden) clearCaPassphrase(); });
  el('hide-ca-passphrase').addEventListener('click', clearCaPassphrase);
  el('ca-passphrase-fingerprint').addEventListener('change', () => { clearCaPassphrase(); updateCaPassphraseSelection(); });
  task('show-ca-passphrase', 'click', async () => {
    clearCaPassphrase();
    if (document.hidden || location.protocol !== 'https:') throw new Error('请通过 HTTPS 打开此页后显示 CA 口令。');
    const fingerprint = el('ca-passphrase-fingerprint').value, epoch = caRevealEpoch;
    const controller = new AbortController(); caRevealController = controller;
    const data = await request('ca-passphrase/reveal', { fingerprint }, false, controller.signal);
    if (epoch !== caRevealEpoch || document.hidden || !token) return;
    if (data.fingerprint_sha256 !== fingerprint || !/^[A-Za-z0-9_-]{64}$/.test(data.passphrase)) throw new Error('CA 口令不可用。');
    caRevealController = null; caPassphrase = data.passphrase;
    el('ca-passphrase-value').value = caPassphrase; el('ca-passphrase-display').hidden = false;
    el('copy-ca-passphrase').disabled = false;
    caPassphraseTimer = setTimeout(clearCaPassphrase, 60000);
  });
  task('copy-ca-passphrase', 'click', async () => {
    if (!caPassphrase || document.hidden) throw new Error('请先显示当前 CA 口令。');
    await navigator.clipboard.writeText(caPassphrase); message('CA 口令已复制，请妥善保管。');
  });
  ['host', 'port', 'https'].forEach(id => el(id).addEventListener('input', () => { updateUrls(); clearCommand(); }));
  el('settings-form').addEventListener('change', clearCommand);
  el('command-form').addEventListener('input', clearCommand);
  el('command-form').addEventListener('change', clearCommand);
  el('mode').addEventListener('change', () => { el('type-label').hidden = el('mode').value === 'machine'; el('unused-identity').checked = false; });
  el('kernel').addEventListener('change', () => {
    const unsupported = ['tuic', 'anytls'];
    for (const option of el('node-type').options) option.disabled = el('kernel').value === 'xray' && unsupported.includes(option.value);
    if (el('node-type').selectedOptions[0].disabled) el('node-type').value = 'vless';
    el('unused-identity').checked = false;
  });
  el('identity').addEventListener('input', () => { el('unused-identity').checked = false; });
  task('settings-form', 'submit', async () => {
    const data = await request('settings', { enabled: el('enabled').checked, host: el('host').value, port: Number(el('port').value), https: el('https').checked, http_acknowledged: el('http-ack').checked, ws_mode: el('ws-mode').value });
    await load(); message(data.note);
  });
  task('detect-ip', 'click', async () => { const data = await request('detect-ip', {}); el('detected-ip').textContent = '检测到 ' + data.detected_ip + '。' + data.note; });
  task('probe', 'click', async () => { const data = await request('probe', {}); el('probe-result').textContent = pretty(data); message('连接检查已完成；请分别查看 TLS、REST 与 WSS 的结果。'); });
  task('ca-form', 'submit', async () => {
    clearCaPassphrase();
    const file = el('ca-file').files[0]; if (!file || file.size > 32768) throw new Error('请选择不超过 32KB 的 CA 公共证书。');
    const data = await request('ca', { pem: await file.text(), fingerprints: el('fingerprints').value.split(',').map(s => s.trim()) });
    renderCaPassphrases((await request('settings')).ca_passphrases);
    el('cert-status').textContent = pretty(data); clearCommand(); message('CA 指纹与证书用途已核对。请在 Node 上独立核对同一指纹。');
  });
  task('download-ca', 'click', async () => {
    const blob = await request('ca', undefined, true), url = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = 'node-endpoint-ca.pem'; a.click(); URL.revokeObjectURL(url);
  });
  task('import-status', 'click', async () => {
    const file = el('status-file').files[0]; if (!file || file.size > 32768) throw new Error('请选择不超过 32KB 的公共状态 JSON。');
    const data = await request('status-import', { status: JSON.parse(await file.text()) }); await load(); message(data.note);
  });
  task('command-form', 'submit', async () => {
    const data = await request('command', { mode: el('mode').value, identity_id: Number(el('identity').value), kernel: el('kernel').value, architecture: el('architecture').value, instance: el('instance').value, node_type: el('node-type').value });
    generated = data.prepare + '\n\n' + data.install;
    el('command-result').textContent = generated + '\n\n' + data.notes.join('\n'); message('对接命令已生成；请核对目标实例与独立可信 CA 指纹。');
  });
  task('copy-command', 'click', async () => { if (!generated) throw new Error('请先生成命令。'); await navigator.clipboard.writeText(generated); message('命令已复制。'); });
})();
