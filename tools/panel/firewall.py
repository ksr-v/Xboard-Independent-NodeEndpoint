"""Narrow, persistent TCP allow rules for an already installed firewall.

``ensure_port(port)`` is a mutation entry point, to be called only after the
endpoint was installed successfully. It never installs or enables a manager,
changes a default policy, reloads a firewall, or saves arbitrary kernel rules.
Only manager configuration is verified; external reachability is not tested.

Invalid arguments/non-Root callers raise SafetyError. Operational failures are
JSON-safe results, including whether a partial change may need inspection.
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess

from manager import linux_root, trusted_file
from pki import SafetyError


SYSTEM_PATH = '/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/sbin:/usr/local/bin'
COMMAND_TIMEOUT = 20
ZONE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z')


class _Failure(Exception):
    def __init__(self, reason, operation, returncode=None, timeout=False):
        self.detail = {'reason': reason, 'operation': operation}
        if returncode is not None:
            self.detail['returncode'] = returncode
        if timeout:
            self.detail['timeout'] = True
        super().__init__(reason)


def _find(name):
    """Use a fixed system PATH and reject replaceable executable ancestors."""
    selected = shutil.which(name, path=SYSTEM_PATH)
    if selected is None:
        return None
    try:
        return str(trusted_file(selected, executable=True))
    except (SafetyError, OSError) as exc:
        raise _Failure('untrusted_executable', 'detect:' + name) from exc


def _run(binary, args, operation, accepted=(0,)):
    try:
        result = subprocess.run(
            [binary, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=COMMAND_TIMEOUT, shell=False,
            env={'PATH': SYSTEM_PATH, 'LC_ALL': 'C', 'LANG': 'C'},
            text=True, encoding='utf-8', errors='replace', check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise _Failure('command_timeout', operation, timeout=True) from exc
    except OSError as exc:
        raise _Failure('command_unavailable', operation) from exc
    if result.returncode not in accepted:
        # Firewall diagnostics can disclose unrelated local policy; never
        # serialize raw stdout/stderr or an arbitrary exception message.
        raise _Failure('command_failed', operation, result.returncode)
    return result


def _ufw_state(output):
    first = next((line.strip() for line in output.splitlines() if line.strip()), '')
    if first == 'Status: active':
        return 'active'
    if first == 'Status: inactive':
        return 'inactive'
    raise _Failure('unrecognized_manager_state', 'ufw:status')


def _zones(default, active):
    default = default.strip()
    if not ZONE.fullmatch(default):
        raise _Failure('invalid_zone_output', 'firewalld:default_zone')
    result = [default]
    current = None
    for line in active.splitlines():
        if not line.strip():
            continue
        if line[0].isspace():
            if current is None or not line.strip().startswith(('interfaces:', 'sources:')):
                raise _Failure('invalid_zone_output', 'firewalld:active_zones')
            continue
        current = line.strip()
        if not ZONE.fullmatch(current):
            raise _Failure('invalid_zone_output', 'firewalld:active_zones')
        if current not in result:
            result.append(current)
        if len(result) > 128:
            raise _Failure('too_many_active_zones', 'firewalld:active_zones')
    return result


def _firewalld(binary, port, result):
    default = _run(binary, ['--get-default-zone'], 'firewalld:default_zone').stdout
    active = _run(binary, ['--get-active-zones'], 'firewalld:active_zones').stdout
    zones = _zones(default, active)
    result['zones'] = zones
    # Query every required scope before the first change, so a bad zone or
    # unreadable permanent configuration cannot cause an avoidable partial run.
    missing = []
    for zone in zones:
        for scope in ('runtime', 'permanent'):
            args = (['--permanent'] if scope == 'permanent' else []) + ['--zone=' + zone]
            operation = f'firewalld:{zone}:{scope}'
            query = _run(binary, [*args, f'--query-port={port}/tcp'], operation + ':query', (0, 1))
            if query.returncode == 1:
                missing.append((zone, scope, args))
    for zone, scope, args in missing:
        operation = f'firewalld:{zone}:{scope}'
        result['mutation_attempted'] = True
        _run(binary, [*args, f'--add-port={port}/tcp'], operation + ':add')
        result['changes'].append({'manager': 'firewalld', 'zone': zone, 'scope': scope, 'action': 'add_port'})
    # Verify both scopes even when an existing rule made all mutations a no-op.
    for zone in zones:
        for scope in ('runtime', 'permanent'):
            args = (['--permanent'] if scope == 'permanent' else []) + ['--zone=' + zone]
            _run(binary, [*args, f'--query-port={port}/tcp'], f'firewalld:{zone}:{scope}:verify')
    result.update(status='ensured', active=True, persistent=True, runtime_applied=True)


def _saved_ufw_allow(output, port):
    """Recognize only an unrestricted single-port incoming TCP allow.

    UFW's added report normalizes the CLI rule. A source/interface restriction,
    outgoing rule, service profile, port range or UDP rule must not satisfy it.
    No returned command is executed.
    """
    for line in output.splitlines():
        if not line.strip().startswith('ufw '):
            continue
        try:
            words = shlex.split(line.strip())
        except ValueError as exc:
            raise _Failure('unrecognized_saved_rule', 'ufw:show_added') from exc
        if 'comment' in words:
            index = words.index('comment')
            if len(words) != index + 2:
                raise _Failure('unrecognized_saved_rule', 'ufw:show_added')
            words = words[:index]
        if words in (['ufw', 'allow', f'{port}/tcp'], ['ufw', 'allow', 'in', f'{port}/tcp'],
                     ['ufw', 'allow', 'proto', 'tcp', 'from', 'any', 'to', 'any', 'port', str(port)],
                     ['ufw', 'allow', 'in', 'proto', 'tcp', 'from', 'any', 'to', 'any', 'port', str(port)]):
            return True
    return False


def _ufw_priority(output, port):
    """Conservatively check both listed IP families, never move old rules.

    UFW skips duplicate inserts, so a prior unrestricted allow may remain below
    a deny. Only preceding ALLOW IN/OUT/FWD rules can safely be ignored without
    interpreting every UFW address/port/profile syntax. Any other preceding
    rule requires manual review, even if it might target an unrelated port.
    Custom before/after rules and external firewall layers are outside scope.
    """
    rows = {4: [], 6: []}
    for line in output.splitlines():
        if not line.lstrip().startswith('['):
            continue
        match = re.fullmatch(r'\s*\[\s*\d+\]\s+(.+)', line)
        if not match:
            raise _Failure('unrecognized_rule_order', 'ufw:priority')
        body = match[1].split('#', 1)[0].strip()
        family = 6 if '(v6)' in body else 4
        rows[family].append(body)
    families = [4] + ([6] if rows[6] else [])
    for family in families:
        seen_blocker = False
        found = False
        for body in rows[family]:
            normalized = ' '.join(body.replace('(v6)', '').split())
            if normalized == f'{port}/tcp ALLOW IN Anywhere':
                if seen_blocker:
                    return False, families
                found = True
                break
            if not re.search(r'\sALLOW (?:IN|OUT|FWD)\s', normalized):
                seen_blocker = True
        if not found:
            return False, families
    return True, families


def _ufw(binary, port, state, result):
    before = _run(binary, ['show', 'added'], 'ufw:show_added:before').stdout
    # UFW handles duplicate rules itself. Inserting rather than appending makes
    # a new rule precede user denies without deleting or rewriting those rules.
    # UFW 0.36 rejects insert position 1 when no rules exist. Only its exact
    # C-locale empty report permits an ordinary add; never retry a failed
    # insert with an append when existing rules or unknown output are present.
    empty = before.strip() == "Added user rules (see 'ufw status' for running firewall):\n(None)"
    args = ([] if empty else ['insert', '1']) + ['allow', 'in', f'{port}/tcp', 'comment', 'NodeEndpoint']
    result['mutation_attempted'] = True
    _run(binary, args, 'ufw:add_empty' if empty else 'ufw:insert')
    after = _run(binary, ['show', 'added'], 'ufw:show_added:after').stdout
    if after != before:
        result['changes'].append({'manager': 'ufw', 'scope': 'persistent', 'action': 'insert_allow_tcp'})
    if not _saved_ufw_allow(after, port):
        raise _Failure('persistent_rule_not_verified', 'ufw:verify_saved')
    status = _run(binary, ['status', 'numbered'], 'ufw:status:after').stdout
    if _ufw_state(status) != state:
        raise _Failure('manager_state_changed_during_operation', 'ufw:status:after')
    result.update(active=state == 'active', persistent=True, runtime_applied=False,
                  requested_rule_comment='NodeEndpoint', rule_scope='ufw_user_rules')
    if state == 'inactive':
        result.update(status='saved_inactive', reason='ufw_remains_inactive_not_enabled',
                      notice='端口规则已持久保存，但 UFW 当前未启用；本工具没有启用防火墙，未验证公网连通性。')
        return
    priority, families = _ufw_priority(status, port)
    result['listed_ip_families'] = families
    if not priority:
        result.update(status='manual_required', reason='ufw_existing_rule_precedence_requires_review')
        return
    result.update(status='ensured', runtime_applied=True, user_rule_priority_verified=True)


def ensure_port(port):
    """Ensure one TCP port via a supported manager; return JSON-safe evidence.

    Status values: ``ensured`` (active manager's configuration verified),
    ``saved_inactive`` (UFW rule saved, firewall stays off), ``manual_required``
    or ``error``. None of them asserts public network reachability. An active
    firewalld takes precedence over an installed inactive UFW; both active is a
    conflict and causes no mutation. Operational errors retain a partial-change
    marker; they never flush or attempt to roll back an unrelated ruleset.
    """
    if type(port) is not int or not 1 <= port <= 65535:
        raise SafetyError('Firewall TCP port must be an integer from 1 through 65535')
    linux_root()
    result = {'status': 'manual_required', 'manager': 'none', 'port': port, 'protocol': 'tcp',
              'network_reachability': 'not_tested', 'changes': [], 'mutation_attempted': False,
              'changed': False, 'observed_managers': {},
              'notice': '需要管理员人工核对防火墙；未验证公网连通性。'}
    try:
        firewalld, ufw = _find('firewall-cmd'), _find('ufw')
        firewalld_state, ufw_state = 'absent', 'absent'
        if firewalld:
            state = _run(firewalld, ['--state'], 'firewalld:state', (0, 252))
            firewalld_state = 'inactive' if state.returncode == 252 else 'active'
            if state.returncode == 0 and state.stdout.strip() != 'running':
                raise _Failure('unrecognized_manager_state', 'firewalld:state')
        if ufw:
            ufw_state = _ufw_state(_run(ufw, ['status', 'numbered'], 'ufw:status').stdout)
        result['observed_managers'] = {'firewalld': firewalld_state, 'ufw': ufw_state}
        if firewalld_state == ufw_state == 'active':
            result.update(manager='firewalld+ufw', reason='multiple_active_firewall_managers',
                          notice='同时检测到两个已启用的防火墙管理器，未改动规则；请管理员人工核对。')
        elif firewalld_state == 'active':
            result['manager'] = 'firewalld'
            _firewalld(firewalld, port, result)
        elif ufw:
            result['manager'] = 'ufw'
            _ufw(ufw, port, ufw_state, result)
        else:
            result.update(reason='no_active_supported_firewall_manager',
                          raw_tools_present=[name for name in ('nft', 'iptables', 'ip6tables')
                                             if shutil.which(name, path=SYSTEM_PATH)],
                          raw_rules_modified=False)
    except _Failure as exc:
        result.update(status='error', error=exc.detail,
                      partial_application_possible=result['mutation_attempted'],
                      notice=('防火墙操作未完成，指定端口规则可能已部分应用；请核对错误阶段。'
                              if result['mutation_attempted'] else '防火墙检测失败，尚未改动规则；请核对错误阶段。'))
    if result['status'] == 'ensured':
        result['notice'] = '指定 TCP 端口的管理器规则已验证并持久保存；未验证公网连通性或上游安全组。'
    result['changed'] = bool(result['changes'])
    return result
