#!/usr/bin/env python3
"""Real Linux permission and cross-process flock acceptance in one new directory.

Requires Root on an explicitly authorized test host. No services, networking,
credentials, existing application paths or parent permissions are changed.
"""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tools/node"))
import node_endpoint as ne


class CheckFailed(RuntimeError):
    pass


def ensure(condition, code):
    if not condition:
        raise CheckFailed(code)


def lock_holder(config, connection):
    """Acquire the production lock and hold it until an IPC release command."""
    try:
        with ne.migration_lock(config):
            connection.send("held")
            ensure(connection.recv() == "release", "invalid_release_command")
        connection.send("released")
    except (OSError, EOFError, ne.SafetyError, CheckFailed):
        try:
            connection.send("worker_failed")
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def lock_attempt(config, connection):
    try:
        with ne.migration_lock(config):
            connection.send("acquired")
    except ne.SafetyError:
        connection.send("rejected")
    except OSError:
        connection.send("worker_failed")
    finally:
        connection.close()


class PermissionAcceptance:
    def __init__(self, base):
        ensure(sys.platform == "linux" and os.geteuid() == 0, "linux_root_required")
        candidate = Path(base)
        ensure(candidate.is_absolute() and ".." not in candidate.parts, "absolute_normal_path_required")
        self.base = ne.safe_path(candidate, existing=False)
        ne.require_root_ancestry(self.base)
        ensure(self.base.parent.is_dir() and not self.base.exists(), "new_private_child_directory_required")
        self.marker = secrets.token_hex(16)
        self.base.mkdir(mode=0o700)
        ne.atomic_write(self.base / ".fixture-owner.json", json.dumps({"schema": 1, "marker": self.marker}).encode())
        self.checks = []
        self.processes = []
        self.pipes = []
        self.context = multiprocessing.get_context("spawn")

    def record(self, name, passed=True):
        self.checks.append({"name": name, "passed": bool(passed)})
        print(json.dumps({"check": name, "passed": bool(passed)}), flush=True)

    def expect_rejected(self, name, action):
        try:
            action()
        except ne.SafetyError:
            self.record(name)
            return
        raise CheckFailed(name + ":unexpected_acceptance")

    def file(self, name, mode=0o600):
        path = self.base / name
        ne.atomic_write(path, b"synthetic fixture input only\n", mode)
        return path

    def start_worker(self, target, config):
        parent, child = self.context.Pipe()
        process = self.context.Process(target=target, args=(str(config), child))
        process.start()
        child.close()
        self.processes.append(process)
        self.pipes.append(parent)
        return process, parent

    def receive(self, connection):
        ensure(connection.poll(10), "worker_ipc_timeout")
        return connection.recv()

    def join(self, process):
        process.join(10)
        ensure(not process.is_alive() and process.exitcode == 0, "worker_exit_failed")

    def attempt(self, config, expected):
        process, connection = self.start_worker(lock_attempt, config)
        ensure(self.receive(connection) == expected, "unexpected_cross_process_lock_result")
        self.join(process)

    def flock_acceptance(self):
        directory = self.base / "flock"
        directory.mkdir(mode=0o700)
        config = directory / "config.yml"
        ne.atomic_write(config, b"synthetic: true\n")
        holder, channel = self.start_worker(lock_holder, config)
        ensure(self.receive(channel) == "held", "holder_did_not_acquire")
        lock_info = (directory / ".node-endpoint.lock").stat()
        ensure(lock_info.st_uid == 0 and stat.S_IMODE(lock_info.st_mode) == 0o600 and lock_info.st_nlink == 1,
               "lock_file_ownership_or_permissions_wrong")
        self.record("production-lock-file-root-owned-private")
        self.attempt(config, "rejected")
        self.record("independent-process-competition-rejected")
        channel.send("release")
        ensure(self.receive(channel) == "released", "graceful_release_not_confirmed")
        self.join(holder)
        self.attempt(config, "acquired")
        self.record("lock-reacquired-after-graceful-release")

        holder, channel = self.start_worker(lock_holder, config)
        ensure(self.receive(channel) == "held", "second_holder_did_not_acquire")
        self.attempt(config, "rejected")
        holder.terminate()  # Only the child process created by this fixture.
        holder.join(10)
        ensure(not holder.is_alive() and holder.exitcode is not None and holder.exitcode < 0,
               "terminated_holder_did_not_exit")
        self.attempt(config, "acquired")
        self.record("os-releases-flock-after-holder-termination")

    def path_acceptance(self):
        ordinary = self.file("private-root-input")
        ensure(ne.safe_path(ordinary, private=True) == ordinary, "safe_private_root_input_refused")
        ne.require_root_ancestry(ordinary)
        self.record("safe-root-private-input-accepted")

        writable = self.base / "group-writable-root-parent"
        writable.mkdir(mode=0o700)
        writable.chmod(0o770)
        child = writable / "private-root-child"
        ne.atomic_write(child, b"synthetic only\n")
        self.expect_rejected("group-writable-root-ancestry-rejected", lambda: ne.require_root_ancestry(child))

        foreign_directory = self.base / "foreign-owner-parent"
        foreign_directory.mkdir(mode=0o700)
        os.chown(foreign_directory, 65534, 65534)
        child = foreign_directory / "root-owned-child"
        ne.atomic_write(child, b"synthetic only\n")
        self.expect_rejected("foreign-owned-ancestry-rejected", lambda: ne.require_root_ancestry(child))

        foreign = self.file("foreign-owned-private-input")
        os.chown(foreign, 65534, 65534)
        self.expect_rejected("other-uid-private-input-rejected", lambda: ne.safe_path(foreign, private=True))

        readable = self.file("group-readable-private-input", 0o640)
        self.expect_rejected("group-readable-private-input-rejected", lambda: ne.safe_path(readable, private=True))

        linked = self.base / "hardlink-input"
        os.link(ordinary, linked)
        self.expect_rejected("hardlinked-private-input-rejected", lambda: ne.safe_path(linked, private=True))

        symbolic = self.base / "symlink-input"
        symbolic.symlink_to(readable.name)
        self.expect_rejected("symlink-private-input-rejected", lambda: ne.safe_path(symbolic, private=True))

        symbolic_parent = self.base / "symlink-parent"
        symbolic_parent.symlink_to(writable.name, target_is_directory=True)
        self.expect_rejected("symlink-parent-rejected", lambda: ne.safe_path(symbolic_parent / child.name, existing=False))

        unsafe_lock_dir = self.base / "unsafe-lock"
        unsafe_lock_dir.mkdir(mode=0o700)
        unsafe_lock = unsafe_lock_dir / ".node-endpoint.lock"
        ne.atomic_write(unsafe_lock, b"", 0o660)
        def acquire_unsafe_lock():
            with ne.migration_lock(unsafe_lock_dir / "config.yml"):
                pass
        self.expect_rejected("group-writable-lock-file-rejected", acquire_unsafe_lock)

    def cleanup_workers(self):
        failures = []
        for process in self.processes:
            if process.is_alive():
                process.terminate()
            process.join(10)
            if process.is_alive():
                process.kill()
                process.join(5)
            if process.is_alive():
                failures.append("owned_child_process_cleanup_failed")
        for connection in self.pipes:
            connection.close()
        return failures

    def run(self):
        failure = None
        try:
            self.flock_acceptance()
            self.path_acceptance()
        except (OSError, EOFError, ValueError, CheckFailed, ne.SafetyError) as error:
            failure = str(error) if isinstance(error, CheckFailed) else "permission_fixture_failed"
        finally:
            cleanup_failures = self.cleanup_workers()
        if failure is None and not cleanup_failures:
            try:
                ensure(json.loads((self.base / ".fixture-owner.json").read_text())["marker"] == self.marker,
                       "private_directory_marker_changed")
                # Retain only public, credential-free acceptance evidence.
                for path in self.base.iterdir():
                    if path.name == ".fixture-owner.json":
                        continue
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
            except (OSError, ValueError, CheckFailed):
                cleanup_failures.append("owned_files_cleanup_failed")
        self.record("owned-processes-and-test-files-cleaned", passed=failure is None and not cleanup_failures)
        report = {"schema": 1, "passed": failure is None and not cleanup_failures,
                  "checks": self.checks, "failure": failure, "cleanup_failures": cleanup_failures,
                  "scope": "real Linux filesystem ownership/modes/links and independent spawned-process production flock; no services or networking"}
        ne.atomic_write(self.base / "report.json", json.dumps(report, indent=2).encode())
        print(json.dumps({"passed": report["passed"], "checks": len(self.checks), "report": str(self.base / "report.json")}), flush=True)
        return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="new private Root-owned directory under an existing safe Root-owned parent")
    args = parser.parse_args()
    try:
        return PermissionAcceptance(args.base).run()
    except (OSError, EOFError, ValueError, CheckFailed, ne.SafetyError):
        print(json.dumps({"passed": False, "failure": "initialization_or_report_failed", "private_base": args.base}), flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
