"""Isolated protocol/job/permission tests; no production service or network calls."""
import copy
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/panel'))
import web_manager as web


def policy():
    binding = {'app': '/srv/xboard', 'app_user': 'www', 'php': '/usr/bin/php', 'deployment': 'fpm',
               'backend': 'unix:/run/php/php-fpm.sock', 'source_site': '/etc/nginx/panel.conf',
               'site_name': 'panel.example', 'include_dir': '/etc/nginx/conf.d',
               'config_path': '/etc/nginx/conf.d/node-endpoint.conf',
               'main_config': '/etc/nginx/nginx.conf', 'ownership': 'a' * 32}
    return {'schema': 1, 'version': web.VERSION, 'tools_sha256': web.TOOLS_SHA256,
            'manifest_sha256': 'b' * 64, 'app': binding['app'], 'app_user': 'www', 'php': binding['php'],
            'app_uid': 1001, 'app_gid': 1001, 'nginx': '/usr/sbin/nginx',
            'state': '/var/lib/node-endpoint', 'binding': binding,
            'ws_backend': 'http://127.0.0.1:8080', 'allowed_ports': [8443, 9443]}


class MemoryStore:
    def __init__(self):
        self.values = {'plan': {}, 'job': {}}

    def save(self, kind, value):
        self.values[kind][value['id']] = copy.deepcopy(value)

    def load_all(self, kind):
        return copy.deepcopy(self.values[kind])

    def remove(self, kind, identity):
        del self.values[kind][identity]


class Adapter:
    def __init__(self):
        self.revision = 1
        self.calls = []
        self.fail = None
        self.firewall_status = 'ensured'
        self.mutated_firewall = False
        self.target = {'host': '198.18.1.1', 'port': 8443, 'https': True, 'ws_mode': 'rest'}

    def status(self):
        return {'root_status': {'schema_version': 1, 'version': '1.0.0', 'installed': True,
                                'endpoint': self.target, 'checks': {}},
                'ca': {'pem': 'public certificate', 'fingerprints': ['c' * 64]}}

    def prepare(self, action, parameters):
        self.calls.append('prepare')
        target = dict(self.target)
        for key in ('host', 'port', 'ws_mode'):
            if key in parameters:
                target[key] = parameters[key]
        return {'review': {'action': action, 'target': target, 'candidate': {'root_only': True},
                           'root_digest': str(self.revision), 'desired': None},
                'fingerprints': ['c' * 64], 'current': self.status()['root_status']}

    def apply_entry(self, saved):
        self.calls.append('entry')
        if self.fail == 'entry':
            raise RuntimeError('PRIVATE KEY password=never-expose stderr')
        return {'root_status': self.status()['root_status'], 'endpoint': saved['target'],
                'backup_id': '20261029T000000-1234abcd', 'ca_preserved': True}

    def sync_ca(self, saved):
        self.calls.append('ca_sync')
        if self.fail == 'ca_sync':
            raise RuntimeError('APP_KEY=never-expose')
        return {'status': 'unchanged', 'certificates': 1, 'fingerprints': ['c' * 64]}

    def firewall(self, saved):
        self.calls.append('firewall')
        if self.fail == 'firewall':
            raise RuntimeError('unrelated firewall policy must stay private')
        return {'status': self.firewall_status, 'port': 8443,
                'mutation_attempted': self.mutated_firewall,
                'stderr': 'unrelated firewall policy must stay private'}


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.store, self.adapter = MemoryStore(), Adapter()
        self.clock = 1000
        self.engine = web.Engine(policy(), self.adapter, self.store, start_worker=False, now=lambda: self.clock)

    def rpc(self, operation, parameters=None, uid=1001):
        return web.reply(self.engine, web.canonical({'schema': 1, 'operation': operation,
                                                   'parameters': parameters or {}}), uid)

    def make_job(self, action='refresh'):
        plan = self.rpc('plan', {'action': action})['data']
        submitted = {'plan_id': plan['id'], 'digest': plan['digest'], 'confirmation': plan['confirmation']}
        job = self.rpc('execute', submitted)['data']
        return plan, submitted, job

    def test_status_public_policy_and_peer_uid(self):
        result = self.rpc('status')['data']
        self.assertEqual(result['policy'], {'app': '/srv/xboard', 'app_user': 'www', 'allowed_ports': [8443, 9443]})
        self.assertEqual(result['root_status']['schema_version'], 1)
        self.assertEqual(result['active_job'], None)
        self.assertTrue(self.rpc('status', uid=0)['ok'])
        self.assertEqual(self.rpc('status', uid=1002)['error']['code'], 'unauthorized')
        self.assertNotIn('manifest', json.dumps(result))
        self.assertNotIn('binding', json.dumps(result))

    def test_strict_schema_unknown_fields_and_command_injection(self):
        bad = [b'{}', b'[]', b'{"schema":true,"operation":"status","parameters":{}}',
               b'{"schema":1,"schema":1,"operation":"status","parameters":{}}',
               b'{"schema":1,"operation":"status","parameters":{},"command":"id"}',
               b'{"schema":1,"operation":"status","parameters":{"state":"/etc"}}',
               b'{"schema":1,"operation":"shell","parameters":{}}',
               b'{"schema":1,"operation":"status","parameters":NaN}',
               b'{"schema":1,"operation":"status","parameters":null}',
               b'x' * (web.MAX_REQUEST + 1), b'\xff']
        for raw in bad:
            with self.subTest(raw=raw[:80]):
                self.assertEqual(web.reply(self.engine, raw, 1001)['error']['code'], 'invalid_request')
        for forbidden in ('state', 'nginx', 'php', 'app', 'backend', 'ca_key', 'container', 'shell', 'command'):
            result = self.rpc('plan', {'action': 'configure', forbidden: '/tmp/untrusted'})
            self.assertEqual(result['error']['code'], 'invalid_request')
        for action in ('export-ca', 'rotate-prepare', 'remove-online-ca-key', 'uninstall', 'rollback', 'id; reboot'):
            self.assertEqual(self.rpc('plan', {'action': action})['error']['code'], 'invalid_request')
        self.assertEqual(self.adapter.calls, [])

    def test_only_configure_accepts_target_fields(self):
        for action in ('refresh', 'renew', 'firewall'):
            self.assertEqual(self.rpc('plan', {'action': action, 'host': 'example.test'})['error']['code'], 'invalid_request')
        for changes in ({'port': True}, {'port': 0}, {'port': 65536}, {'host': 'x' * 254}, {'ws_mode': 'shell'}):
            self.assertFalse(self.rpc('plan', {'action': 'configure', **changes})['ok'])

    def test_plan_is_public_typed_time_limited_and_bound_to_digest(self):
        plan = self.rpc('plan', {'action': 'refresh'})['data']
        self.assertEqual(plan['confirmation'], 'REFRESH 198.18.1.1:8443')
        self.assertEqual(plan['target']['ws_mode'], 'rest')
        self.assertEqual(plan['current']['schema_version'], 1)
        self.assertTrue(plan['expires_at'].endswith('+00:00'))
        self.assertNotIn('review', plan)
        self.assertNotIn('fingerprints', plan)
        self.assertEqual(self.rpc('execute', {'plan_id': plan['id'], 'digest': '0' * 64,
                                            'confirmation': plan['confirmation']})['error']['code'], 'confirmation_mismatch')
        self.assertEqual(self.rpc('execute', {'plan_id': plan['id'], 'digest': plan['digest'],
                                            'confirmation': 'yes'})['error']['code'], 'confirmation_mismatch')
        self.clock += web.PLAN_TTL
        self.assertEqual(self.rpc('execute', {'plan_id': plan['id'], 'digest': plan['digest'],
                                            'confirmation': plan['confirmation']})['error']['code'], 'expired')
        self.assertFalse(self.store.values['job'])

    def test_duplicate_execute_is_same_durable_job_before_and_after_expiry(self):
        plan, parameters, job = self.make_job()
        self.assertEqual(job['id'], plan['id'])
        self.assertEqual(self.rpc('execute', parameters)['data'], job)
        self.assertEqual(self.engine.queue.qsize(), 1)
        self.engine.run(job['id'])
        self.clock += 10000
        repeated = self.rpc('execute', parameters)['data']
        self.assertEqual(repeated, {'id': job['id'], 'state': 'succeeded'})
        self.assertEqual(self.adapter.calls.count('entry'), 1)

    def test_plan_drift_and_expiration_fail_before_mutation(self):
        for change in ('drift', 'expiry'):
            with self.subTest(change=change):
                self.setUp()
                _, _, submitted = self.make_job()
                if change == 'drift':
                    self.adapter.revision += 1
                else:
                    self.clock += 301
                self.engine.run(submitted['id'])
                job = self.rpc('job', {'id': submitted['id']})['data']
                self.assertEqual(job['state'], 'failed')
                self.assertEqual(job['error']['code'], 'plan_changed' if change == 'drift' else 'expired')
                self.assertNotIn('entry', self.adapter.calls)
                self.assertTrue(all(stage['state'] == 'skipped' for stage in job['stages']))

    def test_followup_failure_keeps_entry_and_runs_other_independent_stage(self):
        for failure in ('ca_sync', 'firewall'):
            with self.subTest(failure=failure):
                self.setUp()
                self.adapter.fail = failure
                _, _, submitted = self.make_job('configure')
                self.engine.run(submitted['id'])
                job = self.rpc('job', {'id': submitted['id']})['data']
                self.assertEqual(job['state'], 'partial')
                self.assertEqual(job['stages'][0]['state'], 'succeeded')
                self.assertEqual(self.adapter.calls.count('firewall'), 1)
                self.assertEqual(job['result']['backup_id'], '20261029T000000-1234abcd')
                self.assertTrue(job['result']['ca_preserved'])
                for secret in ('APP_KEY', 'PRIVATE KEY', 'never-expose', 'unrelated firewall', 'stderr'):
                    self.assertNotIn(secret, json.dumps(job))

    def test_entry_failure_skips_followups_and_never_echoes_exception(self):
        self.adapter.fail = 'entry'
        _, _, submitted = self.make_job()
        self.engine.run(submitted['id'])
        job = self.rpc('job', {'id': submitted['id']})['data']
        self.assertEqual(job['state'], 'failed')
        self.assertEqual([stage['state'] for stage in job['stages']], ['failed', 'skipped', 'skipped'])
        self.assertNotIn('ca_sync', self.adapter.calls)
        self.assertNotIn('firewall', self.adapter.calls)
        self.assertNotIn('never-expose', json.dumps(job))

    def test_firewall_result_does_not_claim_reachability_or_expose_policy(self):
        self.adapter.firewall_status = 'saved_inactive'
        _, _, submitted = self.make_job('firewall')
        self.engine.run(submitted['id'])
        job = self.rpc('job', {'id': submitted['id']})['data']
        self.assertEqual(job['state'], 'succeeded')
        self.assertEqual(job['result']['firewall_result'], {'status': 'saved_inactive', 'port': 8443})
        self.assertEqual(job['stages'][0]['code'], 'saved_inactive')
        self.assertNotIn('stderr', json.dumps(job))
        self.assertNotIn('entry', self.adapter.calls)

    def test_partial_firewall_failure_is_distinct_from_no_mutation(self):
        for mutated in (False, True):
            self.setUp()
            self.adapter.firewall_status = 'error'
            self.adapter.mutated_firewall = mutated
            _, _, submitted = self.make_job('firewall')
            self.engine.run(submitted['id'])
            job = self.rpc('job', {'id': submitted['id']})['data']
            self.assertEqual(job['state'], 'partial' if mutated else 'failed')
            self.assertEqual(job['result']['firewall_result']['status'], 'error')

    def test_restart_never_replays_uncertain_job(self):
        _, parameters, submitted = self.make_job()
        stored = self.store.values['job'][submitted['id']]
        stored['state'] = 'running'
        stored['stages'][0]['state'] = 'running'
        recovered = web.Engine(policy(), self.adapter, self.store, start_worker=False, now=lambda: self.clock)
        job = recovered.job({'id': submitted['id']})
        self.assertEqual(job['state'], 'interrupted')
        self.assertEqual(job['stages'][0]['state'], 'interrupted')
        self.assertEqual(recovered.queue.qsize(), 0)
        self.assertEqual(recovered.execute(parameters)['state'], 'interrupted')
        self.assertNotIn('entry', self.adapter.calls)

    def test_crash_during_history_pruning_cannot_replay_an_unexpired_plan(self):
        _, parameters, submitted = self.make_job()
        self.engine.run(submitted['id'])
        original = self.store.remove
        removed = []
        def crash(kind, identity):
            removed.append(kind)
            original(kind, identity)
            raise RuntimeError('simulated process crash after durable removal')
        self.store.remove = crash
        with patch.object(web, 'MAX_JOBS', 1), self.assertRaises(RuntimeError):
            self.engine.prune()
        self.assertEqual(removed, ['plan'])
        recovered = web.Engine(policy(), self.adapter, self.store, start_worker=False, now=lambda: self.clock)
        with self.assertRaises(web.RpcError) as error:
            recovered.execute(parameters)
        self.assertEqual(error.exception.code, 'not_found')
        self.assertEqual(recovered.jobs[submitted['id']]['state'], 'succeeded')
        self.assertEqual(self.adapter.calls.count('entry'), 1)

    def test_queue_and_plan_storage_are_bounded(self):
        for _ in range(web.MAX_PENDING):
            self.make_job()
        plan = self.rpc('plan', {'action': 'refresh'})['data']
        self.assertEqual(self.rpc('execute', {'plan_id': plan['id'], 'digest': plan['digest'],
            'confirmation': plan['confirmation']})['error']['code'], 'capacity')
        with patch.object(web, 'MAX_PLANS', len(self.engine.plans)):
            self.assertEqual(self.rpc('plan', {'action': 'refresh'})['error']['code'], 'capacity')

    def test_active_operation_blocks_plan_without_waiting(self):
        self.engine.operations.acquire()
        try:
            self.assertEqual(self.rpc('plan', {'action': 'refresh'})['error']['code'], 'busy')
        finally:
            self.engine.operations.release()

    def test_job_ids_cannot_be_paths(self):
        for value in ('../private', '/etc/shadow', 'x' * 32, True, None):
            self.assertEqual(self.rpc('job', {'id': value})['error']['code'], 'invalid_request')
        self.assertEqual(self.rpc('job', {'id': 'a' * 32})['error']['code'], 'not_found')

    def test_shutdown_interrupts_queued_work_and_rejects_new_mutations(self):
        _, parameters, submitted = self.make_job()
        self.engine.shutdown(timeout=0)
        self.assertEqual(self.engine.queue.qsize(), 0)
        job = self.rpc('job', {'id': submitted['id']})['data']
        self.assertEqual(job['state'], 'interrupted')
        self.assertTrue(all(stage['state'] == 'skipped' for stage in job['stages']))
        self.assertEqual(self.rpc('plan', {'action': 'refresh'})['error']['code'], 'unavailable')
        self.assertEqual(self.rpc('execute', parameters)['error']['code'], 'unavailable')
        self.assertNotIn('entry', self.adapter.calls)

    def test_shutdown_waits_for_active_worker_without_starting_another_job(self):
        entered, release = threading.Event(), threading.Event()
        original = self.adapter.apply_entry
        def slow(saved):
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            return original(saved)
        self.adapter.apply_entry = slow
        self.engine = web.Engine(policy(), self.adapter, self.store, now=lambda: self.clock)
        _, _, submitted = self.make_job()
        self.assertTrue(entered.wait(timeout=5))
        shutdown = threading.Thread(target=self.engine.shutdown, kwargs={'timeout': 5})
        shutdown.start()
        self.assertTrue(self.engine.stopping.wait(timeout=5))
        release.set()
        shutdown.join(timeout=5)
        self.assertFalse(shutdown.is_alive())
        self.assertFalse(self.engine.worker_thread.is_alive())
        self.assertEqual(self.engine.job({'id': submitted['id']})['state'], 'succeeded')


class PolicyTests(unittest.TestCase):
    def test_valid_policy_and_reject_root_account_ports_extra_fields_and_docker(self):
        self.assertEqual(web.validate_policy(policy()), policy())
        bad = [{'app_uid': 0}, {'app_gid': 0}, {'allowed_ports': [80]}, {'allowed_ports': [443]},
               {'allowed_ports': [True]}, {'allowed_ports': [8443, 8443]}, {'state': '/'},
               {'command': 'shell'}, {'tools_sha256': 'f' * 64},
               {'binding': {**policy()['binding'], 'deployment': 'docker'}}]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(web.RpcError):
                web.validate_policy({**policy(), **change})

    def test_installer_rejects_docker_before_any_service_writes(self):
        tools = SimpleNamespace(manager=SimpleNamespace(safe_state=lambda value: Path(value)),
                                panel_ca=SimpleNamespace(payload_from_state=lambda value: ({'deployment': 'docker'}, {})))
        args = SimpleNamespace(confirm='ENABLE WEB MANAGEMENT', state='/var/lib/node-endpoint')
        with patch.object(web, 'root_only'), patch.object(web, 'PinnedTools', return_value=tools), \
                patch.object(web, 'atomic') as write, patch.object(web, 'systemctl') as systemctl:
            with self.assertRaises(web.RpcError) as error:
                web.install(args)
            self.assertEqual(error.exception.code, 'manual_required')
            write.assert_not_called()
            systemctl.assert_not_called()

    def test_installer_confirmation_precedes_tools_or_filesystem(self):
        args = SimpleNamespace(confirm=None)
        with patch.object(web, 'root_only'), patch.object(sys, 'stdin', SimpleNamespace(isatty=lambda: False)), \
                patch.object(web, 'PinnedTools') as tools:
            with self.assertRaises(web.RpcError):
                web.install(args)
            tools.assert_not_called()

    def test_final_review_is_rechecked_inside_cli_lock(self):
        adapter = object.__new__(web.RootAdapter)
        entered = []
        @contextmanager
        def locked():
            entered.append(True)
            yield
            entered.pop()
        adapter.locked = locked
        def prepare(*args, **kwargs):
            self.assertTrue(entered)
            self.assertTrue(kwargs['_locked'])
            return {'review': {'changed': True}, 'fingerprints': ['c' * 64]}
        adapter.prepare = prepare
        adapter.current = lambda: self.fail('Stale plan reached a Root mutation')
        saved = {'review': {'action': 'refresh', 'target': Adapter().target}, 'fingerprints': ['c' * 64]}
        with self.assertRaises(web.RpcError) as error:
            adapter.apply_entry(saved)
        self.assertEqual(error.exception.code, 'plan_changed')

    def test_runtime_command_adapter_keeps_trust_timeout_and_allow_failure_contract(self):
        manager = SimpleNamespace(command=Mock(), trusted_file=Mock(return_value=Path('/usr/sbin/nginx')))
        panel = SimpleNamespace(PanelBridge=object)
        tools = SimpleNamespace(manager=manager, pki=SimpleNamespace(), panel_passphrase=panel)
        adapter = web.RootAdapter(policy(), tools)
        with patch.object(web, 'bounded_capture', return_value=(b'output', b'diagnostic', 7)) as run:
            self.assertEqual(manager.command(['nginx', '-t'], timeout=12, allow_failure=True),
                             ('output', 'diagnostic', 7))
            self.assertEqual(run.call_args.kwargs['limit'], 4 * 1024 * 1024)
            self.assertEqual(run.call_args.kwargs['error_limit'], 65536)
            self.assertEqual(run.call_args.kwargs['timeout'], 12)
            manager.trusted_file.assert_called_once()
            with self.assertRaises(web.RpcError):
                manager.command(['nginx', '-t'])
        self.assertIsInstance(panel.PanelBridge({key: policy()[key] for key in ('app', 'app_user', 'php')}),
                              web.BoundedPanelBridge)

    def test_secret_bridge_fixed_context_and_exact_response(self):
        adapter = SimpleNamespace(policy=policy(), app_call=Mock(return_value={
            'credential_id': 'a' * 32, 'passphrase': 's' * 64}))
        context = {key: policy()[key] for key in ('app', 'app_user', 'php')}
        bridge = web.BoundedPanelBridge(adapter, context)
        self.assertEqual(bridge.call('get', 'a' * 32, host='example.test')['passphrase'], 's' * 64)
        adapter.app_call.assert_called_with('panel_passphrase.php', {
            'action': 'get', 'credential_id': 'a' * 32, 'host': 'example.test'})
        for change in ({'app': '/foreign/site'}, {'php': '/tmp/php'}, {'app_user': 'root'}):
            with self.assertRaises(web.RpcError):
                web.BoundedPanelBridge(adapter, {**context, **change})
        for response in ({'credential_id': 'b' * 32, 'passphrase': 's' * 64},
                         {'credential_id': 'a' * 32, 'passphrase': 'short'},
                         {'credential_id': 'a' * 32, 'passphrase': 's' * 64, 'stderr': 'secret'}):
            adapter.app_call.return_value = response
            with self.assertRaises(web.RpcError):
                bridge.call('get', 'a' * 32)
        with self.assertRaises(web.RpcError):
            bridge.call('prepare', 'a' * 32, host='example.test')


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in web.PinnedTools.REQUIRED:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        self.manifest()
        (self.root / '.nodeendpoint-verified-package.json').write_text(json.dumps({'sha256': web.TOOLS_SHA256}))
        self.tools = object.__new__(web.PinnedTools)
        self.tools.root = self.root
        self.secure = patch.object(web, 'secure', side_effect=lambda path, *args, **kwargs: Path(path))
        self.secure.start()
        self.addCleanup(self.secure.stop)
        self.read = patch.object(web, 'read_private', side_effect=lambda path: web.strict_json(Path(path).read_bytes()))
        self.read.start()
        self.addCleanup(self.read.stop)

    def manifest(self):
        lines = [hashlib.sha256((self.root / name).read_bytes()).hexdigest() + '  ' + name
                 for name in sorted(web.PinnedTools.REQUIRED)]
        (self.root / 'MANIFEST.sha256').write_text('\n'.join(lines) + '\n', encoding='ascii')

    def test_complete_member_hashes_and_manifest_pin(self):
        expected = hashlib.sha256((self.root / 'MANIFEST.sha256').read_bytes()).hexdigest()
        self.assertEqual(self.tools.verify(expected), expected)
        with self.assertRaises(web.RpcError):
            self.tools.verify('f' * 64)

    def test_member_tamper_missing_and_extra_files_are_rejected(self):
        path = self.root / 'tools/panel/manager.py'
        path.write_bytes(b'changed')
        with self.assertRaises(web.RpcError):
            self.tools.verify()
        self.manifest()
        self.tools.verify()
        extra = self.root / 'tools/panel/foreign.py'
        extra.write_bytes(b'not in manifest')
        with self.assertRaises(web.RpcError):
            self.tools.verify()
        extra.unlink()
        path.unlink()
        with self.assertRaises(web.RpcError):
            self.tools.verify()

    def test_duplicate_path_traversal_and_wrong_archive_marker_rejected(self):
        manifest = self.root / 'MANIFEST.sha256'
        original = manifest.read_bytes()
        for extra in (original.splitlines()[0] + b'\n', b'a' * 64 + b'  ../outside.py\n'):
            manifest.write_bytes(original + extra)
            with self.assertRaises(web.RpcError):
                self.tools.verify()
        manifest.write_bytes(original)
        (self.root / '.nodeendpoint-verified-package.json').write_text('{"sha256":"other"}')
        with self.assertRaises(web.RpcError):
            self.tools.verify()


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Linux Root permission/socket checks')
class LinuxPermissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='.web-manager-test-', dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)

    def test_private_store_durable_permissions_and_symlink_rejection(self):
        store = web.Store(self.root)
        value = {'id': 'a' * 32, 'state': 'queued'}
        store.save('job', value)
        path = store.path('job', value['id'])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(store.load_all('job')[value['id']], value)
        real = self.root / 'real'
        path.rename(real)
        path.symlink_to(real)
        with self.assertRaises(web.RpcError):
            store.load_all('job')

    def test_group_writable_ancestor_and_hardlink_rejected(self):
        path = self.root / 'private'
        path.write_text('data')
        path.chmod(0o600)
        os.link(path, self.root / 'second')
        with self.assertRaises(web.RpcError):
            web.secure(path)
        (self.root / 'second').unlink()
        self.root.chmod(0o770)
        with self.assertRaises(web.RpcError):
            web.secure(path)

    def test_history_removal_fsyncs_directory_before_returning(self):
        store = web.Store(self.root)
        value = {'id': 'a' * 32, 'state': 'succeeded'}
        store.save('job', value)
        original = os.fsync
        observed = []
        def observe(descriptor):
            observed.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
            original(descriptor)
        with patch.object(web.os, 'fsync', side_effect=observe):
            store.remove('job', value['id'])
        self.assertEqual(observed, [True])

    def test_actual_unix_peer_credentials_and_one_line_protocol(self):
        engine = web.Engine(policy(), Adapter(), MemoryStore(), start_worker=False)
        for raw, okay in ((b'{"schema":1,"operation":"status","parameters":{}}\n', True),
                          (b'{}\n{}\n', False), (b'x' * (web.MAX_REQUEST + 1), False)):
            with self.subTest(okay=okay):
                server, client = socket.socketpair(socket.AF_UNIX)
                with server, client:
                    thread = threading.Thread(target=web.connection, args=(engine, server))
                    thread.start()
                    client.settimeout(5)
                    client.sendall(raw)
                    data = b''
                    while not data.endswith(b'\n'):
                        data += client.recv(4096)
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(web.strict_json(data)['ok'], okay)

    def test_private_diagnostics_contain_frames_but_no_exception_text(self):
        with patch.object(web, 'PRIVATE', self.root):
            try:
                raise RuntimeError('APP_KEY=never-expose PRIVATE KEY password')
            except RuntimeError as error:
                web.audit('entry', error, 'a' * 32)
        path = self.root / 'events.jsonl'
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        event = web.strict_json(path.read_bytes())
        self.assertEqual(event['error_type'], 'RuntimeError')
        self.assertEqual(event['stage'], 'entry')
        self.assertTrue(event['frames'])
        self.assertNotIn('APP_KEY', path.read_text())
        self.assertNotIn('never-expose', path.read_text())

    def test_malicious_stdout_stderr_and_hanging_descendants_are_bounded(self):
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'HOME': '/root'}
        commands = ["import os; os.write(1,b'x'*131072)",
                    "import os; os.write(2,b'PRIVATE KEY'*131072)",
                    "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])"]
        for command in commands:
            with self.subTest(command=command), self.assertRaises(web.RpcError) as error:
                web.bounded_capture([sys.executable, '-I', '-B', '-c', command], None, environment,
                                    limit=4096, error_limit=1024, timeout=1)
            self.assertEqual(error.exception.code, 'operation_failed')
            self.assertNotIn('PRIVATE KEY', str(error.exception))

    def test_raw_bounded_command_preserves_nonzero_status_and_json_bridge_requires_success(self):
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'HOME': '/root'}
        output, errors, code = web.bounded_capture([sys.executable, '-I', '-B', '-c',
            "import sys;sys.stdout.write('out');sys.stderr.write('err');sys.exit(3)"], None, environment)
        self.assertEqual((output, errors, code), (b'out', b'err', 3))
        with self.assertRaises(web.RpcError):
            web.bounded_process([sys.executable, '-I', '-B', '-c', "raise SystemExit(3)"], {}, environment)


if __name__ == '__main__':
    unittest.main()
