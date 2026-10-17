#!/usr/bin/env python3
"""Observe one externally coordinated real Admin property update per phase.

Root-only, private manifest/state. This helper does not call Admin, change
credentials, authenticate WSS, restart units or alter the pinned Node binary.
The panel fixture must finish its guarded HTTP apply/restore between arm/check.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

from node_runner import (LabError, Runner, ensure, observer_user_update_evidence,
                         private_json, write_json)


def observe(runner, action, phase):
    path = runner.base / ("admin-property-" + phase + ".private.json")
    ensure(runner.state.get("phase") == "new_install", "admin_property_probe_requires_new_install")
    runner.ws_check()
    units = runner.active_units()
    for service in units:
        runner.assert_owned_unit(service)
    pids = {service: runner.unit_state(service)["MainPID"] for service in units}
    ensure(len(pids) == 2, "both_cold_install_units_required")
    if action == "arm":
        ensure(not path.exists(), "admin_property_phase_already_armed")
        cursors = {}
        for service in pids:
            output = runner.command(["journalctl", "--unit", service, "--lines=0", "--show-cursor", "--no-pager"],
                                    "admin_property_cursor").stdout.decode()
            match = re.search(r"^-- cursor: (.+)$", output, re.MULTILINE)
            ensure(match is not None, "admin_property_journal_cursor_missing")
            cursors[service] = match[1].strip()
        write_json(path, {"schema": 1, "marker": runner.state["marker"], "phase": phase, "pids": pids,
                          "uuid_sha256": hashlib.sha256(runner.current_uuid().encode()).hexdigest(),
                          "cursors": cursors, "status": "ARMED"})
        return {"result": "ARMED", "phase": phase, "unit_count": 2}
    saved = private_json(path)
    ensure(saved.get("schema") == 1 and saved.get("marker") == runner.state["marker"] and
           saved.get("phase") == phase and saved.get("status") == "ARMED", "admin_property_arm_owner_mismatch")
    ensure(saved["pids"] == pids, "node_restarted_during_admin_property_update")
    refreshed = runner.callback("credentials-refresh")
    runner.validate_credentials(refreshed)
    ensure(refreshed == runner.credential and saved["uuid_sha256"] == hashlib.sha256(runner.current_uuid().encode()).hexdigest(),
           "admin_property_update_changed_private_credentials")
    deadline = time.monotonic() + 60
    while True:
        messages = []
        for service, cursor in saved["cursors"].items():
            output = runner.command(["journalctl", "--unit", service, "--after-cursor", cursor, "--no-pager", "--output=json"],
                                    "admin_property_ws_journal").stdout
            ensure(len(output) <= 4 * 1024 * 1024, "admin_property_journal_too_large")
            messages.extend(json.loads(line).get("MESSAGE") for line in output.decode().splitlines() if line.strip())
        deltas, full, disconnected = observer_user_update_evidence(messages, runner.nodes)
        ensure(not disconnected, "admin_property_ws_disconnect_or_rest_reconciliation_observed")
        if deltas == {row["id"] for row in runner.nodes if row["mode"] == "node"} and full == {
                row["id"] for row in runner.nodes if row["mode"] == "machine"}:
            break
        ensure(time.monotonic() < deadline, "admin_property_sync_events_not_observed")
        time.sleep(1)
    for row in runner.nodes:
        runner.probe(row, runner.current_uuid(), 16384)
    ensure(pids == {service: runner.unit_state(service)["MainPID"] for service in units},
           "node_restarted_during_admin_property_update")
    runner.ws_check()
    saved.update(status="PASS", traditional_delta_binding_count=len(deltas), machine_full_binding_count=len(full))
    write_json(path, saved)
    runner.record("actual-admin-http-user-property-" + phase + "-redis-wss-four-proxies-uuid-and-node-pids-unchanged",
                  traditional_ws_delta_binding_count=len(deltas), machine_mailbox_full_user_binding_count=len(full),
                  binding_count=4, uuid_unchanged=True, node_pids_unchanged=True,
                  ws_disconnect_or_rest_reconciliation_observed=False,
                  observation_helper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    return {"result": "PASS", "phase": phase, "traditional_delta_binding_count": len(deltas), "machine_full_binding_count": len(full)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--action", choices=["arm", "check"], required=True)
    parser.add_argument("--phase", choices=["apply", "restore"], required=True)
    args = parser.parse_args()
    runner = None
    try:
        runner = Runner(args.manifest)
        with runner.lock():
            result = observe(runner, args.action, args.phase)
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except BaseException as error:
        reason = str(error) if isinstance(error, LabError) and re.fullmatch(r"[a-zA-Z0-9_]+", str(error)) else "private_admin_property_observation_failed"
        # Leave owned nodes running so the panel fixture can always restore its
        # original property before the parent invokes normal runner cleanup.
        if runner and runner.state:
            runner.state.update(failed=True, result="FAIL", failure_reason=reason)
            runner.save()
        print(json.dumps({"result": "FAIL", "phase": args.phase, "reason": reason}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
