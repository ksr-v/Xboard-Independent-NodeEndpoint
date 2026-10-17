# Native component integration

NodeEndpoint 1.0.3: Windows and Linux each pass 36 component checks, including independent domain-source generation, original URL/fallback, and shell-sensitive credentials; the 14 Chrome choice checks now verify both domain and IP commands use the independent installer. See [NATIVE_DOMAIN_TEST_REPORT.md](../../NATIVE_DOMAIN_TEST_REPORT.md). The 1.0.2 baseline below remains historical.

2026-10-17, NodeEndpoint 1.0.2: Windows and Linux each pass 34 native component checks and 6 dynamic Admin view/resource checks. The actual audited Admin build passes 14 Chrome checks for domain/IP choices, clipboard accuracy, native query cache, creation, mobile layouts, authentication and disabling. See [NATIVE_INSTALL_CHOICES_TEST_REPORT.md](../../NATIVE_INSTALL_CHOICES_TEST_REPORT.md) for mocks and scope.

```powershell
php -c .runtime/php.ini tests/integration/run.php
php -c .runtime/php.ini tests/integration/native_views.php
```

To reproduce the native installation-choice browser run, first prepare a synthetic fixture (only a direct child of this directory's work/ is accepted), then keep the loopback server running:

```powershell
$env:NE_FIXTURE_PATH = 'D:/xb-node-endpoint/tests/integration/work/native-choice-ui'
php -c .runtime/php.ini tests/integration/native_ui_setup.php
php -c .runtime/php.ini -S 127.0.0.1:18744 tests/integration/serve.php
```

In another terminal with the locked tests/ui dependencies and Chrome installed:

```powershell
node tests/ui/native-install.cjs
```

Use a local PHP configuration with the required extensions on Linux. NE_UI_BASE and NE_UI_AUTH can override the loopback base and synthetic fixture-auth path. The browser uses real native command/create controllers; list/history/user display data are mocked. It does not install a Node or contact a production server.

The runner loads unmodified Xboard `PluginManager`, `AbstractPlugin`, `InitializePlugins`, `Admin`, `ServerV2`, `ServerController`, models and exception handler from the audited `.audit/Xboard` checkout. It creates an isolated SQLite database and copies plugin packages/Admin assets to a new directory under `work/`. Real Sanctum personal access tokens exercise the native Admin guard. A fixture helper supplies `admin_setting` values; it does not access real panel settings or production databases.

Install the locked Laravel/Sanctum dependencies here with Composer, then run from the project root:

```powershell
php -c .runtime/php.ini tests/integration/run.php
```

PHP needs openssl, mbstring, fileinfo, pdo_sqlite and curl. The project-local `.runtime/php.ini` is machine-specific and does not change system configuration. On another machine, use a suitable local PHP configuration. Each run retains its new `work/` fixture for diagnostics; it never edits `.audit` or deletes unrelated directories.

This suite verifies native component behavior. Separate Linux Nginx, Root CLI/FPM, permissions and Go/systemd results and their scope are recorded in [VM_TEST_REPORT.md](../../VM_TEST_REPORT.md). The component suite alone does not establish those deployment results or production compatibility.

Rerun on 2026-10-10: **26 PASS** on Windows PHP 8.2.33 and Ubuntu PHP 8.3.6, with Laravel 12.69.3 and Sanctum 4.3.3. Checks include native lifecycle, administrator authorization, scoped handshake, HTTP acknowledgement, CA verification, three-plugin coexistence, controlled purge and pinned-kernel protocol command constraints. The previous 2026-10-09 baseline was 25 PASS.

For a loopback-only UI fixture with the same native API and synthetic Sanctum tokens:

```powershell
$env:NE_FIXTURE_PATH = 'D:/xb-node-endpoint/tests/integration/work/ui-fixture'
php -c .runtime/php.ini -S 127.0.0.1:18743 tests/integration/serve.php
```

Open `http://127.0.0.1:18743/node-endpoint`; the fixture creates test-only credentials in `work/ui-fixture/fixture-auth.json`. This development server accepts loopback clients only and synchronizes the current plugin package into its own fixture, preserving its isolated SQLite settings. It is not a deployment script and must not be used on production.

With that server running, install the locked `tests/ui` dependencies and execute the browser runner from the project root:

```powershell
npm --prefix tests/ui ci
node tests/ui/run.cjs
```

The runner uses installed Chrome through Playwright Core and a synthetic administrator bearer token from the fixture. Final rerun on 2026-10-10: **9 PASS**, Chrome 154.0.8037.98, no page errors. Checks cover the login shell, memory-only bearer authentication, IPv6 previews, HTTP acknowledgement, node/machine commands without Token, Xray protocol-option constraints, desktop/mobile layout and logout on refresh. It writes screenshots and `result.json` under `tests/ui/work/`. Password login, CAPTCHA, production authentication and actual TLS/WS deployment are outside this browser suite.

After building the release ZIP, run `php -c .runtime/php.ini tests/integration/package.php` (optionally pass a ZIP path as the first argument). Six checks exercise the actual native upload/install/enable path, compare every imported member with the ZIP, verify page/API/scoped handshake and preserve all existing plugin rows and Admin assets. This is isolated native component testing, not a production browser upload or Linux deployment.
