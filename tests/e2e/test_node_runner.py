#!/usr/bin/env python3
"""Local protocol/manifest checks only: no VM, systemd, credentials or DB."""
import importlib.util
import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import uuid

MODULE_PATH = Path(__file__).with_name("node_runner.py")
module_spec = importlib.util.spec_from_file_location("node_e2e_runner", MODULE_PATH)
runner = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(runner)


class ProxyPeer:
    def __init__(self, protocol, valid_uuid, reject=False):
        self.protocol, self.valid_uuid, self.reject = protocol, valid_uuid, reject
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.errors = []
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            with self.listener, self.listener.accept()[0] as connection:
                connection.settimeout(3)
                if self.protocol == "vless":
                    request = runner.exact(connection, 26)
                    assert request[:1] == b"\x00" and request[1:17] == uuid.UUID(self.valid_uuid).bytes
                    assert request[17:19] == b"\x00\x01" and request[21:] == b"\x01\x7f\x00\x00\x01"
                    if self.reject:
                        return
                    connection.sendall(b"\x00\x00")
                else:
                    assert runner.exact(connection, 3) == b"\x05\x01\x02"
                    connection.sendall(b"\x05\x02")
                    assert runner.exact(connection, 1) == b"\x01"
                    username = runner.exact(connection, runner.exact(connection, 1)[0])
                    password = runner.exact(connection, runner.exact(connection, 1)[0])
                    assert username == password == self.valid_uuid.encode()
                    connection.sendall(b"\x01\x01" if self.reject else b"\x01\x00")
                    if self.reject:
                        return
                    request = runner.exact(connection, 10)
                    assert request[:8] == b"\x05\x01\x00\x01\x7f\x00\x00\x01"
                    connection.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x01")
                while True:
                    payload = connection.recv(8192)
                    if not payload:
                        return
                    connection.sendall(payload)
        except BaseException as error:
            self.errors.append(type(error).__name__)

    def finish(self):
        self.thread.join(5)
        assert not self.thread.is_alive() and not self.errors


class ProxyTests(unittest.TestCase):
    def test_both_raw_clients_large_stream_and_redacted_result(self):
        for protocol in ("vless", "socks"):
            with self.subTest(protocol=protocol):
                credential = str(uuid.uuid4())
                peer = ProxyPeer(protocol, credential)
                result = runner.proxy_probe({"host": "127.0.0.1", "port": peer.port, "echo_port": 32185,
                         "protocol": protocol, "uuid": credential, "bytes": 1024 * 1024, "hold_seconds": 0,
                         "node_id": 1, "expected_source": "127.0.0.1"})
                peer.finish()
                self.assertEqual(result["bytes_uploaded"], 1024 * 1024)
                self.assertEqual(result["bytes_downloaded"], 1024 * 1024)
                self.assertTrue(result["cross_vm_source_validated"])
                self.assertNotIn(credential, json.dumps(result))

    def test_both_auth_rejections_fail_without_credential_message(self):
        for protocol in ("vless", "socks"):
            with self.subTest(protocol=protocol):
                credential = str(uuid.uuid4())
                peer = ProxyPeer(protocol, credential, reject=True)
                with self.assertRaises((runner.LabError, OSError)) as context:
                    runner.proxy_probe({"host": "127.0.0.1", "port": peer.port, "echo_port": 32185,
                       "protocol": protocol, "uuid": credential, "bytes": 16, "hold_seconds": 0})
                peer.finish()
                self.assertNotIn(credential, str(context.exception))

    def test_non_loopback_target_is_rejected_before_connect(self):
        with self.assertRaisesRegex(runner.LabError, "echo_target_must_be_loopback"):
            runner.proxy_probe({"host": "127.0.0.1", "target": "192.168.1.1", "port": 32181,
                                "echo_port": 32185, "protocol": "vless", "uuid": str(uuid.uuid4())})


class BoundaryTests(unittest.TestCase):
    def test_empty_owned_dropin_directory_after_rollback_can_be_removed(self):
        with tempfile.TemporaryDirectory() as base:
            directory = Path(base) / 'xboard-node.service.d'
            directory.mkdir()
            with patch.object(runner.ne, 'require_root_ancestry') as guard:
                runner.remove_owned_standard_dropin(directory, b'owned trust')
            guard.assert_called_once_with(directory.absolute())
            self.assertFalse(directory.exists())

    def test_foreign_or_modified_dropin_is_preserved(self):
        for filename, contents in [('foreign.conf', b'foreign'),
                                   ('90-node-endpoint.conf', b'changed')]:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as base:
                directory = Path(base) / 'xboard-node.service.d'
                directory.mkdir()
                path = directory / filename
                path.write_bytes(contents)
                with patch.object(runner.ne, 'require_root_ancestry'), self.assertRaisesRegex(
                        runner.LabError, 'foreign_standard_dropin'):
                    runner.remove_owned_standard_dropin(directory, b'owned trust')
                self.assertEqual(path.read_bytes(), contents)

    def test_exact_owned_dropin_is_removed_and_ancestry_rejection_preserves_it(self):
        with tempfile.TemporaryDirectory() as base:
            directory = Path(base) / 'xboard-node.service.d'
            directory.mkdir()
            path = directory / '90-node-endpoint.conf'
            path.write_bytes(b'owned trust')
            with patch.object(runner.ne, 'require_root_ancestry', side_effect=runner.ne.SafetyError('unsafe')):
                with self.assertRaises(runner.ne.SafetyError):
                    runner.remove_owned_standard_dropin(directory, b'owned trust')
            self.assertEqual(path.read_bytes(), b'owned trust')
            with patch.object(runner.ne, 'require_root_ancestry'):
                runner.remove_owned_standard_dropin(directory, b'owned trust')
            self.assertFalse(directory.exists())

    def test_native_observer_node_delta_and_machine_mailbox_full_events_are_distinct(self):
        bindings = self.credentials()["nodes"]
        messages = ["INFO [" + row["protocol"] + ":" + str(row["port"]) + "] " +
                    ("users delta: add, 1 users" if row["mode"] == "node" else "users updated, 1 users") for row in bindings]
        deltas, full, disconnected = runner.observer_user_update_evidence(messages, bindings)
        self.assertEqual(deltas, {1, 2})
        self.assertEqual(full, {3, 4})
        self.assertFalse(disconnected)
        wrong = [message.replace("users delta: add, 1 users", "users updated, 1 users") for message in messages]
        self.assertFalse(runner.observer_user_update_evidence(wrong, bindings)[0])
        self.assertTrue(runner.observer_user_update_evidence(messages + ["ws disconnected"], bindings)[2])

    def test_reconnect_same_certificate_is_valid_and_trust_changes_still_fail(self):
        instance = runner.Runner.__new__(runner.Runner)
        instance.state = {"last_leaf_sha256": "previous-leaf"}
        instance.command = Mock()
        instance.assert_owned_unit = Mock()
        instance.runtime_check = Mock()
        instance.ws_check = Mock()
        instance.observe_tls_leaf = Mock(return_value="previous-leaf")
        instance.record = Mock()
        with patch.object(runner, "digest_file", side_effect=["trust", "trust"]):
            instance.restart_check()
        self.assertFalse(instance.record.call_args.kwargs["leaf_changed"])
        instance.record.reset_mock()
        with patch.object(runner, "digest_file", side_effect=["trust", "modified-trust"]):
            with self.assertRaisesRegex(runner.LabError, "trust_changed_during_certificate_restart"):
                instance.restart_check()
        instance.record.assert_not_called()

    def test_exclusive_ws_log_signature_maps_protocol_port_without_exposing_messages(self):
        bindings = self.credentials()["nodes"]
        messages = ["01:02:03 INFO [" + row["protocol"] + ":" + str(row["port"]) + "] users updated, 1 users" for row in bindings]
        seen, disconnected = runner.ws_user_update_evidence(messages, bindings)
        self.assertEqual(seen, {1, 2, 3, 4})
        self.assertFalse(disconnected)
        seen, disconnected = runner.ws_user_update_evidence(["INFO [vless:32181] users updated: +1 -1"], bindings)
        self.assertFalse(seen)
        seen, disconnected = runner.ws_user_update_evidence(messages + ["INFO [socks:32183] ws disconnected"], bindings)
        self.assertTrue(disconnected)

    def test_temporary_auth_exception_needs_all_three_exact_guards(self):
        value = {"global_settings_unchanged": False, "test_auth_active": True,
                 "test_auth_setting_matches_owned": True, "other_global_settings_unchanged": True}
        self.assertTrue(runner.panel_global_guard(value))
        for field in ("test_auth_active", "test_auth_setting_matches_owned", "other_global_settings_unchanged"):
            invalid = dict(value)
            invalid[field] = False
            self.assertFalse(runner.panel_global_guard(invalid))
        self.assertTrue(runner.panel_global_guard({"global_settings_unchanged": True}))

    def test_nft_dynamic_counters_do_not_change_guard_but_policy_does(self):
        first = {"nftables": [{"metainfo": {"version": "1"}}, {"rule": {"family": "ip", "table": "filter",
                 "handle": 10, "expr": [{"counter": {"packets": 1, "bytes": 10}}, {"accept": None}]}}]}
        second = json.loads(json.dumps(first))
        second["nftables"][1]["rule"]["expr"][0]["counter"] = {"packets": 20, "bytes": 2000}
        self.assertEqual(runner.stable_nft(first), runner.stable_nft(second))
        second["nftables"][1]["rule"]["expr"][1] = {"drop": None}
        self.assertNotEqual(runner.stable_nft(first), runner.stable_nft(second))
        self.assertNotEqual(runner.stable_nft({"policy": {"bytes": 10}}), runner.stable_nft({"policy": {"bytes": 20}}))

    def credentials(self):
        return {"schema": 1, "run_id": "local-only", "node_token": "synthetic-global-token",
                "machine": {"id": 1, "token": "synthetic-machine-token"}, "user": {"id": 2, "uuid": str(uuid.uuid4())},
                "nodes": [{"id": 1, "mode": "node", "protocol": "vless", "kernel": "xray", "port": 32181},
                          {"id": 2, "mode": "node", "protocol": "vless", "kernel": "xray", "port": 32182},
                          {"id": 3, "mode": "machine", "protocol": "socks", "kernel": "singbox", "port": 32183},
                          {"id": 4, "mode": "machine", "protocol": "socks", "kernel": "singbox", "port": 32184}]}

    def test_canonical_panel_helper_credentials_supported(self):
        instance = runner.Runner.__new__(runner.Runner)
        instance.run_id = "local-only"
        instance.validate_credentials(self.credentials())

    def test_wrong_identity_duplicate_port_and_wrong_kernel_rejected(self):
        instance = runner.Runner.__new__(runner.Runner)
        instance.run_id = "local-only"
        for alteration in ("run", "port", "kernel"):
            value = self.credentials()
            if alteration == "run":
                value["run_id"] = "wrong"
            elif alteration == "port":
                value["nodes"][1]["port"] = value["nodes"][0]["port"]
            else:
                value["nodes"][2]["kernel"] = "xray"
            with self.subTest(alteration=alteration), self.assertRaises(runner.LabError):
                instance.validate_credentials(value)


if __name__ == "__main__":
    unittest.main()
