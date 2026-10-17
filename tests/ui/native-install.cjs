'use strict';
// Real audited Admin bundle, real native command/create controllers, synthetic fixture identities.
const {chromium}=require('playwright-core');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
(async()=>{
  const base=process.env.NE_UI_BASE || 'http://127.0.0.1:18744';
  if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base)) throw new Error('Loopback fixture required');
  const auth=JSON.parse(fs.readFileSync(process.env.NE_UI_AUTH ||
    path.join(__dirname,'../integration/work/native-choice-ui/fixture-auth.json'),'utf8'));
  const browser=await chromium.launch({channel:process.env.NE_BROWSER_CHANNEL || 'chrome',headless:true});
  const passed=[],errors=[];
  function record(label) {passed.push(label);console.log('PASS '+label); }
  try {
    const context=await browser.newContext({viewport:{width:1440,height:1000},permissions:['clipboard-read','clipboard-write']});
    await context.addInitScript(token=>{
      localStorage.setItem('XBOARD_ACCESS_TOKEN',JSON.stringify({value:'Bearer '+token,time:Date.now(),expire:null}));
      localStorage.setItem('i18nextLng','en-US');
    },auth.admin_token);
    const page=await context.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    let currentCommand=null,created=null;
    page.on('response',async response=>{
      const url=new URL(response.url());
      if (!/\/server\/machine\/(installCommand|save)$/.test(url.pathname) || response.status()!==200) return;
      const data=(await response.json()).data;
      if (url.pathname.endsWith('/installCommand')) currentCommand=data;
      else if (data?.install_command) created=data;
    });
    await page.route('**/api/v2/**',async route=>{
      const url=new URL(route.request().url());
      let data;
      if (url.pathname==='/api/v2/user/info') data={email:'fixture@invalid.test',is_admin:true,avatar_url:null};
      else if (url.pathname.endsWith('/plugin/getPlugins')) data=[];
      else if (url.pathname.endsWith('/server/machine/fetch')) data=[1,2].map(id=>({
        id,name:id===1?'Fixture machine':'Second choice fixture',notes:'Choice fixture',is_active:true,
        last_seen_at:null,servers_count:0,load_status:null,created_at:Math.floor(Date.now()/1000),updated_at:Math.floor(Date.now()/1000)
      }));
      else if (/\/server\/machine\/(history|nodes)$/.test(url.pathname)) data=[];
      else {await route.continue();return;}
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({data})});
    });
    const card=()=>page.locator('[data-ne-install="card"]');
    const preview=()=>card().locator('.ne-install-preview');
    const radio=mode=>card().locator('input[value="'+mode+'"]');
    async function open(name) {
      currentCommand=null;
      await page.getByRole('row').filter({hasText:name}).getByRole('button',{name:'Server Detail',exact:true}).first().click();
      await card().waitFor();
      await page.waitForFunction(()=>document.querySelectorAll('[data-ne-install="card"]').length===1);
    }
    async function close() {
      await page.keyboard.press('Escape');
      await card().waitFor({state:'detached'});
    }
    async function copyMatches(expected) {
      await card().locator('.ne-install-copy').click();
      const actual=await page.evaluate(()=>navigator.clipboard.readText());
      assert.ok(actual===expected,'Clipboard must equal selected command');
    }
    async function adminPost(url,data) {
      const r=await context.request.post(base+url,{headers:{Authorization:'Bearer '+auth.admin_token},data});
      assert.ok(r.ok(),'Fixture Admin action failed');
      return r.json();
    }
    await adminPost('/api/fixture/node-endpoint-state',{enabled:true});
    await adminPost('/api/v1/node-endpoint/settings',{enabled:true,host:'192.0.2.10',port:8443,https:true,ws_mode:'rewrite'});
    await page.goto(base+'/admin#/server/machine');
    await page.getByText('Fixture machine',{exact:true}).waitFor();
    await open('Fixture machine');
    assert.equal(await radio('domain').isChecked(),true);
    assert.equal(await radio('endpoint').isEnabled(),true);
    assert.equal(await preview().inputValue(),currentCommand.command);
    assert.ok(currentCommand.command.includes('ksr-v/Xboard-Independent/nodeendpoint-native-v1.0.0/node-installer/install.sh'));
    assert.ok(currentCommand.command.includes('https://old.panel.example'));
    assert.ok(!currentCommand.command.includes('cedar2025') && !currentCommand.command.includes('--endpoint-ca'));
    assert.equal(await card().getByText('域名对接（独立部署）',{exact:true}).count(),1);
    assert.equal(await page.locator('[data-ne-install-hidden="node_endpoint"]').count(),2);
    record('native details show two choices and hide both original copy controls');
    await copyMatches(currentCommand.install_options.domain.command);
    record('domain option copies independent deployment command using the existing website');
    await page.evaluate(()=>navigator.clipboard.writeText('CHOICE_FIXTURE_SENTINEL'));
    await radio('endpoint').check();
    assert.equal(await preview().inputValue(),currentCommand.install_options.endpoint.command);
    assert.ok((await preview().inputValue()).includes('https://192.0.2.10:8443'));
    assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'CHOICE_FIXTURE_SENTINEL');
    await copyMatches(currentCommand.install_options.endpoint.command);
    record('independent IP plus public CA copies exactly selected command without auto-copy on switch');
    await radio('domain').check(); await copyMatches(currentCommand.command);
    record('switching back copies domain rather than stale independent command');
    await page.evaluate(()=>{
      const real=Date.now; Date.now=()=>real()+601000;
      document.body.append(document.createElement('span'));
      window.__choiceFixtureRealDate=real;
    });
    await page.waitForTimeout(100);
    assert.equal(await card().count(),1);
    await page.evaluate(()=>{Date.now=window.__choiceFixtureRealDate;delete window.__choiceFixtureRealDate;});
    record('open dialog retains both choices after idle-cache expiry');
    await page.evaluate(()=>navigator.clipboard.writeText('CHOICE_FIXTURE_SENTINEL'));
    await close();
    assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'CHOICE_FIXTURE_SENTINEL');
    await open('Second choice fixture');
    assert.ok(/['"]--machine-id['"] ['"]2['"]/.test(currentCommand.command));
    await radio('endpoint').check(); await copyMatches(currentCommand.install_options.endpoint.command);
    record('closing never copies and switching machines cannot reuse earlier identity');
    const fetchData=await page.evaluate(async token=>{
      const r=await fetch('/api/v2/admin/server/machine/installCommand?id=2',{headers:{Authorization:'Bearer '+token}});
      return (await r.json()).data;
    },auth.admin_token);
    assert.ok(fetchData.command===currentCommand.command);
    await copyMatches(fetchData.install_options.endpoint.command);
    record('fetch observation preserves native response and selected-mode clipboard');
    await close();
    await open('Fixture machine');
    assert.equal(await radio('endpoint').isEnabled(),true);
    await radio('endpoint').check();
    assert.ok((await preview().inputValue()).includes('fixture-machine-token'));
    await close();
    await open('Second choice fixture');
    assert.equal(await radio('endpoint').isChecked(),true);
    record('reopening cached native details retains choices and the correct selected identity');
    await close();
    await page.getByRole('button',{name:'Add Server',exact:true}).click();
    await page.getByRole('dialog').getByLabel('Server Name',{exact:true}).fill('Choice new');
    await page.getByRole('dialog').getByLabel('Server Name',{exact:true}).press('Tab');
    await page.getByRole('dialog').getByRole('button',{name:'Submit',exact:true}).click();
    try {await card().waitFor({timeout:8000});} catch (error) {
      console.log(JSON.stringify({newMachineDiagnostic:{
        created:!!created,options:!!created?.install_options,owner:created?.install_options?.owner,
        shapes:await page.locator('code').evaluateAll(nodes=>nodes.map(el=>({
          curl:el.textContent.startsWith('curl'),length:el.textContent.length,
          parent:el.parentElement.className,children:[...el.parentElement.children].map(n=>n.tagName)
        }))),runtime:await page.evaluate(()=>!!window.__NODE_ENDPOINT_INSTALL_UI__),
        dialogs:await page.getByRole('dialog').count()
      }}));
      throw error;
    }
    assert.ok(created?.id>2);
    assert.equal(await preview().inputValue(),created.install_command);
    assert.ok(!created.install_command.includes('cedar2025') && !created.install_command.includes('--endpoint-ca'));
    await copyMatches(created.install_options.domain.command);
    assert.equal(await page.getByText(created.token,{exact:true}).count(),1);
    await radio('endpoint').check(); await copyMatches(created.install_options.endpoint.command);
    assert.ok((await preview().inputValue()).includes("'--machine-id' '"+created.id+"'") ||
      (await preview().inputValue()).includes('"--machine-id" "'+created.id+'"'));
    record('native new-machine popup offers both modes while keeping Token controls intact');
    await page.setViewportSize({width:390,height:844});
    assert.equal(await card().isVisible(),true);
    assert.equal(await radio('endpoint').isChecked(),true);
    assert.ok(await card().evaluate(el=>el.scrollWidth<=el.clientWidth+1),'Mobile card overflows');
    const output=path.join(__dirname,'work');fs.mkdirSync(output,{recursive:true});
    await page.screenshot({path:path.join(output,'native-install-mobile.png'),fullPage:true,animations:'disabled',
      mask:[page.locator('textarea'),page.locator('code')]});
    record('mobile native popup fits width with scoped styles');
    await close(); await page.setViewportSize({width:1440,height:1000});
    await adminPost('/api/v1/node-endpoint/settings',{enabled:false,host:'192.0.2.10',port:8443,https:true,ws_mode:'rewrite'});
    // The native app caches command queries; refresh after changing settings.
    await page.reload();
    await page.getByText('Fixture machine',{exact:true}).waitFor();
    await open('Fixture machine');
    assert.equal(await radio('endpoint').isDisabled(),true);
    assert.equal(await radio('domain').isChecked(),true);
    assert.equal(await card().locator('.ne-install-unavailable').isVisible(),true);
    await copyMatches(currentCommand.command);
    record('unready independent entry disables only IP choice and preserves domain copying');
    await page.evaluate(()=>fetch('/api/v2/admin/server/machine/installCommand?id=1'));
    await card().waitFor({state:'detached'});
    await page.evaluate(token=>fetch('/api/v2/admin/server/machine/installCommand?id=1',{
      headers:{Authorization:'Bearer '+token}}),auth.admin_token);
    await card().waitFor();
    const priorUrl=await page.evaluate(()=>{
      const previous=location.href;
      history.replaceState(null,'','/admin#/sign-in');
      document.body.append(document.createElement('span'));
      return previous;
    });
    await card().waitFor({state:'detached'});
    await page.evaluate(url=>history.replaceState(null,'',url),priorUrl);
    await page.evaluate(token=>fetch('/api/v2/admin/server/machine/installCommand?id=1',{
      headers:{Authorization:'Bearer '+token}}),auth.admin_token);
    await card().waitFor();
    record('authentication rejection and SPA sign-in navigation clear cached commands safely');
    await adminPost('/api/fixture/node-endpoint-state',{enabled:false});
    await card().waitFor({state:'detached',timeout:15000});
    assert.equal(await page.locator('[data-ne-install-hidden]').count(),0);
    const nativeButtons=await page.getByRole('button',{name:'Copy install command',exact:true}).all();
    assert.ok((await Promise.all(nativeButtons.map(button=>button.isVisible()))).some(Boolean),'Original copy control must be visible');
    record('disabling plugin clears choice commands and restores original native controls');
    assert.deepEqual(errors,[]);
    record('real Admin bundle has no browser runtime errors');
    await adminPost('/api/fixture/node-endpoint-state',{enabled:true});
    console.log(JSON.stringify({result:'PASS',checks:passed.length,passed,scope:'Real audited Admin browser UI and native command/create APIs; list/history/userinfo mocked; loopback synthetic fixture'}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
