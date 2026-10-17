"""Isolated firewall policy/command tests; no system firewall is touched.

Run: python3 tests/panel/test_firewall.py
"""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/panel'))
import firewall
from pki import SafetyError


class FirewallSystem:
    """A deliberately small command protocol, rejecting all broad mutations."""
    def __init__(self, firewalld=None, ufw=None, raw=()):
        self.firewalld = firewalld
        self.ufw = ufw
        self.raw = raw
        self.commands = []
        self.options = []
        self.default_zone = 'public'
        self.active_zones = 'public\n  interfaces: eth0\n'
        self.runtime = {}
        self.permanent = {}
        self.saved = []
        self.rows = {4: ['22/tcp ALLOW IN Anywhere'], 6: []}
        self.ipv6 = False
        self.fail = {}
        self.lose_insert = False
        self.hidden_port = None

    def which(self, name, path=None):
        if name == 'firewall-cmd' and self.firewalld is not None:
            return '/usr/bin/firewall-cmd'
        if name == 'ufw' and self.ufw is not None:
            return '/usr/sbin/ufw'
        return '/usr/sbin/' + name if name in self.raw else None

    def added(self):
        return 'Added user rules:\n' + ''.join(line + '\n' for line in self.saved)

    def numbered(self):
        if self.ufw == 'inactive':
            return 'Status: inactive\n'
        lines = ['Status: active', '', '     To                         Action      From', '     --                         ------      ----']
        index = 0
        for family in (4, 6):
            for row in self.rows[family]:
                index += 1
                lines.append(f'[{index:2}] {row}')
        return '\n'.join(lines) + '\n'

    def run(self, argv, **options):
        self.commands.append(tuple(argv))
        self.options.append(options)
        key = tuple(argv)
        if key in self.fail:
            failure = self.fail[key]
            if isinstance(failure, BaseException):
                raise failure
            return subprocess.CompletedProcess(argv, failure, 'unrelated-secret', 'private firewall diagnostics')
        binary, args = argv[0], argv[1:]
        if binary == '/usr/bin/firewall-cmd':
            if args == ['--state']:
                return subprocess.CompletedProcess(argv, 0 if self.firewalld == 'active' else 252,
                                                   'running\n' if self.firewalld == 'active' else '', '')
            if args == ['--get-default-zone']:
                return subprocess.CompletedProcess(argv, 0, self.default_zone + '\n', '')
            if args == ['--get-active-zones']:
                return subprocess.CompletedProcess(argv, 0, self.active_zones, '')
            scope = self.permanent if '--permanent' in args else self.runtime
            args = [arg for arg in args if arg != '--permanent']
            if len(args) != 2 or not args[0].startswith('--zone='):
                raise AssertionError('Unexpected firewalld command: ' + repr(argv))
            zone = args[0].split('=', 1)[1]
            port = args[1].split('=', 1)[1]
            if args[1].startswith('--query-port='):
                present = port in scope.get(zone, set()) and port != self.hidden_port
                return subprocess.CompletedProcess(argv, 0 if present else 1, 'yes\n' if present else 'no\n', '')
            if args[1].startswith('--add-port='):
                scope.setdefault(zone, set()).add(port)
                return subprocess.CompletedProcess(argv, 0, 'success\n', '')
            raise AssertionError('Unexpected firewalld mutation: ' + repr(argv))
        if binary == '/usr/sbin/ufw':
            if args == ['status', 'numbered']:
                return subprocess.CompletedProcess(argv, 0, self.numbered(), '')
            if args == ['show', 'added']:
                return subprocess.CompletedProcess(argv, 0, self.added(), '')
            if len(args) == 7 and args[:4] == ['insert', '1', 'allow', 'in'] and args[5:] == ['comment', 'NodeEndpoint']:
                port = args[4]
                if not self.lose_insert and not any(line.startswith('ufw allow ' + port) or line.startswith('ufw allow in ' + port)
                                                 for line in self.saved):
                    self.saved.insert(0, 'ufw allow ' + port + " comment 'NodeEndpoint'")
                    self.rows[4].insert(0, port + ' ALLOW IN Anywhere # NodeEndpoint')
                    if self.ipv6:
                        self.rows[6].insert(0, port + ' (v6) ALLOW IN Anywhere (v6) # NodeEndpoint')
                return subprocess.CompletedProcess(argv, 0, 'Rule inserted or existing rule skipped\n', '')
            raise AssertionError('Unexpected UFW mutation: ' + repr(argv))
        raise AssertionError('Unknown executable: ' + repr(argv))


class FirewallTests(unittest.TestCase):
    def execute(self, system, port=8443):
        with patch('firewall.linux_root') as root, \
                patch('firewall.shutil.which', side_effect=system.which), \
                patch('firewall.trusted_file', side_effect=lambda path, executable: path) as trusted, \
                patch('firewall.subprocess.run', side_effect=system.run):
            result = firewall.ensure_port(port)
        root.assert_called_once_with()
        for call in trusted.call_args_list:
            self.assertTrue(call.kwargs['executable'])
        # This receipt must never contain private diagnostics or claim a network probe.
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn('unrelated-secret', serialized)
        self.assertNotIn('private firewall diagnostics', serialized)
        self.assertEqual(result['network_reachability'], 'not_tested')
        self.assertEqual(result['port'], port)
        self.assertEqual(result['protocol'], 'tcp')
        return result

    def test_invalid_port_never_discovers_or_runs_any_command(self):
        for value in (True, False, None, '8443', '8443;reboot', 8443.0, 0, -1, 65536):
            with self.subTest(value=value), patch('firewall.linux_root') as root, \
                    patch('firewall.shutil.which') as which, patch('firewall.subprocess.run') as run:
                with self.assertRaises(SafetyError):
                    firewall.ensure_port(value)
                root.assert_not_called()
                which.assert_not_called()
                run.assert_not_called()

    def test_linux_root_guard_precedes_detection(self):
        with patch('firewall.linux_root', side_effect=SafetyError('Linux Root required')), \
                patch('firewall.shutil.which') as which, patch('firewall.subprocess.run') as run:
            with self.assertRaises(SafetyError):
                firewall.ensure_port(8443)
            which.assert_not_called()
            run.assert_not_called()

    def test_port_boundaries_are_accepted(self):
        for port in (1, 65535):
            result = self.execute(FirewallSystem(), port)
            self.assertEqual(result['status'], 'manual_required')

    def test_absent_managers_and_raw_backends_are_manual_only(self):
        system = FirewallSystem(raw=('nft', 'iptables', 'ip6tables'))
        result = self.execute(system)
        self.assertEqual(result['status'], 'manual_required')
        self.assertEqual(result['manager'], 'none')
        self.assertEqual(result['raw_tools_present'], ['nft', 'iptables', 'ip6tables'])
        self.assertFalse(result['raw_rules_modified'])
        self.assertFalse(result['mutation_attempted'])
        self.assertEqual(system.commands, [])

    def test_inactive_firewalld_is_not_started_or_configured(self):
        system = FirewallSystem(firewalld='inactive')
        result = self.execute(system)
        self.assertEqual(result['status'], 'manual_required')
        self.assertEqual(result['observed_managers'], {'firewalld': 'inactive', 'ufw': 'absent'})
        self.assertEqual(system.commands, [('/usr/bin/firewall-cmd', '--state')])

    def test_ufw_inactive_persists_rule_without_enabling_and_is_idempotent(self):
        system = FirewallSystem(ufw='inactive')
        first = self.execute(system)
        self.assertEqual(first['status'], 'saved_inactive')
        self.assertFalse(first['active'])
        self.assertFalse(first['runtime_applied'])
        self.assertTrue(first['persistent'])
        self.assertTrue(first['changed'])
        self.assertIn('未启用', first['notice'])
        self.assertIn('没有启用', first['notice'])
        saved = list(system.saved)
        second = self.execute(system)
        self.assertEqual(second['status'], 'saved_inactive')
        self.assertFalse(second['changed'])
        self.assertEqual(system.saved, saved)
        self.assertEqual(system.ufw, 'inactive')

    def test_active_ufw_inserts_before_deny_for_both_families_without_touching_ssh(self):
        system = FirewallSystem(ufw='active')
        system.ipv6 = True
        system.rows[4].append('8443/tcp DENY IN Anywhere')
        system.rows[6] = ['22/tcp (v6) ALLOW IN Anywhere (v6)', '8443/tcp (v6) DENY IN Anywhere (v6)']
        before = {key: list(rows) for key, rows in system.rows.items()}
        result = self.execute(system)
        self.assertEqual(result['status'], 'ensured')
        self.assertEqual(result['listed_ip_families'], [4, 6])
        self.assertTrue(result['runtime_applied'])
        self.assertTrue(result['user_rule_priority_verified'])
        self.assertTrue(result['persistent'])
        for family in (4, 6):
            self.assertEqual(system.rows[family][1:], before[family])
        saved, rows = list(system.saved), {key: list(items) for key, items in system.rows.items()}
        self.assertFalse(self.execute(system)['changed'])
        self.assertEqual(system.saved, saved)
        self.assertEqual(system.rows, rows)
        self.assertIn(('/usr/sbin/ufw', 'insert', '1', 'allow', 'in', '8443/tcp', 'comment', 'NodeEndpoint'), system.commands)

    def test_existing_allow_below_deny_does_not_false_claim_open_port(self):
        system = FirewallSystem(ufw='active')
        system.saved = ['ufw deny 8443/tcp', "ufw allow 8443/tcp comment 'Original administrator'"]
        system.rows[4] = ['8443/tcp DENY IN Anywhere', '8443/tcp ALLOW IN Anywhere # Original administrator']
        before = list(system.saved)
        result = self.execute(system)
        self.assertEqual(result['status'], 'manual_required')
        self.assertEqual(result['reason'], 'ufw_existing_rule_precedence_requires_review')
        self.assertFalse(result['runtime_applied'])
        self.assertFalse(result['changed'])
        self.assertEqual(system.saved, before)

    def test_ipv6_deny_precedence_is_not_hidden_by_ipv4_allow(self):
        system = FirewallSystem(ufw='active')
        system.saved = ['ufw allow 8443/tcp']
        system.rows[4] = ['8443/tcp ALLOW IN Anywhere']
        system.rows[6] = ['Anywhere (v6) REJECT IN Anywhere (v6)', '8443/tcp (v6) ALLOW IN Anywhere (v6)']
        result = self.execute(system)
        self.assertEqual(result['status'], 'manual_required')
        self.assertEqual(result['listed_ip_families'], [4, 6])

    def test_existing_allow_after_ssh_allow_is_reused_without_rewriting_comment(self):
        system = FirewallSystem(ufw='active')
        system.saved = ["ufw allow 8443/tcp comment 'Original administrator'"]
        system.rows[4].append('8443/tcp ALLOW IN Anywhere # Original administrator')
        result = self.execute(system)
        self.assertEqual(result['status'], 'ensured')
        self.assertFalse(result['changed'])
        self.assertEqual(system.saved, ["ufw allow 8443/tcp comment 'Original administrator'"])

    def test_a_scoped_outgoing_or_udp_rule_does_not_verify_saved_incoming_tcp_rule(self):
        for saved in ('ufw allow out 8443/tcp', 'ufw allow 8443/udp',
                      'ufw allow in on eth0 to any port 8443 proto tcp',
                      'ufw allow from 192.0.2.1 to any port 8443 proto tcp',
                      'ufw allow 8443:8444/tcp'):
            with self.subTest(saved=saved):
                system = FirewallSystem(ufw='inactive')
                system.saved = [saved]
                system.lose_insert = True
                result = self.execute(system)
                self.assertEqual(result['status'], 'error')
                self.assertEqual(result['error']['reason'], 'persistent_rule_not_verified')

    def test_both_active_managers_fail_before_any_port_change(self):
        system = FirewallSystem(firewalld='active', ufw='active')
        result = self.execute(system)
        self.assertEqual(result['status'], 'manual_required')
        self.assertEqual(result['manager'], 'firewalld+ufw')
        self.assertFalse(result['mutation_attempted'])
        self.assertEqual(system.runtime, {})
        self.assertEqual(system.permanent, {})
        self.assertEqual(system.saved, [])
        self.assertEqual(len(system.commands), 2)

    def test_running_firewalld_reports_inactive_ufw_without_modifying_it(self):
        system = FirewallSystem(firewalld='active', ufw='inactive')
        result = self.execute(system)
        self.assertEqual(result['manager'], 'firewalld')
        self.assertEqual(result['status'], 'ensured')
        self.assertEqual(result['observed_managers']['ufw'], 'inactive')
        self.assertEqual(system.saved, [])
        self.assertEqual([cmd for cmd in system.commands if cmd[0].endswith('/ufw')],
                         [('/usr/sbin/ufw', 'status', 'numbered')])

    def test_stopped_firewalld_and_inactive_ufw_save_only_the_ufw_rule(self):
        system = FirewallSystem(firewalld='inactive', ufw='inactive')
        result = self.execute(system)
        self.assertEqual(result['status'], 'saved_inactive')
        self.assertEqual(result['manager'], 'ufw')
        self.assertEqual(system.runtime, {})
        self.assertEqual(system.permanent, {})

    def test_firewalld_default_and_all_active_zones_get_both_scopes(self):
        system = FirewallSystem(firewalld='active')
        system.default_zone = 'drop'
        system.active_zones = 'public\n  interfaces: eth0 eth1\nwork\n  sources: 192.0.2.0/24\npublic\n'
        system.runtime = {'public': {'22/tcp'}}
        system.permanent = {'public': {'22/tcp'}}
        result = self.execute(system)
        self.assertEqual(result['status'], 'ensured')
        self.assertEqual(result['zones'], ['drop', 'public', 'work'])
        self.assertEqual(len(result['changes']), 6)
        for scope in (system.runtime, system.permanent):
            for zone in result['zones']:
                self.assertIn('8443/tcp', scope[zone])
            self.assertIn('22/tcp', scope['public'])
        first_add = next(index for index, args in enumerate(system.commands) if any('--add-port=' in arg for arg in args))
        queries_before = [cmd for cmd in system.commands[:first_add] if '--query-port=8443/tcp' in cmd]
        self.assertEqual(len(queries_before), 6)
        commands_before = len(system.commands)
        again = self.execute(system)
        self.assertFalse(again['changed'])
        self.assertFalse(again['mutation_attempted'])
        self.assertFalse(any('--add-port=8443/tcp' in args for args in system.commands[commands_before:]))

    def test_firewalld_adds_only_missing_permanent_scope(self):
        system = FirewallSystem(firewalld='active')
        system.runtime = {'public': {'8443/tcp', '22/tcp'}}
        system.permanent = {'public': {'22/tcp'}}
        result = self.execute(system)
        self.assertEqual(result['changes'], [{'manager': 'firewalld', 'zone': 'public', 'scope': 'permanent', 'action': 'add_port'}])
        self.assertNotIn(('/usr/bin/firewall-cmd', '--zone=public', '--add-port=8443/tcp'), system.commands)

    def test_bad_firewalld_zone_output_never_reaches_a_mutation(self):
        for default, active in [('public\n--reload', ''), ('public', '--reload\n'),
                                ('public', '  interfaces: eth0\n'), ('public', 'public\n  unexpected: thing\n')]:
            with self.subTest(default=default, active=active):
                system = FirewallSystem(firewalld='active')
                system.default_zone, system.active_zones = default, active
                result = self.execute(system)
                self.assertEqual(result['status'], 'error')
                self.assertFalse(result['mutation_attempted'])
                self.assertEqual(system.runtime, {})
                self.assertEqual(system.permanent, {})

    def test_unknown_firewalld_state_code_is_not_assumed_inactive(self):
        system = FirewallSystem(firewalld='active', ufw='active')
        system.fail[('/usr/bin/firewall-cmd', '--state')] = 251
        result = self.execute(system)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error']['returncode'], 251)
        self.assertFalse(result['mutation_attempted'])

    def test_success_exit_with_unknown_manager_state_does_not_mutate(self):
        for binary, args in (('/usr/bin/firewall-cmd', ('--state',)),
                             ('/usr/sbin/ufw', ('status', 'numbered'))):
            with self.subTest(binary=binary):
                system = FirewallSystem(firewalld='active', ufw='active')
                system.fail[(binary, *args)] = 0  # Successful command, invalid state text.
                result = self.execute(system)
                self.assertEqual(result['status'], 'error')
                self.assertEqual(result['error']['reason'], 'unrecognized_manager_state')
                self.assertFalse(result['mutation_attempted'])

    def test_query_error_preflights_all_scopes_without_partial_runtime_change(self):
        system = FirewallSystem(firewalld='active')
        system.fail[('/usr/bin/firewall-cmd', '--permanent', '--zone=public', '--query-port=8443/tcp')] = 254
        result = self.execute(system)
        self.assertEqual(result['status'], 'error')
        self.assertFalse(result['partial_application_possible'])
        self.assertEqual(system.runtime, {})

    def test_permanent_add_failure_surfaces_runtime_partial_change(self):
        system = FirewallSystem(firewalld='active')
        system.fail[('/usr/bin/firewall-cmd', '--permanent', '--zone=public', '--add-port=8443/tcp')] = 13
        result = self.execute(system)
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['partial_application_possible'])
        self.assertTrue(result['changed'])
        self.assertEqual(result['changes'], [{'manager': 'firewalld', 'zone': 'public', 'scope': 'runtime', 'action': 'add_port'}])
        self.assertIn('8443/tcp', system.runtime['public'])
        self.assertEqual(system.permanent, {})
        self.assertEqual(result['error']['returncode'], 13)

    def test_successful_add_without_verified_firewalld_rule_is_an_error(self):
        system = FirewallSystem(firewalld='active')
        system.hidden_port = '8443/tcp'
        result = self.execute(system)
        self.assertEqual(result['status'], 'error')
        self.assertIn(':verify', result['error']['operation'])
        self.assertTrue(result['partial_application_possible'])

    def test_ufw_insert_failure_does_not_claim_persistence(self):
        system = FirewallSystem(ufw='inactive')
        key = ('/usr/sbin/ufw', 'insert', '1', 'allow', 'in', '8443/tcp', 'comment', 'NodeEndpoint')
        system.fail[key] = 1
        result = self.execute(system)
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['partial_application_possible'])
        self.assertNotIn('persistent', result)

    def test_status_command_failure_does_not_mutate_other_manager(self):
        system = FirewallSystem(firewalld='active', ufw='inactive')
        system.fail[('/usr/sbin/ufw', 'status', 'numbered')] = 1
        result = self.execute(system)
        self.assertEqual(result['status'], 'error')
        self.assertFalse(result['mutation_attempted'])
        self.assertEqual(system.runtime, {})

    def test_timeout_and_os_error_are_public_safe_and_bounded(self):
        for failure in (subprocess.TimeoutExpired('sensitive arbitrary command', 20, output='unrelated-secret'),
                        OSError('private firewall diagnostics')):
            with self.subTest(failure=type(failure).__name__):
                system = FirewallSystem(ufw='active')
                system.fail[('/usr/sbin/ufw', 'status', 'numbered')] = failure
                result = self.execute(system)
                self.assertEqual(result['status'], 'error')
                self.assertFalse(result['mutation_attempted'])
                if isinstance(failure, subprocess.TimeoutExpired):
                    self.assertTrue(result['error']['timeout'])

    def test_timeout_during_add_reports_possible_partial_application(self):
        system = FirewallSystem(firewalld='active')
        key = ('/usr/bin/firewall-cmd', '--zone=public', '--add-port=8443/tcp')
        system.fail[key] = subprocess.TimeoutExpired('firewall-cmd', 20)
        result = self.execute(system)
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['partial_application_possible'])
        self.assertTrue(result['error']['timeout'])

    def test_insecure_executable_or_ancestor_fails_before_execution(self):
        system = FirewallSystem(firewalld='active', ufw='inactive')
        with patch('firewall.linux_root'), patch('firewall.shutil.which', side_effect=system.which), \
                patch('firewall.trusted_file', side_effect=SafetyError('writable ancestor')), \
                patch('firewall.subprocess.run') as run:
            result = firewall.ensure_port(8443)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error']['reason'], 'untrusted_executable')
        run.assert_not_called()

    def test_second_manager_executable_is_validated_before_first_manager_is_changed(self):
        system = FirewallSystem(firewalld='active', ufw='inactive')
        def trust(path, executable):
            if path.endswith('/ufw'):
                raise SafetyError('second executable has replaceable parent')
            return path
        with patch('firewall.linux_root'), patch('firewall.shutil.which', side_effect=system.which), \
                patch('firewall.trusted_file', side_effect=trust), patch('firewall.subprocess.run') as run:
            result = firewall.ensure_port(8443)
        self.assertEqual(result['status'], 'error')
        self.assertFalse(result['mutation_attempted'])
        self.assertEqual(result['error']['operation'], 'detect:ufw')
        run.assert_not_called()

    def test_fixed_environment_no_shell_and_no_broad_mutations(self):
        for system in (FirewallSystem(firewalld='active'), FirewallSystem(ufw='active')):
            with self.subTest(manager=system.firewalld or system.ufw):
                self.execute(system)
                for options in system.options:
                    self.assertFalse(options['shell'])
                    self.assertEqual(options['stdin'], subprocess.DEVNULL)
                    self.assertGreater(options['timeout'], 0)
                    self.assertLessEqual(options['timeout'], 30)
                    self.assertEqual(options['env'], {'PATH': firewall.SYSTEM_PATH, 'LC_ALL': 'C', 'LANG': 'C'})
                forbidden = {'--reload', '--complete-reload', '--runtime-to-permanent', '--set-default-zone',
                             'enable', 'disable', 'reset', 'default', 'reload', 'delete', 'flush', 'iptables-save', 'nft'}
                self.assertFalse(any(forbidden.intersection(cmd) for cmd in system.commands))


if __name__ == '__main__':
    unittest.main(verbosity=2)
