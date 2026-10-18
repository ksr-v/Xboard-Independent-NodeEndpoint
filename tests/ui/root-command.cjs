'use strict';
// Browser behavior only. The PHP suite checks command generation and authorization.
const { chromium } = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '../../plugin/NodeEndpoint');
  const installer = 'https://raw.githubusercontent.com/ksr-v/Xboard-Independent-NodeEndpoint/root-installer-v1.0.6/install.sh';
  let settings = { enabled: true, host: '8.8.8.8', port: 8443, https: true, http_acknowledged: false, ws_mode: 'rest' };
  let failSave = false, expired = false, saves = 0, loads = 0;
  let holdPost = false, holdGet = false, releasePost, releaseGet, postHeld, getHeld;
  const errors = [], passed = [];
  const commandFor = host => `curl -fsSL ${installer} | bash -s -- --host '${host}'`;
  const data = () => ({
    settings: { ...settings }, ca: [], ca_passphrases: [], compatibility: [], note: 'Saved fixture settings.',
    root_tool: {
      command: settings.https ? commandFor(settings.host) : null,
      target_url: `${settings.https ? 'https' : 'http'}://${settings.host}:${settings.port}`,
      note: settings.https ? 'Root 用户通过 SSH 执行此命令，再验证已保存入口。' : 'HTTP 入口不可使用此 Root 工具命令，请先启用 HTTPS。'
    }
  });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: 'https://panel.fixture' });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://panel.fixture/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/node-endpoint') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(root, 'resources/views/index.html')) });
    if (url.pathname.startsWith('/node-endpoint/assets/')) return route.fulfill({
      contentType: url.pathname.endsWith('/app') ? 'application/javascript' : 'text/css',
      body: fs.readFileSync(path.join(root, 'resources/assets', url.pathname.endsWith('/app') ? 'app.js' : 'style.css'))
    });
    if (expired) return route.fulfill({ status: 401, json: { message: 'Expired fixture session.' } });
    if (url.pathname.endsWith('/settings')) {
      if (route.request().method() === 'POST') {
        saves++;
        if (failSave) return route.fulfill({ status: 422, json: { message: 'Fixture save rejected.' } });
        const submitted = route.request().postDataJSON();
        assert.deepEqual(Object.keys(submitted).sort(), Object.keys(settings).sort());
        if (holdPost) {
          holdPost = false;
          await new Promise(resolve => { releasePost = resolve; postHeld(); });
        }
        settings = { ...submitted };
      } else {
        loads++;
        if (holdGet) {
          holdGet = false;
          await new Promise(resolve => { releaseGet = resolve; getHeld(); });
        }
      }
      return route.fulfill({ json: { data: data() } });
    }
    return route.fulfill({ status: 404, json: { message: 'Unknown fixture API.' } });
  });
  const copyButton = page.locator('#copy-root-tool');
  const command = page.locator('#root-tool-command');
  const save = () => page.locator('#settings-form button').first().click();
  const waitCommand = expected => page.waitForFunction(value => {
    const result = document.getElementById('root-tool-command');
    const copy = document.getElementById('copy-root-tool');
    return result?.textContent === value && copy && !copy.disabled;
  }, expected);
  try {
    await page.goto('https://panel.fixture/node-endpoint');
    await page.locator('summary').click();
    await page.locator('#admin-token').fill('synthetic-token');
    await page.locator('#token-form button').click();
    await page.locator('#workspace').waitFor({ state: 'visible' });
    await waitCommand(commandFor('8.8.8.8'));
    assert.match(await page.locator('#root-tool-note').textContent(), /Root.*SSH/);
    assert.equal(await page.locator('#root-tool-target').textContent(), 'https://8.8.8.8:8443');
    await copyButton.click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), commandFor('8.8.8.8'));
    passed.push('authorized load displays and copies the saved endpoint command using the real clipboard');

    await page.locator('#host').fill('9.9.9.9');
    assert.equal(await copyButton.isDisabled(), true);
    assert.equal((await command.textContent()).includes('curl '), false);
    assert.equal((await command.textContent()).includes('8.8.8.8'), false);
    passed.push('editing the host disables copying and clears the stale executable command');

    const beforeSaveLoads = loads;
    await save();
    await waitCommand(commandFor('9.9.9.9'));
    assert.equal(loads, beforeSaveLoads + 1);
    assert.equal(settings.host, '9.9.9.9');
    assert.equal(await page.locator('#root-tool-target').textContent(), 'https://9.9.9.9:8443');
    await copyButton.click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), commandFor('9.9.9.9'));
    passed.push('successful save reloads authoritative settings and copies the full command for the new saved host');

    failSave = true;
    await page.locator('#host').fill('1.1.1.1');
    await save();
    await page.waitForFunction(() => document.getElementById('notice').textContent === 'Fixture save rejected.');
    assert.equal(await copyButton.isDisabled(), true);
    assert.equal((await command.textContent()).includes('curl '), false);
    assert.equal(settings.host, '9.9.9.9');
    passed.push('rejected save leaves copying disabled and never presents an uncommitted command');

    failSave = false;
    await page.locator('#https').uncheck();
    await page.locator('#http-ack').check();
    await save();
    await page.waitForFunction(() => document.getElementById('root-tool-note').textContent.includes('HTTP 入口不可'));
    assert.equal(await copyButton.isDisabled(), true);
    assert.equal((await command.textContent()).includes('curl '), false);
    passed.push('an unavailable HTTP command remains disabled and displays the server explanation');

    await page.locator('#https').check();
    await save();
    await waitCommand(commandFor('1.1.1.1'));
    await page.evaluate(() => {
      Object.defineProperty(navigator.clipboard, 'writeText', {
        configurable: true,
        value: async () => { throw new DOMException('Fixture clipboard permission denied.', 'NotAllowedError'); }
      });
    });
    await copyButton.click();
    await page.waitForFunction(() => document.getElementById('notice').classList.contains('error'));
    assert.match(await page.locator('#notice').textContent(), /复制|剪贴|手动/);
    assert.equal(await command.textContent(), commandFor('1.1.1.1'));
    assert.equal(await command.isVisible(), true);
    await page.evaluate(() => { delete navigator.clipboard.writeText; });
    passed.push('clipboard denial shows a visible failure and retains the command for manual copying');

    holdPost = true;
    const postArrived = new Promise(resolve => { postHeld = resolve; });
    await page.locator('#host').fill('4.4.4.4');
    await save();
    await postArrived;
    await page.locator('#host').fill('8.8.4.4');
    releasePost();
    await page.waitForFunction(() => !document.querySelector('#settings-form button').disabled);
    assert.equal(settings.host, '4.4.4.4');
    assert.equal(await page.locator('#host').inputValue(), '8.8.4.4');
    assert.equal(await copyButton.isDisabled(), true);
    assert.equal((await command.textContent()).includes('curl '), false);

    holdGet = true;
    const getArrived = new Promise(resolve => { getHeld = resolve; });
    await save();
    await getArrived;
    await page.locator('#host').fill('4.2.2.2');
    releaseGet();
    await page.waitForFunction(() => !document.querySelector('#settings-form button').disabled);
    assert.equal(settings.host, '8.8.4.4');
    assert.equal(await page.locator('#host').inputValue(), '4.2.2.2');
    assert.equal(await copyButton.isDisabled(), true);
    assert.equal((await command.textContent()).includes('curl '), false);
    await save();
    await waitCommand(commandFor('4.2.2.2'));
    passed.push('edits during both the save POST and refresh GET survive without re-enabling a stale command');

    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(await page.locator('#root-tool-panel').evaluate(element => element.scrollWidth > element.clientWidth), false);
    passed.push('the long Root command and controls fit a mobile viewport');

    expired = true;
    await page.locator('#probe').click();
    await page.locator('#login-panel').waitFor({ state: 'visible' });
    assert.equal(await copyButton.isDisabled(), true);
    assert.equal((await command.textContent()).includes('curl '), false);
    assert.equal((await command.textContent()).includes('4.2.2.2'), false);
    assert.equal(await page.locator('#workspace').isVisible(), false);
    passed.push('expired authorization clears the Root command and returns to login');

    assert.equal(saves, 7);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ result: 'PASS', checks: passed.length, tests: passed, scope: 'Headless Chrome; HTTPS origin and APIs mocked; real plugin HTML/JS/CSS and clipboard' }));
  } finally { releasePost?.(); releaseGet?.(); await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
