"""Synthetic-only regressions for the private final ZIP scanner; no real lab files."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
import zlib


def main():
    workspace = Path(__file__).resolve().parents[2]
    scratch = (workspace / ".runtime" / "e2e").resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    php = sys.argv[1]
    ini = sys.argv[2]
    script = workspace / "tests/e2e/package_secret_scan.php"
    run = "scanner-synthetic-test"
    entropy = "A" * 40
    retired = entropy + f"{zlib.crc32(entropy.encode()) & 0xffffffff:08x}"
    other = "B" * 40
    current = other + f"{zlib.crc32(other.encode()) & 0xffffffff:08x}"
    exact = ["synthetic-node-token", "synthetic-machine-token", "synthetic-user-uuid"]
    checks = 0
    with tempfile.TemporaryDirectory(prefix="secret-scan-selftest-", dir=scratch) as folder:
        stage = Path(folder).resolve()
        # Verify the complete absolute target before TemporaryDirectory's own cleanup.
        assert stage.is_relative_to(scratch) and stage != scratch
        (stage / ".node-endpoint-e2e-owner").write_text("synthetic-only", encoding="ascii")
        private = {"schema": 1, "run": run, "exact_values": exact,
                   "sanctum_secret_digests": [hashlib.sha256(value.encode()).hexdigest() for value in (current, retired)],
                   "sanctum_prefixes": [""]}
        path = stage / (run + "-secret-scan-values.json")
        path.write_text(json.dumps(private), encoding="ascii")
        for label, body, expected, corrupt_pin in [
                ("clean", "harmless synthetic package", "PASS", False),
                ("exact", exact[0], "FAIL", False),
                # Prefix/suffix deliberately touch the candidate; overlapping scan
                # must still detect a retired token without retaining its plaintext.
                ("retired", "prefix" + retired + "suffix", "FAIL", False),
                ("pin", "harmless synthetic package", "ERROR", True)]:
            packages = []
            for index in range(3):
                target = stage / f"{label}-{index}.zip"
                with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("synthetic.txt", body)
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                packages.append({"file": target.name, "sha256": "0" * 64 if corrupt_pin and index == 0 else digest})
            manifest = stage / "manifest.json"
            manifest.write_text(json.dumps({"schema": 1, "run": run, "packages": packages}), encoding="ascii")
            for private_file in stage.iterdir():
                private_file.chmod(0o600)
            process = subprocess.run([php, "-c", ini, str(script), "scan", "--stage=" + str(stage),
                                      "--run=" + run, "--manifest-file=manifest.json"],
                                     capture_output=True, timeout=15, check=False)
            result = json.loads(process.stdout)
            assert result["status"] == expected, label
            assert process.returncode == (0 if expected == "PASS" else 1), label
            assert not any(value.encode() in process.stdout for value in [retired, current, *exact]), label
            checks += 1
    print(f"{checks} synthetic scanner cases PASS (clean/exact leak/retired digest leak/wrong pin)")


if __name__ == "__main__":
    main()
