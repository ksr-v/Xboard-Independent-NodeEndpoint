'use strict';
// Browser behavior only. API authorization/encryption use the real PHP suite.
const { chromium } = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '../../plugin/NodeEndpoint');
  const pin = 'a'.repeat(64), secret = 'S'.repeat(64);
  let reveals = 0, reject = false;
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: 'https://panel.fixture' });
  const page = await context.newPage(), errors = [], passed = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://panel.fixture/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/node-endpoint') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(root, 'resources/views/index.html')) });
    if (url.pathname.startsWith('/node-endpoint/assets/')) return route.fulfill({
      contentType: url.pathname.endsWith('/app') ? 'application/javascript' : 'text/css',
      body: fs.readFileSync(path.join(root, 'resources/assets', url.pathname.endsWith('/app') ? 'app.js' : 'style.css'))
    });
    if (url.pathname.endsWith('/settings')) return route.fulfill({ json: { data: {
      settings: { enabled: true, host: '192.0.2.10', port: 8443, https: true, http_acknowledged: false, ws_mode: 'rest' },
      ca: [{ fingerprint_sha256: pin }], ca_passphrases: [{ fingerprint_sha256: pin, available: true }], compatibility: [], note: ''
    } } });
    if (url.pathname.endsWith('/ca-passphrase/reveal')) {
      reveals++;
      assert.deepEqual(route.request().postDataJSON(), { fingerprint: pin });
      assert.equal(route.request().method(), 'POST');
      return route.fulfill({ status: reject ? 401 : 200, json: reject ? { message: 'expired synthetic session' } : { data: { fingerprint_sha256: pin, passphrase: secret } } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  try {
    await page.goto('https://panel.fixture/node-endpoint');
    await page.locator('summary').click();
    await page.locator('#admin-token').fill('synthetic-token');
    await page.locator('#token-form button').click();
    await page.locator('#workspace').waitFor({ state: 'visible' });
    assert.equal(reveals, 0); assert.equal(await page.locator('#ca-passphrase-value').inputValue(), '');
    passed.push('ordinary settings load never requests or displays a passphrase');
    await page.locator('#show-ca-passphrase').click();
    await page.waitForFunction(() => !document.getElementById('ca-passphrase-display').hidden);
    assert.equal(await page.locator('#ca-passphrase-value').inputValue(), secret);
    await page.locator('#copy-ca-passphrase').click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), secret);
    assert.deepEqual(await page.evaluate(() => [Object.keys(localStorage), Object.keys(sessionStorage)]), [[], []]);
    passed.push('explicit administrator show/copy uses only page memory');
    await page.locator('#hide-ca-passphrase').click();
    assert.equal(await page.locator('#ca-passphrase-value').inputValue(), '');
    passed.push('hide immediately clears secret and copy state');
    await page.clock.install();
    await page.locator('#show-ca-passphrase').click();
    await page.waitForFunction(() => !document.getElementById('ca-passphrase-display').hidden);
    await page.clock.fastForward(61000);
    assert.equal(await page.locator('#ca-passphrase-value').inputValue(), '');
    passed.push('sixty-second timeout clears the revealed passphrase');
    await page.locator('#show-ca-passphrase').click();
    await page.waitForFunction(() => !document.getElementById('ca-passphrase-display').hidden);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    passed.push('mobile certificate and passphrase controls fit viewport');
    await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
    assert.equal(await page.locator('#ca-passphrase-value').inputValue(), '');
    passed.push('leaving the page clears the secret');
    reject = true;
    await page.locator('#show-ca-passphrase').click();
    await page.locator('#login-panel').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#ca-passphrase-value').inputValue(), '');
    passed.push('expired administrator session clears secrets and hides workspace');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ result: 'PASS', checks: passed.length, tests: passed, scope: 'Headless Chrome; HTTPS origin and APIs mocked; real plugin HTML/JS/CSS' }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
