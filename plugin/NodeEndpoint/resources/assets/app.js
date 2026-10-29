'use strict';
(() => {
  const el = id => document.getElementById(id);
  let token = '', generated = '', caPassphrase = '', caPassphraseTimer = null, caRevealEpoch = 0, caRevealController = null;
  let caPassphraseMetadata = [];
  let rootTool = null, savedSettingsKey = '', settingsRevision = 0, settingsLoadEpoch = 0;
  const ADMIN_KEY = 'XBOARD_ACCESS_TOKEN', SUPPRESS_KEY = 'node_endpoint_sso_paused';
  const actions = { configure: '配置 HTTPS 入口', refresh: '刷新入口配置与状态', renew: '续签服务器证书', firewall: '配置入口防火墙' };
  const stageLabels = { preflight: '检查目标与权限', entry: '配置通讯入口', configure: '配置通讯入口', certificate: '检查与更新服务器证书', firewall: '检查入口防火墙', ca_sync: '同步公共 CA 与状态', backup: '备份当前配置', reload: '重载入口服务', verify: '验证入口配置', renew: '续签服务器证书', refresh: '刷新入口状态', status: '读取公共状态', complete: '完成检查' };
  const states = { pending: '等待执行', queued: '排队中', running: '执行中', succeeded: '成功', partial: '部分完成', failed: '失败', interrupted: '已中断', skipped: '已跳过', planned: '待确认' };
  const finalStates = new Set(['succeeded', 'partial', 'failed', 'interrupted']);
  let authMode = '', authEpoch = 0, sharedExpires = 0, expiryTimer = null, ssoPaused = false;
  let helper = null, rootPlan = null, rootJob = null, rootPoll = null, planTimer = null;
  let rootStatusEpoch = 0, rootPlanEpoch = 0, rootJobEpoch = 0, rootPlanBusy = false, rootExecuteBusy = false;
  try { ssoPaused = sessionStorage.getItem(SUPPRESS_KEY) === '1'; } catch { /* Storage may be disabled. */ }
  function pauseSso(value) {
    ssoPaused = value;
    try { if (value) sessionStorage.setItem(SUPPRESS_KEY, '1'); else sessionStorage.removeItem(SUPPRESS_KEY); } catch { /* Memory state still applies. */ }
  }
  function readAdminSession() {
    try {
      const raw = localStorage.getItem(ADMIN_KEY);
      if (!raw || raw.length > 8192) return null;
      const data = JSON.parse(raw);
      if (typeof data?.value !== 'string' || !/^Bearer [A-Za-z0-9._~+\/-]+=*$/i.test(data.value) ||
          typeof data.expire !== 'number' || !Number.isFinite(data.expire) || data.expire <= Date.now()) return null;
      return { token: data.value, expires: data.expire };
    } catch { return null; }
  }
  function clearRootPlan() {
    rootPlanEpoch++; rootPlan = null; rootPlanBusy = false;
    if (planTimer !== null) { clearInterval(planTimer); planTimer = null; }
    el('root-plan-panel').hidden = true; el('root-confirmation').value = '';
    el('root-plan-summary').replaceChildren(); el('root-plan-stages').replaceChildren();
    el('root-confirmation-text').textContent = ''; el('root-plan-expiry').textContent = '';
    updateRootControls();
  }
  function stopRootPolling() { if (rootPoll !== null) { clearTimeout(rootPoll); rootPoll = null; } }
  function invalidateSession(note = '') {
    authEpoch++; token = ''; authMode = ''; sharedExpires = 0;
    if (expiryTimer !== null) { clearTimeout(expiryTimer); expiryTimer = null; }
    clearCaPassphrase(); clearCommand(); stopRootPolling(); clearRootPlan(); clearRootTool();
    rootStatusEpoch++; rootJobEpoch++; helper = null; rootJob = null; rootExecuteBusy = false;
    el('root-job-panel').hidden = true; el('root-job-state').textContent = ''; el('root-job-backup').textContent = '';
    el('root-job-stages').replaceChildren(); el('root-job-error').textContent = '';
    el('root-helper-controls').hidden = true; el('workspace').hidden = true; el('login-panel').hidden = false;
    el('cert-status').textContent = '尚未导入根 CA。'; el('root-status').textContent = '尚未导入。';
    el('probe-result').textContent = '尚未测试。REST / WSS: NOT TESTED'; el('compatibility').textContent = '';
    el('detected-ip').textContent = ''; renderCaPassphrases([]);
    if (note) { el('session-status').textContent = note; message(note, true); }
  }
  function syncAdminSession() {
    if (authMode !== 'shared') return Boolean(token);
    const current = readAdminSession();
    if (!current || current.token !== token) {
      invalidateSession('后台登录已退出、切换或过期。请重新选择“使用当前后台登录”。'); return false;
    }
    sharedExpires = current.expires;
    return true;
  }
  function scheduleSessionExpiry() {
    if (expiryTimer !== null) clearTimeout(expiryTimer);
    if (authMode === 'shared') expiryTimer = setTimeout(() => {
      expiryTimer = null;
      if (syncAdminSession()) scheduleSessionExpiry();
    }, Math.max(1, Math.min(sharedExpires - Date.now(), 2147483647)));
  }
  async function useAdminSession(explicit = false) {
    if (!explicit && ssoPaused) { el('session-status').textContent = '已退出本页；点击上方按钮可再次使用后台登录。'; return; }
    const current = readAdminSession();
    if (!current) { if (explicit) message('当前地址下没有有效的后台登录，请先登录后台，或使用管理员凭据验证。', true); return; }
    invalidateSession(); pauseSso(false); token = current.token; authMode = 'shared'; sharedExpires = current.expires;
    scheduleSessionExpiry();
    try { await load(); message('已使用当前后台管理员登录。'); }
    catch (error) { if (error.name !== 'AbortError') { invalidateSession(); message(error.message, true); } }
  }
  function settingsInput() {
    return { enabled: el('enabled').checked, host: el('host').value, port: Number(el('port').value),
      https: el('https').checked, http_acknowledged: el('http-ack').checked, ws_mode: el('ws-mode').value };
  }
  const settingsKey = s => JSON.stringify([s.enabled, s.host, s.port, s.https, s.http_acknowledged, s.ws_mode]);
  function refreshRootTool() {
    const dirty = settingsKey(settingsInput()) !== savedSettingsKey;
    el('root-tool-panel').hidden = !token;
    el('copy-root-tool').disabled = !token || dirty || !rootTool?.command;
    el('root-tool-target').textContent = dirty ? '设置尚未保存' : rootTool?.target_url || '—';
    el('root-tool-command').textContent = dirty ? '请先保存设置，再复制对应的 Root 工具命令。'
      : rootTool?.command || '当前设置没有可复制的 Root 工具命令。';
    el('root-tool-note').textContent = dirty ? '尚未保存的修改不会用于生成命令。' : rootTool?.note || '请先保存有效的 HTTPS 通讯设置。';
    updateRootControls();
  }
  function clearRootTool() { rootTool = null; savedSettingsKey = ''; settingsLoadEpoch++; refreshRootTool(); }
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
    if (!syncAdminSession() || !token) throw new Error('管理员会话已失效，请重新验证。');
    const session = token, epoch = authEpoch;
    const headers = { Accept: 'application/json', Authorization: token };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch('/api/v1/node-endpoint/' + path, { method: body === undefined ? 'GET' : 'POST', headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: 'omit', cache: 'no-store', redirect: 'error', signal });
    if (epoch !== authEpoch || token !== session || !syncAdminSession()) throw new DOMException('会话已变更。', 'AbortError');
    if (response.status === 401 || response.status === 403) {
      invalidateSession('管理员登录已过期或权限不足，请重新验证。');
      throw new Error('管理员登录已过期或权限不足，请重新验证。');
    }
    if (raw && response.ok) {
      const blob = await response.blob();
      if (epoch !== authEpoch || token !== session || !syncAdminSession()) throw new DOMException('会话已变更。', 'AbortError');
      return blob;
    }
    const data = await response.json();
    if (response.ok && (epoch !== authEpoch || token !== session || !syncAdminSession())) throw new DOMException('会话已变更。', 'AbortError');
    if (!response.ok) {
      const error = new Error(data.message || '请求失败（' + response.status + '）');
      error.status = response.status; error.code = typeof data.error?.code === 'string' ? data.error.code : '';
      throw error;
    }
    return data.data;
  }
  const pretty = value => JSON.stringify(value, null, 2);
  async function load(preserveForm = false) {
    const revision = settingsRevision, epoch = ++settingsLoadEpoch, session = token;
    const data = await request('settings');
    if (epoch !== settingsLoadEpoch || !token || token !== session) return;
    const s = data.settings;
    if (!preserveForm && revision === settingsRevision) {
      el('enabled').checked = s.enabled; el('host').value = s.host; el('port').value = s.port;
      el('https').checked = s.https; el('http-ack').checked = s.http_acknowledged; el('ws-mode').value = s.ws_mode;
    }
    savedSettingsKey = settingsKey(s); rootTool = data.root_tool || null; refreshRootTool();
    el('cert-status').textContent = data.ca.length ? pretty(data.ca) : '尚未导入根 CA。';
    renderCaPassphrases(data.ca_passphrases);
    el('probe-result').textContent = s.last_probe ? pretty(s.last_probe) : '尚未测试。REST / WSS: NOT TESTED';
    el('root-status').textContent = s.root_status ? pretty({ imported_at: s.status_imported_at, snapshot: s.root_status }) : '尚未导入。';
    el('compatibility').textContent = pretty(data.compatibility) + '\n' + data.note;
    el('enabled-badge').textContent = s.enabled ? '已保存 · 等待实际入口验证' : '独立入口未启用';
    el('login-panel').hidden = true; el('workspace').hidden = false; updateUrls();
    clearCommand();
    await readRootStatus();
    if (!token || token !== session) throw new DOMException('会话已变更。', 'AbortError');
  }
  const shortText = (value, max = 180) => typeof value === 'string' || typeof value === 'number' ? String(value).slice(0, max) : '';
  function timestamp(value) {
    if (typeof value === 'number' && Number.isFinite(value)) return value < 1e12 ? value * 1000 : value;
    return typeof value === 'string' ? Date.parse(value) : NaN;
  }
  function endpointText(target) {
    if (!target || typeof target.host !== 'string' || !Number.isInteger(Number(target.port))) return '请核对已保存入口';
    const host = shortText(target.host, 253);
    return (target.https ? 'https://' : 'http://') + (host.includes(':') ? '[' + host + ']' : host) + ':' + target.port;
  }
  function stageRows(stages) {
    if (Array.isArray(stages)) return stages.slice(0, 24).map(stage => typeof stage === 'string' ? { name: stage } : stage).filter(stage => stage && typeof stage === 'object');
    if (stages && typeof stages === 'object') return Object.entries(stages).slice(0, 24).map(([name, stage]) => typeof stage === 'string' ? { name, state: stage } : { ...stage, name });
    return [];
  }
  function renderStages(id, stages) {
    const list = el(id); list.replaceChildren();
    for (const stage of stageRows(stages)) {
      const row = document.createElement('li'), label = document.createElement('span'), badge = document.createElement('span');
      label.textContent = stageLabels[stage.name || stage.id] || shortText(stage.label || stage.name || stage.id, 80) || '操作阶段';
      badge.textContent = states[stage.state] || '待处理'; badge.className = 'stage-state';
      row.dataset.state = Object.hasOwn(states, stage.state) ? stage.state : 'pending'; row.append(label, badge); list.append(row);
    }
  }
  function summaryRow(label, value) {
    const term = document.createElement('dt'), detail = document.createElement('dd'); term.textContent = label; detail.textContent = value; el('root-plan-summary').append(term, detail);
  }
  function safeRootError(value) {
    const text = shortText(typeof value === 'object' ? value?.message : value, 240);
    return text && !/PRIVATE|BEGIN |socket|\/run\/|\/etc\/|\/var\/|token|passphrase|password|secret|\.pem/i.test(text)
      ? text : '操作未完成。请在服务器检查 Root 助手日志，核对当前状态后重试。';
  }
  function jobActive() { return rootJob && !finalStates.has(rootJob.state); }
  function rootBusy() { return Boolean(jobActive() || helper?.busy === true); }
  function updateRootControls() {
    const available = Boolean(token && helper?.available === true && location.protocol === 'https:'), dirty = !savedSettingsKey || settingsKey(settingsInput()) !== savedSettingsKey;
    const configureInvalid = el('root-action').value === 'configure' && (dirty || !el('https').checked);
    const busy = rootPlanBusy || rootExecuteBusy || rootBusy();
    el('root-plan').disabled = !available || busy || configureInvalid;
    el('sync-root-ca').disabled = !available || rootExecuteBusy || rootBusy();
    el('root-action').disabled = !available || busy;
    el('root-plan-hint').textContent = location.protocol !== 'https:' ? 'Root 操作需要通过面板的 HTTPS 地址访问本页。' : configureInvalid ? '配置入口前，请先保存有效的 HTTPS 设置。'
      : rootBusy() ? 'Root 有任务正在执行。完成后刷新助手状态，再生成下一次操作预览。' : '每次执行前均需核对预览并输入确认文字。';
    const expired = !rootPlan || !Number.isFinite(timestamp(rootPlan.expires_at)) || timestamp(rootPlan.expires_at) <= Date.now();
    el('root-execute').disabled = !available || busy || dirty || expired || !rootPlan || el('root-confirmation').value !== rootPlan.confirmation;
    el('copy-helper-install').disabled = !token || !rootTool?.web_manager_install_command;
    el('root-helper-install-command').textContent = token && rootTool?.web_manager_install_command || '当前版本未提供首次初始化命令，请升级固定版本的 Root 安装工具。';
    el('root-helper-install-note').textContent = token ? rootTool?.web_manager_install_note || '此命令只初始化助手；配置入口仍需在网页中预览并确认。' : '';
    if (rootPlan) el('root-plan-expiry').textContent = expired ? '此预览已过期，请重新生成。' : '预览有效期至 ' + new Date(timestamp(rootPlan.expires_at)).toLocaleTimeString() + '；剩余 ' + Math.max(0, Math.ceil((timestamp(rootPlan.expires_at) - Date.now()) / 1000)) + ' 秒。';
  }
  async function readRootStatus() {
    const epoch = ++rootStatusEpoch;
    try {
      const data = await request('root/status');
      if (epoch !== rootStatusEpoch) return;
      helper = data && typeof data === 'object' ? data : { available: false };
      el('root-helper-state').textContent = helper.available === true ? 'Root 助手已连接 · 版本 ' + (shortText(helper.version, 40) || '未知') : 'Root 助手当前不可用。';
      el('root-helper-unavailable').hidden = helper.available === true; el('root-helper-controls').hidden = helper.available !== true;
      const ports = Array.isArray(helper.policy?.allowed_ports) ? helper.policy.allowed_ports.filter(port => Number.isInteger(Number(port)) && Number(port) > 0 && Number(port) <= 65535).slice(0, 30).join('、') : '';
      el('root-helper-policy').textContent = ports ? '助手允许的入口端口：' + ports : '';
      updateRootControls();
      const active = helper.active_job || helper.last_job;
      const id = typeof active === 'string' || typeof active === 'number' ? String(active) : active?.id;
      if (helper.available === true && id) {
        try { await readRootJob(id); }
        catch (error) {
          if (error.name === 'AbortError' || !token || epoch !== rootStatusEpoch) return;
          if (error.status === 404 || error.code === 'not_found') {
            stopRootPolling(); rootJobEpoch++; rootJob = null;
            el('root-job-panel').hidden = true; el('root-job-state').textContent = ''; el('root-job-backup').textContent = '';
            el('root-job-stages').replaceChildren(); el('root-job-error').textContent = '';
            message('该任务记录不可用，可重新预览。', true);
          } else { message('助手已连接，但任务状态暂时无法读取。请刷新任务或助手状态重试。', true); }
          updateRootControls();
        }
      }
    } catch (error) {
      if (error.name === 'AbortError' || !token || epoch !== rootStatusEpoch) return;
      helper = { available: false }; el('root-helper-state').textContent = '暂时无法读取 Root 助手状态，请刷新重试。';
      el('root-helper-policy').textContent = ''; el('root-helper-controls').hidden = true; el('root-helper-unavailable').hidden = false; updateRootControls();
    }
  }
  function renderRootJob(data) {
    const job = data?.job || data;
    if (!job || !/^[A-Za-z0-9_-]{1,128}$/.test(String(job.id || '')) || typeof job.state !== 'string') throw new Error('助手返回的任务状态无效，请刷新状态。');
    rootJob = job; el('root-job-panel').hidden = false;
    if (helper && String(helper.active_job) === String(job.id) && finalStates.has(job.state)) { helper.active_job = null; helper.busy = false; }
    el('root-job-state').textContent = (actions[job.action] || 'Root 操作') + ' · ' + (states[job.state] || '状态待确认');
    const backup = job.result?.backup_id || job.backup_id;
    el('root-job-backup').textContent = backup ? '备份编号：' + shortText(backup, 128) : '';
    if (typeof job.result?.ca_preserved === 'boolean') el('root-job-backup').textContent += (backup ? ' · ' : '') + (job.result.ca_preserved ? '现有根 CA 已保留' : '已按预览创建根 CA，请独立核对指纹');
    renderStages('root-job-stages', job.stages);
    el('root-job-error').textContent = ['partial', 'failed', 'interrupted'].includes(job.state) ? safeRootError(job.error || job.message) : '';
    updateRootControls();
  }
  async function readRootJob(id) {
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(String(id))) throw new Error('任务编号无效，请刷新助手状态。');
    stopRootPolling(); const jobEpoch = ++rootJobEpoch;
    const data = await request('root/jobs/' + encodeURIComponent(id));
    if (jobEpoch !== rootJobEpoch) return;
    renderRootJob(data);
    if (!finalStates.has(rootJob.state)) {
      const epoch = authEpoch;
      rootPoll = setTimeout(async () => {
        rootPoll = null;
        if (epoch !== authEpoch || !token) return;
        try { await readRootJob(id); }
        catch (error) { if (error.name !== 'AbortError' && token) message('任务状态读取失败，请点击“刷新任务状态”重试。', true); }
      }, 1500);
    }
  }
  task('refresh-root-status', 'click', readRootStatus);
  task('refresh-root-job', 'click', async () => { if (rootJob) await readRootJob(rootJob.id); });
  el('root-action').addEventListener('change', clearRootPlan);
  el('root-confirmation').addEventListener('input', updateRootControls);
  task('root-plan', 'click', async () => {
    if (rootPlanBusy || rootExecuteBusy || rootBusy() || !helper?.available) return;
    if (el('root-action').value === 'configure' && (settingsKey(settingsInput()) !== savedSettingsKey || !el('https').checked)) throw new Error('请先保存有效的 HTTPS 设置。');
    clearRootPlan(); const epoch = rootPlanEpoch, revision = settingsRevision;
    rootPlanBusy = true; updateRootControls();
    try {
      const action = el('root-action').value, plan = await request('root/plan', { action });
      if (epoch !== rootPlanEpoch || revision !== settingsRevision) return;
      if (!plan?.id || typeof plan.digest !== 'string' || typeof plan.confirmation !== 'string' || !plan.confirmation ||
          !Number.isFinite(timestamp(plan.expires_at)) || timestamp(plan.expires_at) <= Date.now() || !Object.hasOwn(actions, plan.action)) throw new Error('助手返回的预览无效或已过期，请重新生成。');
      rootPlan = plan; el('root-plan-summary').replaceChildren();
      summaryRow('操作', actions[plan.action]); summaryRow('目标入口', endpointText(plan.target));
      summaryRow('根 CA', plan.ca_preserved === true || plan.ca?.preserved === true ? '保留现有根 CA' : '以本次预览的 Root 检查结果为准；网页不执行根 CA 轮换');
      if (plan.backup_id) summaryRow('备份编号', shortText(plan.backup_id, 100));
      summaryRow('执行前备份', plan.backup_id ? '使用本次操作备份' : '由助手在执行阶段创建');
      renderStages('root-plan-stages', plan.stages); el('root-confirmation-text').textContent = plan.confirmation;
      el('root-plan-panel').hidden = false; planTimer = setInterval(updateRootControls, 1000); updateRootControls();
    } finally { if (epoch === rootPlanEpoch) rootPlanBusy = false; updateRootControls(); }
  });
  task('root-execute-form', 'submit', async () => {
    if (!syncAdminSession() || !rootPlan || rootExecuteBusy || rootBusy() || !helper?.available || settingsKey(settingsInput()) !== savedSettingsKey ||
        timestamp(rootPlan.expires_at) <= Date.now() || el('root-confirmation').value !== rootPlan.confirmation) throw new Error('预览已过期、设置已变更或确认文字不匹配，请重新核对。');
    const plan = rootPlan; rootExecuteBusy = true; rootStatusEpoch++; rootJobEpoch++; stopRootPolling(); updateRootControls();
    try {
      const data = await request('root/execute', { plan_id: plan.id, digest: plan.digest, confirmation: el('root-confirmation').value });
      clearRootPlan(); renderRootJob(data);
      await readRootJob(rootJob.id);
      message('Root 操作已提交。请查看各阶段状态；完成后可同步公共 CA，并手动验证入口。');
    } finally { rootExecuteBusy = false; updateRootControls(); }
  });
  task('sync-root-ca', 'click', async () => {
    if (!helper?.available || rootBusy()) return;
    await request('root/sync-ca', {}); await load(true); message('公共 CA 指纹已校验，公共证书与状态已同步。请在目标 Node 上独立核对信任指纹。');
  });
  task('copy-helper-install', 'click', async () => {
    if (!syncAdminSession() || !rootTool?.web_manager_install_command) return;
    try { await navigator.clipboard.writeText(rootTool.web_manager_install_command); message('首次初始化命令已复制。请在面板服务器通过 SSH 以 Root 执行，再刷新助手状态。'); }
    catch { message('复制失败，请手动复制上方完整的首次初始化命令。', true); }
  });
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
      finally { if (button) button.disabled = false; updateRootControls(); }
    });
  }
  task('login-form', 'submit', async () => {
    invalidateSession(); const epoch = authEpoch;
    const password = el('password').value; el('password').value = '';
    const res = await fetch('/api/v1/passport/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify({ email: el('email').value, password }), credentials: 'omit', redirect: 'error' });
    const data = await res.json();
    if (epoch !== authEpoch) return;
    if (!res.ok || !data.data?.auth_data) throw new Error(data.message || '登录失败；启用验证码时请使用管理员 Token 验证。');
    token = data.data.auth_data;
    if (!/^Bearer /i.test(token)) token = 'Bearer ' + token;
    authMode = 'manual'; pauseSso(false);
    try { await load(); message('管理员验证成功。'); } catch (error) { if (epoch === authEpoch) invalidateSession(); throw error; }
  });
  task('token-form', 'submit', async () => {
    invalidateSession(); const epoch = authEpoch;
    token = el('admin-token').value.trim(); el('admin-token').value = '';
    if (!/^Bearer /i.test(token)) token = 'Bearer ' + token;
    authMode = 'manual'; pauseSso(false);
    try { await load(); message('管理员验证成功。'); } catch (error) { if (epoch === authEpoch) invalidateSession(); throw error; }
  });
  task('use-admin-session', 'click', () => useAdminSession(true));
  el('logout').addEventListener('click', () => { pauseSso(true); invalidateSession(); el('session-status').textContent = '已退出本页；后台登录仍保留。点击上方按钮可再次登录本页。'; message('已退出本页会话。'); });
  window.addEventListener('storage', event => { if (event.key === null || event.key === ADMIN_KEY) syncAdminSession(); });
  window.addEventListener('focus', () => { if (syncAdminSession()) scheduleSessionExpiry(); });
  window.addEventListener('pagehide', () => { clearCaPassphrase(); stopRootPolling(); });
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
  ['input', 'change'].forEach(event => el('settings-form').addEventListener(event, () => { settingsRevision++; clearRootPlan(); refreshRootTool(); }));
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
    const revision = settingsRevision;
    const data = await request('settings', settingsInput());
    await load(settingsRevision !== revision); message(data.note);
  });
  el('copy-root-tool').addEventListener('click', async () => {
    if (!token || settingsKey(settingsInput()) !== savedSettingsKey || !rootTool?.command) return;
    el('copy-root-tool').disabled = true;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(rootTool.command);
      message('Root 工具命令已复制。请在面板服务器以 Root 执行，完成后再验证入口。');
    } catch {
      message('复制失败，请手工复制上方完整命令，在面板服务器以 Root 执行。', true);
    } finally { refreshRootTool(); }
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
  task('copy-command', 'click', async () => { if (!syncAdminSession() || !generated) throw new Error('请先生成命令。'); await navigator.clipboard.writeText(generated); message('命令已复制。'); });
  useAdminSession();
})();
