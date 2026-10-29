'use strict';
// Synthetic API fixtures exercise browser behavior only; no Root process, SSH or real deployment is used.
const { chromium } = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const plugin = path.resolve(__dirname, '../../plugin/NodeEndpoint');
  const origin = 'https://panel.synthetic.test', key = 'XBOARD_ACCESS_TOKEN';
  const fakeA = 'Bearer synthetic-admin-A', fakeB = 'Bearer synthetic-admin-B';
  const output = path.join(__dirname, 'work'); fs.mkdirSync(output, { recursive: true });
  const requests = [], errors = [], passed = [];
  let settings = { enabled: true, host: 'node.synthetic.test', port: 8443, https: true, http_acknowledged: false, ws_mode: 'rest' };
  let available = true, activeJob = null, lastJob = null, helperBusy = false, missingHistory = false, failJobRead = false, reject = 0, planTtl = 60000, plans = 0, executions = 0, dropExecutionResponse = false;
  const syntheticDurableJobs = new Set(), syntheticOwnerCache = new Set();
  const syntheticExecutedPlans = new Set();
  let jobStates = ['running', 'succeeded'], jobRead = 0, jobError = null;
  let holdPlan = false, releasePlan, planHeld;
  const stages = state => [{ name: 'preflight', label: '检查目标与权限', state: 'succeeded' }, { name: 'certificate', label: '检查服务器证书', state }, { name: 'reload', label: '重载入口服务', state: state === 'succeeded' ? 'succeeded' : 'pending' }];
  const settingsData = () => ({ settings: { ...settings }, ca: [], ca_passphrases: [], compatibility: [], note: 'Synthetic settings saved.', root_tool: {
    command: "printf 'synthetic manual SSH fallback'", target_url: `https://${settings.host}:${settings.port}`, note: 'Synthetic SSH fallback.',
    web_manager_install_command: "printf 'synthetic helper install'", web_manager_install_note: 'Synthetic helper initialization; no Root execution.'
  } });
  const browser = await chromium.launch({ channel: process.env.NE_BROWSER_CHANNEL || 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin });
  await context.route(origin + '/**', async route => {
    const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
    if (pathname === '/admin') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Synthetic administrator storage editor</title>' });
    if (pathname === '/node-endpoint') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(plugin, 'resources/views/index.html')) });
    if (pathname.startsWith('/node-endpoint/assets/')) return route.fulfill({ contentType: pathname.endsWith('/app') ? 'application/javascript' : 'text/css', body: fs.readFileSync(path.join(plugin, 'resources/assets', pathname.endsWith('/app') ? 'app.js' : 'style.css')) });
    requests.push({ path: pathname, method: request.method(), auth: request.headers().authorization, body: request.postData() ? request.postDataJSON() : undefined });
    if (reject) return route.fulfill({ status: reject, json: { message: 'Synthetic administrator access denied.' } });
    if (![fakeA, fakeB].includes(request.headers().authorization)) return route.fulfill({ status: 403, json: { message: 'Synthetic administrator credential required.' } });
    if (pathname.endsWith('/settings')) {
      if (request.method() === 'POST') settings = request.postDataJSON();
      return route.fulfill({ json: { data: settingsData() } });
    }
    if (pathname.endsWith('/root/status')) return route.fulfill({ json: { data: {
      available, version: 'synthetic-1.1.0', policy: { app: '/private/synthetic-app', app_user: 'synthetic', allowed_ports: [8443, 9443] },
      root_status: { private_metadata: 'SYNTHETIC_PRIVATE_SENTINEL', socket: '/run/synthetic-secret.sock' },
      ca: { pem: 'SYNTHETIC_PEM_SENTINEL', fingerprints: ['a'.repeat(64)] }, active_job: activeJob, last_job: lastJob, busy: helperBusy
    } } });
    if (pathname.endsWith('/root/plan')) {
      assert.deepEqual(Object.keys(request.postDataJSON()), ['action']);
      const action = request.postDataJSON().action, id = 'synthetic-plan-' + (++plans);
      const data = { id, digest: 'd'.repeat(64), action, expires_at: new Date(Date.now() + planTtl).toISOString(), confirmation: 'CONFIRM SYNTHETIC ' + id, target: { host: settings.host, port: settings.port, https: settings.https }, ca_preserved: true, backup_id: 'synthetic-backup', stages: stages('planned') };
      if (holdPlan) { holdPlan = false; await new Promise(resolve => { releasePlan = resolve; planHeld(); }); }
      return route.fulfill({ json: { data } });
    }
    if (pathname.endsWith('/root/execute')) {
      executions++; const body = request.postDataJSON();
      assert.deepEqual(Object.keys(body).sort(), ['confirmation', 'digest', 'plan_id']);
      assert.equal(body.confirmation, 'CONFIRM SYNTHETIC ' + body.plan_id); assert.equal(body.digest, 'd'.repeat(64));
      syntheticExecutedPlans.add(body.plan_id); activeJob = 'synthetic-job'; lastJob = activeJob;
      syntheticDurableJobs.add(activeJob); syntheticOwnerCache.add(activeJob); jobRead = 0;
      if (dropExecutionResponse) { dropExecutionResponse = false; return route.abort('timedout'); }
      return route.fulfill({ json: { data: { job: { id: activeJob, action: 'configure', state: 'queued', stages: stages('pending') } } } });
    }
    if (pathname.includes('/root/jobs/')) {
      if (missingHistory) { lastJob = null; return route.fulfill({ status: 404, json: { message: 'Synthetic history was pruned.', error: { code: 'not_found' } } }); }
      if (failJobRead) return route.fulfill({ status: 503, json: { message: 'Synthetic job status temporarily unreadable.' } });
      assert.equal(syntheticDurableJobs.has(pathname.split('/').at(-1)), true, 'Synthetic persistent owner association must permit job recovery independently of the cache.');
      const state = jobStates[Math.min(jobRead++, jobStates.length - 1)];
      return route.fulfill({ json: { data: { id: 'synthetic-job', action: 'configure', state, result: { backup_id: 'synthetic-backup', ca_preserved: true }, stages: stages(state), error: jobError } } });
    }
    if (pathname.endsWith('/root/sync-ca')) { assert.deepEqual(request.postDataJSON(), {}); return route.fulfill({ json: { data: { synced: true } } }); }
    if (pathname.endsWith('/probe')) return route.fulfill({ json: { data: { synthetic: true, tls: 'NOT A REAL ROOT TEST' } } });
    return route.fulfill({ status: 404, json: { message: 'Unknown synthetic API.' } });
  });
  const editor = await context.newPage(), page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  const store = async (value, expires = Date.now() + 3600000) => editor.evaluate(([name, token, expire]) => localStorage.setItem(name, JSON.stringify({ value: token, time: Date.now(), expire })), [key, value, expires]);
  const login = async () => { await page.locator('#use-admin-session').click(); await page.locator('#workspace').waitFor({ state: 'visible' }); await page.waitForFunction(() => !document.getElementById('root-plan').disabled); };
  const save = async () => { await page.locator('#settings-form .actions button').first().click(); await page.waitForFunction(() => !document.querySelector('#settings-form .actions button').disabled); };
  const plan = async () => { await page.locator('#root-plan').click(); await page.locator('#root-plan-panel').waitFor({ state: 'visible' }); };
  const confirm = async () => page.locator('#root-confirmation').fill(await page.locator('#root-confirmation-text').textContent());
  try {
    await editor.goto(origin + '/admin'); await store(fakeA); const stored = await editor.evaluate(name => localStorage.getItem(name), key);
    await page.goto(origin + '/node-endpoint'); await page.locator('#workspace').waitFor({ state: 'visible' }); await page.waitForFunction(() => !document.getElementById('root-plan').disabled);
    assert.equal(requests.some(request => request.path.includes('/passport/')), false);
    assert.equal(requests.find(request => request.path.endsWith('/settings')).auth, fakeA);
    assert.equal(await editor.evaluate(name => localStorage.getItem(name), key), stored);
    assert.deepEqual(await page.evaluate(() => Object.keys(sessionStorage)), []);
    assert.equal(plans, 0); assert.equal(executions, 0);
    assert.equal((await page.locator('body').textContent()).includes('SYNTHETIC_PRIVATE_SENTINEL'), false);
    assert.equal((await page.locator('body').textContent()).includes('SYNTHETIC_PEM_SENTINEL'), false);
    passed.push('same-origin SSO validates through the administrator API without storing another credential, requesting passwords or executing Root');

    await page.locator('#logout').click(); await page.locator('#login-panel').waitFor({ state: 'visible' });
    assert.equal(await editor.evaluate(name => localStorage.getItem(name), key), stored);
    const loadsBefore = requests.filter(request => request.path.endsWith('/settings')).length;
    await page.reload(); assert.equal(await page.locator('#workspace').isVisible(), false);
    assert.equal(requests.filter(request => request.path.endsWith('/settings')).length, loadsBefore);
    await login(); assert.deepEqual(await page.evaluate(() => Object.keys(sessionStorage)), []);
    passed.push('exit applies only to this page, suppresses automatic relogin across refresh and supports explicit SSO restoration');

    await store(fakeB); await page.locator('#login-panel').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#root-job-panel').isVisible(), false); await login();
    assert.equal(requests.filter(request => request.path.endsWith('/settings')).at(-1).auth, fakeB);
    await editor.evaluate(name => localStorage.removeItem(name), key); await page.locator('#login-panel').waitFor({ state: 'visible' });
    await store(fakeA); await login(); await editor.evaluate(() => localStorage.clear()); await page.locator('#login-panel').waitFor({ state: 'visible' });
    passed.push('cross-account replacement, storage deletion and storage clear invalidate the independent page instead of silently switching accounts');

    await store(fakeA, Date.now() - 1000); const beforeExpired = requests.length;
    await page.reload(); assert.equal(await page.locator('#workspace').isVisible(), false); assert.equal(requests.length, beforeExpired);
    await store(fakeA); await login();
    await page.evaluate(name => { const data = JSON.parse(localStorage.getItem(name)); data.expire = Date.now() - 1; localStorage.setItem(name, JSON.stringify(data)); window.dispatchEvent(new Event('focus')); }, key);
    await page.locator('#login-panel').waitFor({ state: 'visible' });
    await store(fakeA); await login();
    const probes = requests.filter(request => request.path.endsWith('/probe')).length;
    await page.evaluate(name => { const data = JSON.parse(localStorage.getItem(name)); data.expire = Date.now() - 1; localStorage.setItem(name, JSON.stringify(data)); document.getElementById('probe').click(); }, key);
    await page.locator('#login-panel').waitFor({ state: 'visible' });
    assert.equal(requests.filter(request => request.path.endsWith('/probe')).length, probes);
    await store(fakeA); await login(); reject = 403; await page.locator('#refresh-root-status').click(); await page.locator('#login-panel').waitFor({ state: 'visible' });
    assert.notEqual(await editor.evaluate(name => localStorage.getItem(name), key), null); reject = 0; await login();
    passed.push('expired stored credentials are rejected at startup, focus and before requests; server 403 clears this page without deleting primary login');

    await plan(); assert.equal(await page.locator('#root-execute').isDisabled(), true);
    assert.match(await page.locator('#root-plan-summary').textContent(), /node\.synthetic\.test:8443/);
    assert.match(await page.locator('#root-plan-summary').textContent(), /保留现有根 CA/);
    await page.locator('#root-confirmation').fill('CONFIRM'); assert.equal(await page.locator('#root-execute').isDisabled(), true);
    await page.locator('#root-execute-form').evaluate(form => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    assert.equal(executions, 0); await confirm(); assert.equal(await page.locator('#root-execute').isDisabled(), false);
    await page.locator('#host').fill('edited.synthetic.test'); assert.equal(await page.locator('#root-plan-panel').isVisible(), false); assert.equal(await page.locator('#root-plan').isDisabled(), true);
    await save(); await plan(); await confirm();
    passed.push('Root requires an explicit plan and exact typed confirmation; settings edits discard the preview and prevent stale-target execution');

    await page.clock.install(); planTtl = 2000; await plan(); await confirm(); await page.clock.fastForward(3000);
    assert.equal(await page.locator('#root-execute').isDisabled(), true); assert.match(await page.locator('#root-plan-expiry').textContent(), /已过期/); assert.equal(executions, 0);
    planTtl = 60000; await plan(); await confirm();
    const beforeExecute = executions, uniqueBefore = syntheticExecutedPlans.size;
    dropExecutionResponse = true; await page.locator('#root-execute').click();
    await page.waitForFunction(() => !document.getElementById('root-execute').disabled);
    assert.equal(await page.locator('#root-plan-panel').isVisible(), true);
    const retryConfirmation = await page.locator('#root-confirmation').inputValue();
    assert.equal(retryConfirmation, await page.locator('#root-confirmation-text').textContent());
    await page.locator('#root-execute').click(); await page.locator('#root-job-panel').waitFor({ state: 'visible' });
    assert.equal(executions, beforeExecute + 2); assert.equal(syntheticExecutedPlans.size, uniqueBefore + 1);
    assert.deepEqual(requests.filter(request => request.path.endsWith('/root/execute')).at(-1).body, requests.filter(request => request.path.endsWith('/root/execute')).at(-2).body);
    assert.equal(await page.locator('#root-plan-panel').isVisible(), false);
    assert.equal(await page.locator('#root-plan').isDisabled(), true);
    await page.clock.fastForward(2000); await page.waitForFunction(() => document.getElementById('root-job-state').textContent.includes('成功'));
    assert.match(await page.locator('#root-job-backup').textContent(), /synthetic-backup/); assert.equal(await page.locator('#root-plan').isDisabled(), false);
    passed.push('expired plans cannot execute; uncertain execute responses preserve the same confirmed plan for retry, then display running/success stages and backup identification');

    for (const state of ['partial', 'failed', 'interrupted']) {
      jobStates = [state]; jobRead = 0; jobError = state === 'failed' ? { message: 'private socket /run/synthetic-secret.sock' } : { message: '合成测试：操作中断，请核对入口状态。' };
      await page.locator('#refresh-root-job').click();
      await page.waitForFunction(expected => document.getElementById('root-job-state').textContent.includes(expected), { partial: '部分完成', failed: '失败', interrupted: '已中断' }[state]);
      assert.match(await page.locator('#root-job-state').textContent(), new RegExp({ partial: '部分完成', failed: '失败', interrupted: '已中断' }[state]));
      assert.equal(await page.locator('#root-plan').isDisabled(), false);
      assert.equal((await page.locator('#root-job-error').textContent()).includes('/run/'), false);
    }
    const readsAtTerminal = jobRead; await page.clock.fastForward(5000); assert.equal(jobRead, readsAtTerminal);
    activeJob = null; helperBusy = false; syntheticOwnerCache.clear();
    await page.reload(); await page.locator('#root-job-panel').waitFor({ state: 'visible' }); assert.match(await page.locator('#root-job-state').textContent(), /已中断/);
    assert.equal(syntheticOwnerCache.size, 0); assert.equal(await page.locator('#root-plan').isDisabled(), false);
    helperBusy = true; await page.locator('#refresh-root-status').click(); await page.waitForFunction(() => document.getElementById('root-plan').disabled);
    helperBusy = false; await page.locator('#refresh-root-status').click(); await page.waitForFunction(() => !document.getElementById('root-plan').disabled);
    passed.push('terminal jobs stop polling and show safe errors; after Root restart and synthetic owner-cache clearing, last_job restores the interrupted task without treating history as busy');

    failJobRead = true; await page.locator('#refresh-root-status').click(); await page.waitForFunction(() => document.getElementById('notice').textContent.includes('任务状态暂时无法读取'));
    assert.equal(await page.locator('#root-helper-controls').isVisible(), true); assert.equal(await page.locator('#root-helper-unavailable').isVisible(), false);
    failJobRead = false; missingHistory = true; await page.locator('#refresh-root-status').click();
    await page.waitForFunction(() => document.getElementById('notice').textContent.includes('该任务记录不可用，可重新预览'));
    assert.equal(await page.locator('#root-job-panel').isVisible(), false); assert.equal(await page.locator('#root-helper-controls').isVisible(), true);
    assert.equal(await page.locator('#root-helper-unavailable').isVisible(), false); assert.equal(await page.locator('#root-plan').isDisabled(), false);
    missingHistory = false; await plan(); assert.equal(await page.locator('#root-plan-panel').isVisible(), true);
    passed.push('nested job 503 and pruned-history 404 preserve a connected helper; missing task display clears and a fresh plan remains available');

    const syncs = requests.filter(request => request.path.endsWith('/root/sync-ca')).length;
    await page.locator('#sync-root-ca').click(); await page.waitForFunction(() => document.getElementById('notice').textContent.includes('公共 CA 指纹已校验'));
    assert.equal(requests.filter(request => request.path.endsWith('/root/sync-ca')).length, syncs + 1);
    available = false; activeJob = null; lastJob = null; await page.locator('#refresh-root-status').click(); await page.locator('#root-helper-unavailable').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#root-helper-controls').isVisible(), false);
    await page.locator('#copy-helper-install').click(); assert.equal(await page.evaluate(() => navigator.clipboard.readText()), "printf 'synthetic helper install'");
    passed.push('CA synchronization is explicit and unavailable helpers expose the separate first-initialization SSH command');

    available = true; await page.locator('#refresh-root-status').click(); await page.waitForFunction(() => !document.getElementById('root-plan').disabled);
    holdPlan = true; const arrived = new Promise(resolve => { planHeld = resolve; }); await page.locator('#root-plan').click(); await arrived;
    await store(fakeB); await page.locator('#login-panel').waitFor({ state: 'visible' }); releasePlan();
    await page.waitForTimeout(100); assert.equal(await page.locator('#root-plan-panel').isVisible(), false); await login();
    assert.equal(await page.locator('#root-plan-panel').isVisible(), false);
    passed.push('a plan response from a previous account cannot repopulate the next account workspace');

    await plan(); await page.screenshot({ path: path.join(output, 'session-root-desktop-synthetic.png'), fullPage: true });
    await page.locator('#root-manager').screenshot({ path: path.join(output, 'root-manager-desktop-synthetic.png') });
    await page.setViewportSize({ width: 390, height: 844 }); await page.screenshot({ path: path.join(output, 'session-root-mobile-synthetic.png'), fullPage: true });
    await page.locator('#root-manager').screenshot({ path: path.join(output, 'root-manager-mobile-synthetic.png') });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(errors, []);
    passed.push('desktop and mobile Root controls render without page errors or horizontal overflow');
    console.log(JSON.stringify({ result: 'PASS', synthetic: true, checks: passed.length, tests: passed, scope: 'Headless Chrome; real plugin HTML/JS/CSS; synthetic same-origin administrator and Root APIs; no actual Root execution' }));
  } finally { releasePlan?.(); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
