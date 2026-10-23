"""Pinned Docker identities, private helper transport and strict command scope."""
import copy
import json
import os
import stat
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/panel'))
import docker_context as docker
from nginx import AUDITED_PROXY_TRUST_SHA256
from pki import SafetyError

CID, IMAGE, DAEMON = 'a' * 64, 'sha256:' + 'b' * 64, 'test-daemon-123456'
SECRET = 'private-credential-must-not-appear-in-argv'


def context():
    return {'schema_version': 1, 'container_id': CID, 'image_id': IMAGE, 'daemon_id': DAEMON,
            'project': 'panel', 'service': 'xboard', 'working_dir': '/srv/panel', 'app': '/www',
            'uid': 1000, 'gid': 1000, 'php': '/usr/local/bin/php', 'host_port': 17001,
            'container_port': 7001, 'published_ip': '127.0.0.1',
            'bind_sources': {destination: '/srv/panel/' + source for destination, source in
                             [('/www/.env', '.env'), ('/www/storage/app', 'storage/app'),
                              ('/www/.docker/.data', '.docker/.data'), ('/www/plugins', 'plugins')]},
            'volume_sources': {'/data': 'panel_redis-data'}}


def inspection():
    ctx = context()
    return {'Id': CID, 'Image': IMAGE, 'State': {'Running': True, 'Paused': False},
            'Config': {'WorkingDir': '/www', 'Env': ['DB_PASSWORD=' + SECRET],
                       'Labels': {'com.docker.compose.project': 'panel', 'com.docker.compose.service': 'xboard',
                                  'com.docker.compose.project.working_dir': '/srv/panel'}},
            'HostConfig': {'Privileged': False, 'NetworkMode': 'panel_default', 'PidMode': '',
                           'IpcMode': 'private', 'UsernsMode': '', 'CapAdd': None, 'Devices': []},
            'NetworkSettings': {'Ports': {'7001/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '17001'}]}},
            'Mounts': [*({'Type': 'bind', 'Source': source, 'Destination': destination, 'RW': True}
                        for destination, source in ctx['bind_sources'].items()),
                       {'Type': 'volume', 'Source': '/var/lib/docker/volumes/panel_redis-data/_data',
                        'Name': 'panel_redis-data', 'Driver': 'local', 'Destination': '/data', 'RW': True}]}


class DockerContextTests(unittest.TestCase):
    def setUp(self):
        self.host_paths = patch.object(docker, '_host_bind_path', side_effect=lambda value: value)
        self.host_paths.start()
        self.addCleanup(self.host_paths.stop)
        self.item = inspection()
        self.probe = {'uid': 1000, 'gid': 1000, 'app': '/www', 'php': '/usr/local/bin/php'}
        self.volume = {'Name': 'panel_redis-data', 'Driver': 'local', 'Options': None}
        self.response = {'status': 'stored'}

    def answer(self, arguments, input_data=None, max_output=4096, timeout=90):
        if arguments[:1] == ['inspect']:
            return json.dumps([self.item]).encode()
        if arguments[:2] == ['volume', 'inspect']:
            return json.dumps([self.volume]).encode()
        if arguments[:1] == ['info']:
            return json.dumps(DAEMON).encode()
        if arguments[:1] == ['exec']:
            if 'www' in arguments:
                return json.dumps(self.probe).encode()
            if 'artisan' in arguments:
                return b'[]'
            if input_data is None:
                return json.dumps({'sha256': next(iter(AUDITED_PROXY_TRUST_SHA256))}).encode()
            return json.dumps(self.response).encode()
        raise AssertionError('Unexpected Docker operation')

    def test_standard_image_with_partial_bind_volumes_and_real_nonroot_uid_is_pinned(self):
        with patch.object(docker, '_docker', side_effect=self.answer):
            ctx = docker.inspect_context(CID, 'http://127.0.0.1:17001')
        self.assertEqual(ctx, context())
        self.assertNotIn('/www', ctx['bind_sources'])
        self.assertNotIn(SECRET, json.dumps(ctx))
        with patch.object(docker.manager, 'linux_root', side_effect=AssertionError('Pure validation accessed OS')):
            self.assertEqual(docker.validate_context(ctx), ctx)

    def test_replaced_cid_or_changed_execution_identity_requires_new_root_review(self):
        with patch.object(docker, '_docker') as command, self.assertRaises(SafetyError):
            docker.inspect_context('c' * 64, expected=context())
        command.assert_not_called()
        for change in ({'uid': 1001}, {'gid': 1001}):
            self.probe = {**self.probe, **change}
            with self.subTest(change=change), patch.object(docker, '_docker', side_effect=self.answer), self.assertRaises(SafetyError):
                docker.inspect_context(CID, expected=context())
        self.probe = {'uid': 1000, 'gid': 1000, 'app': '/www', 'php': '/usr/local/bin/php'}
        self.item['Image'] = 'sha256:' + 'c' * 64
        with patch.object(docker, '_docker', side_effect=self.answer), self.assertRaises(SafetyError):
            docker.inspect_context(CID, expected=context())

    def test_public_or_ambiguous_backends_are_refused(self):
        for bindings in ([{'HostIp': '0.0.0.0', 'HostPort': '17001'}],
                         [{'HostIp': '', 'HostPort': '17001'}],
                         [{'HostIp': '::1', 'HostPort': '17001'}],
                         [{'HostIp': '127.0.0.1', 'HostPort': '17001'}, {'HostIp': '::', 'HostPort': '17001'}],
                         [{'HostIp': '127.0.0.1', 'HostPort': '17001'}, {'HostIp': '127.0.0.1', 'HostPort': '17002'}]):
            self.item['NetworkSettings']['Ports']['7001/tcp'] = bindings
            with self.subTest(bindings=bindings), patch.object(docker, '_docker', side_effect=self.answer), self.assertRaisesRegex(SafetyError, 'loopback'):
                docker.inspect_context(CID)
        self.item = inspection()
        for backend in ('http://public.example:17001', 'http://127.0.0.1:17002', 'http://127.0.0.1:17001/'):
            with self.subTest(backend=backend), patch.object(docker, '_docker', side_effect=self.answer), self.assertRaises(SafetyError):
                docker.inspect_context(CID, backend)

    def test_root_privileges_host_namespaces_and_dangerous_mounts_are_refused(self):
        for change in ({'Privileged': True}, {'NetworkMode': 'host'}, {'PidMode': 'host'},
                       {'IpcMode': 'host'}, {'UTSMode': 'host'}, {'CgroupnsMode': 'host'},
                       {'CapAdd': ['SYS_ADMIN']}, {'Devices': [{'PathOnHost': '/dev/sda'}]}):
            self.item = inspection()
            self.item['HostConfig'].update(change)
            with self.subTest(change=change), patch.object(docker, '_docker', side_effect=self.answer), self.assertRaises(SafetyError):
                docker.inspect_context(CID)
        for source in ('/var/run/docker.sock', '/run', '/var/lib/node-endpoint', '/var/lib', '/etc', '/opt/nodeendpoint-quick'):
            self.item = inspection()
            self.item['Mounts'].append({'Type': 'bind', 'Source': source, 'Destination': '/extra', 'RW': True})
            with self.subTest(source=source), patch.object(docker, '_docker', side_effect=self.answer), self.assertRaises(SafetyError):
                docker.inspect_context(CID)
        self.item = inspection()
        self.volume['Options'] = {'type': 'none', 'device': '/var/lib/node-endpoint', 'o': 'bind'}
        with patch.object(docker, '_docker', side_effect=self.answer), self.assertRaises(SafetyError):
            docker.inspect_context(CID)

    def test_missing_persistence_wrong_service_and_root_probe_are_refused(self):
        for destination in docker.REQUIRED_BINDS:
            self.item = inspection()
            self.item['Mounts'] = [mount for mount in self.item['Mounts'] if mount['Destination'] != destination]
            with self.subTest(destination=destination), patch.object(docker, '_docker', side_effect=self.answer), self.assertRaises(SafetyError):
                docker.inspect_context(CID)
        self.item = inspection()
        self.item['Config']['Labels']['com.docker.compose.service'] = 'other'
        with patch.object(docker, '_docker', side_effect=self.answer), self.assertRaises(SafetyError):
            docker.inspect_context(CID)
        self.item = inspection()
        for field in ('uid', 'gid'):
            self.probe = {'uid': 1000, 'gid': 1000, 'app': '/www', 'php': '/usr/local/bin/php', field: 0}
            with self.subTest(field=field), patch.object(docker, '_docker', side_effect=self.answer), self.assertRaises(SafetyError):
                docker.inspect_context(CID)

    def test_pure_structure_rejects_corrupt_or_overpermissive_saved_contexts(self):
        for change in ({'uid': 0}, {'uid': True}, {'host_port': '17001'}, {'published_ip': '0.0.0.0'},
                       {'container_id': 'short-id'}, {'app': '/other'}, {'working_dir': '/srv/../private'},
                       {'bind_sources': {}}, {'unexpected': SECRET}):
            with self.subTest(change=change), self.assertRaises(SafetyError):
                docker.validate_context({**context(), **change})

    def test_php_and_loader_container_overrides_are_rejected_without_leaking_values(self):
        for key in ('PHPRC', 'PHP_INI_SCAN_DIR', 'PHP_INI_DIR', 'PHP_CUSTOM_DIR', 'PHP_VALUE',
                    'PHP_ADMIN_VALUE', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'DYLD_INSERT_LIBRARIES', 'BASH_ENV', 'ENV'):
            self.item = inspection()
            self.item['Config']['Env'].append(key + '=' + SECRET)
            with self.subTest(key=key), patch.object(docker, '_docker', side_effect=self.answer), self.assertRaises(SafetyError) as caught:
                docker.inspect_context(CID)
            self.assertNotIn(SECRET, str(caught.exception))
        self.item = inspection()
        self.item['Config']['Env'].append('PHP_INI_DIR=/usr/local/etc/php')
        with patch.object(docker, '_docker', side_effect=self.answer) as command:
            docker.inspect_context(CID)
        probe = command.call_args.args[0]
        self.assertIn('auto_prepend_file=', probe)
        self.assertIn('auto_append_file=', probe)

    def test_fixed_helper_uses_numeric_identity_and_json_stdin_only(self):
        payload = {'credential_id': 'c' * 32, 'passphrase': SECRET}
        with patch.object(docker, '_docker', side_effect=self.answer) as command, \
                patch.object(docker.manager, 'trusted_file', side_effect=lambda path, *args: Path(path)):
            self.assertEqual(docker.run_helper(context(), 'panel_passphrase', payload), self.response)
        arguments, data, limit = command.call_args.args
        self.assertEqual(arguments[arguments.index('--user') + 1], '1000:1000')
        self.assertEqual(arguments[arguments.index('--workdir') + 1], '/www')
        self.assertIn(CID, arguments)
        self.assertNotIn(SECRET, '\n'.join(arguments))
        self.assertEqual(json.loads(data), payload)
        self.assertEqual(limit, 4096)
        for key in ('unknown', '../panel_passphrase', {}, None):
            with self.subTest(key=key), patch.object(docker, '_docker') as command, self.assertRaises(SafetyError):
                docker.run_helper(context(), key, {})
            command.assert_not_called()

    def test_artisan_commands_are_limited_and_never_shell_interpreted(self):
        for arguments in (['route:list', '--json'], ['list', '--raw'],
                          ['xboard:configure-endpoint', '--host=8.8.8.8', '--port=8443', '--ws-mode=rest']):
            with self.subTest(arguments=arguments), patch.object(docker, '_docker', side_effect=self.answer) as command:
                self.assertEqual(docker.run_artisan(context(), arguments), '[]')
                self.assertEqual(command.call_args.kwargs['max_output'], 2097152)
                self.assertEqual(command.call_args.args[0][-len(arguments):], arguments)
                self.assertIn('auto_prepend_file=', command.call_args.args[0])
                self.assertIn('auto_append_file=', command.call_args.args[0])
        for arguments in (['migrate', '--force'], ['route:list', '--json', '--path=secret'],
                          ['xboard:configure-endpoint', '--host=8.8.8.8;touch /tmp/no', '--port=8443', '--ws-mode=rest'],
                          ['xboard:configure-endpoint', '--host=8.8.8.8', '--host=1.1.1.1', '--port=8443', '--ws-mode=rest']):
            with self.subTest(arguments=arguments), patch.object(docker, '_docker') as command, self.assertRaises(SafetyError):
                docker.run_artisan(context(), arguments)
            command.assert_not_called()

    def test_only_audited_proxy_profile_is_accepted(self):
        with patch.object(docker, '_docker', side_effect=self.answer):
            self.assertIn(docker.read_proxy_profile(context()), AUDITED_PROXY_TRUST_SHA256)
        with patch.object(docker, '_refresh', return_value=context()), \
                patch.object(docker, '_exec', return_value=json.dumps({'sha256': 'd' * 64}).encode()), self.assertRaises(SafetyError):
            docker.read_proxy_profile(context())

    def test_cli_always_uses_private_configuration_and_fixed_local_socket(self):
        with patch.object(docker, '_client_config', return_value=Path('/root-private/client')), \
                patch.object(docker.manager, 'trusted_file', return_value=Path('/usr/bin/docker')), \
                patch.object(docker, '_bounded_process', return_value=b'{}') as run:
            docker._docker(['inspect', CID])
        self.assertEqual(run.call_args.args[0], [str(Path('/usr/bin/docker')), '--config', str(Path('/root-private/client')),
                                                '--host', 'unix:///var/run/docker.sock', 'inspect', CID])

    def test_discovery_without_docker_never_creates_root_client_configuration(self):
        with patch.object(docker.shutil, 'which', return_value=None), patch.object(docker, '_client_config') as config:
            self.assertEqual(docker.list_contexts(), [])
        config.assert_not_called()

    def test_discovery_returns_only_complete_audited_matching_container_contexts(self):
        other = {**context(), 'container_id': 'd' * 64, 'working_dir': '/srv/other'}
        socket = SimpleNamespace(exists=lambda: True, stat=lambda: SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660))
        output = '\n'.join(['short-id', CID, CID, 'c' * 64, 'd' * 64]).encode()
        def inspect(identity):
            if identity == 'c' * 64:
                raise SafetyError('Not an approved container')
            return context() if identity == CID else other
        with patch.object(docker.shutil, 'which', return_value='/usr/bin/docker'), patch.object(docker, 'SOCKET', socket), \
                patch.object(docker, '_docker', return_value=output), patch.object(docker, 'inspect_context', side_effect=inspect) as audited:
            self.assertEqual(docker.list_contexts('/srv/panel'), [context()])
        self.assertEqual(audited.call_count, 3)


class BoundedTransportTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'linux', 'Real Linux host path semantics required')
    def test_symlinked_bind_source_cannot_hide_a_privileged_host_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            private = base / 'protected'
            private.mkdir()
            (base / 'innocent').symlink_to(private, target_is_directory=True)
            with patch.object(docker, 'PROTECTED', (*docker.PROTECTED, str(private))), self.assertRaises(SafetyError):
                docker._host_bind_path(str(base / 'innocent'))

    @unittest.skipUnless(sys.platform == 'linux', 'Real Linux mounted source semantics required')
    def test_root_state_cannot_live_inside_bind_or_named_volume_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            ctx = context()
            ctx['bind_sources'] = {destination: str(base / str(index)) for index, destination in enumerate(docker.REQUIRED_BINDS)}
            for source in ctx['bind_sources'].values():
                Path(source).mkdir()
            volume = base / 'volume'
            volume.mkdir()
            response = [{'Name': 'panel_redis-data', 'Driver': 'local', 'Options': None, 'Mountpoint': str(volume)}]
            with patch.object(docker, '_docker', return_value=json.dumps(response).encode()):
                for source in (*ctx['bind_sources'].values(), str(volume)):
                    with self.subTest(source=source), self.assertRaises(SafetyError):
                        docker.assert_state_isolated(ctx, Path(source) / 'root-state')
                docker.assert_state_isolated(ctx, base / 'outside')

    def test_child_environment_drops_docker_remote_and_proxy_variables(self):
        program = "import json,os; print(json.dumps({k:os.environ.get(k) for k in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_CONFIG','DOCKER_API_VERSION','HTTP_PROXY','HTTPS_PROXY']}))"
        with patch.dict(os.environ, {'DOCKER_HOST': 'tcp://untrusted:2375', 'DOCKER_CONTEXT': 'remote', 'HTTPS_PROXY': SECRET}):
            result = docker._bounded_process([sys.executable, '-c', program])
        self.assertTrue(all(value is None for value in json.loads(result).values()))

    def test_output_overflow_and_timeout_stop_with_fixed_errors(self):
        for program in ("import sys;sys.stdout.buffer.write(b'x'*20000)",
                        "import sys;sys.stderr.buffer.write(b'x'*20000)",
                        "import time;time.sleep(3)"):
            with self.subTest(program=program), self.assertRaisesRegex(SafetyError, '^' + docker.FAILURE.replace('.', r'\.') + '$'):
                docker._bounded_process([sys.executable, '-c', program], max_output=4096, timeout=0.2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
