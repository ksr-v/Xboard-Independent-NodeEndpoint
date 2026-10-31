"""Controlled official-Release transport; no real network or executable runs."""
import copy
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/node"))
import node_endpoint as ne


class Response(io.BytesIO):
    def __init__(self, payload, url):
        super().__init__(payload)
        self.url = url

    def geturl(self):
        return self.url


class ReleaseTransport:
    def __init__(self, architecture="amd64", payload=None):
        binary = bytearray(128)
        binary[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", binary, 18, 62 if architecture == "amd64" else 183)
        self.binary = bytes(binary) if payload is None else payload
        self.name = "xboard-node-linux-" + architecture
        self.latest = "v1.13-orphan.42"
        self.calls = []
        self.edit_metadata = lambda metadata: metadata
        self.sums = (ne.digest(self.binary) + "  " + self.name + "\n").encode()
        self.binary_override = None
        self.redirect = None

    def metadata(self, tag):
        base = ne.NODE_REPOSITORY + "/releases/download/" + tag + "/"
        return self.edit_metadata({"tag_name": tag, "draft": False, "prerelease": False, "assets": [
            {"name": name, "state": "uploaded", "browser_download_url": base + name,
             "digest": "sha256:" + ne.digest(payload)}
            for name, payload in ((self.name, self.binary), ("SHA256SUMS", self.sums))]})

    def open(self, request, timeout=30):
        url = request.full_url if hasattr(request, "full_url") else request
        self.calls.append(url)
        if url.startswith(ne.NODE_RELEASE_API):
            suffix = url[len(ne.NODE_RELEASE_API):]
            tag = self.latest if suffix == "latest" else suffix.removeprefix("tags/")
            return Response(json.dumps(self.metadata(tag)).encode(), self.redirect or url)
        if url.endswith("/SHA256SUMS"):
            payload = self.sums
        elif url.endswith("/" + self.name):
            payload = self.binary if self.binary_override is None else self.binary_override
        else:
            raise OSError("unrecognized controlled URL")
        return Response(payload, self.redirect or "https://release-assets.githubusercontent.com/controlled")


class LatestReleaseTests(unittest.TestCase):
    def test_future_orphan_tags_and_both_architectures_are_resolved_and_verified(self):
        for architecture in ("amd64", "arm64"):
            transport = ReleaseTransport(architecture)
            transport.latest = "v1.13-orphan.999"
            with self.subTest(architecture=architecture), tempfile.TemporaryDirectory() as folder, \
                 patch.object(ne.urllib.request, "urlopen", side_effect=transport.open):
                lock = ne.download_binary(architecture, folder, release="latest")
                self.assertEqual(lock["version"], "v1.13-orphan.999")
                self.assertEqual((Path(folder) / "xboard-node").read_bytes(), transport.binary)
                self.assertTrue(all("v1.13-orphan.9/" not in url for url in transport.calls))

    def test_unpublished_foreign_duplicate_and_conflicting_api_assets_are_refused(self):
        def change(key, value):
            return lambda item: dict(item, **{key: value})

        def asset_change(key, value):
            def edit(item):
                item["assets"][0][key] = value
                return item
            return edit

        mutations = [change("draft", True), change("prerelease", True), change("tag_name", "../orphan.42"),
                     change("assets", []), asset_change("browser_download_url", "https://foreign.example/node"),
                     asset_change("digest", "sha256:" + "0" * 64),
                     lambda item: dict(item, assets=item["assets"] + [copy.deepcopy(item["assets"][0])])]
        for mutation in mutations:
            transport = ReleaseTransport()
            transport.edit_metadata = mutation
            with self.subTest(mutation=mutation), patch.object(ne.urllib.request, "urlopen", side_effect=transport.open):
                with self.assertRaises(ne.SafetyError):
                    ne.resolve_release_lock("latest", "amd64")

    def test_duplicated_or_missing_checksums_and_foreign_redirects_are_refused(self):
        for variant in ("duplicate", "missing", "redirect", "checksum-digest"):
            transport = ReleaseTransport()
            if variant == "duplicate":
                transport.sums *= 2
            elif variant == "missing":
                transport.sums = b"not a checksum\n"
            elif variant == "redirect":
                transport.redirect = "http://foreign.example/metadata"
            else:
                def bad_digest(item):
                    item["assets"][1]["digest"] = "sha256:" + "0" * 64
                    return item
                transport.edit_metadata = bad_digest
            with self.subTest(variant=variant), patch.object(ne.urllib.request, "urlopen", side_effect=transport.open):
                with self.assertRaises(ne.SafetyError):
                    ne.resolve_release_lock("latest", "amd64")

    def test_corrupt_or_wrong_architecture_download_never_creates_executable(self):
        for variant in ("hash", "architecture"):
            transport = ReleaseTransport(payload=b"not an ELF") if variant == "architecture" else ReleaseTransport()
            if variant == "hash":
                transport.binary_override = b"tampered binary"
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as folder, \
                 patch.object(ne.urllib.request, "urlopen", side_effect=transport.open):
                with self.assertRaises(ne.SafetyError):
                    ne.download_binary("amd64", folder, release="latest")
                self.assertFalse((Path(folder) / "xboard-node").exists())

    def prepare(self, output, transport):
        args = SimpleNamespace(output_dir=str(output), release="latest", download=True,
                               confirm_new_identity="UNUSED", panel_url="http://192.0.2.2:8080", allow_http=True,
                               kernel="singbox", architecture="amd64", mode="machine", machine_id=17,
                               node_id=None, node_type=None, allow_rest_fallback=False)
        with patch.object(ne, "read_token", return_value="synthetic-secret"), \
             patch.object(ne, "preflight", return_value={"rest": "passed", "websocket": "passed"}), \
             patch.object(ne.urllib.request, "urlopen", side_effect=transport.open):
            return ne.prepare(args)

    def test_prepare_pins_exact_tag_and_install_verifies_it_after_latest_advances(self):
        transport = ReleaseTransport()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "new-instance"
            prepared = self.prepare(output, transport)
            self.assertEqual(prepared["upstream_version"], "v1.13-orphan.42")
            self.assertEqual(prepared["release_selection"], "latest")
            self.assertIn("selected-release-lock.json", prepared["files"])
            self.assertNotIn("synthetic-secret", json.dumps(prepared))
            transport.latest = "v1.13-orphan.43"
            transport.calls.clear()
            with patch.object(ne.urllib.request, "urlopen", side_effect=transport.open), \
                 patch.object(ne, "run", return_value="xboard-node v1.13-orphan.42 (built controlled)") as version:
                ne.verify_prepared_binary(output, prepared, "amd64")
                version.assert_called_once_with([str(output.absolute() / "xboard-node"), "-v"])
            self.assertIn(ne.NODE_RELEASE_API + "tags/v1.13-orphan.42", transport.calls)
            self.assertNotIn(ne.NODE_RELEASE_API + "latest", transport.calls)
            self.assertEqual((output / "xboard-node").read_bytes(), transport.binary)

    def test_install_rejects_changed_release_hash_binary_or_version_before_service_work(self):
        for variant in ("release-hash", "binary", "version"):
            transport = ReleaseTransport()
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "new-instance"
                prepared = self.prepare(output, transport)
                if variant == "release-hash":
                    transport.binary = transport.binary + b"different"
                    transport.sums = (ne.digest(transport.binary) + "  " + transport.name + "\n").encode()
                elif variant == "binary":
                    (output / "xboard-node").write_bytes(b"tampered")
                with patch.object(ne.urllib.request, "urlopen", side_effect=transport.open), \
                     patch.object(ne, "run", return_value="xboard-node v1.13-orphan.42evil (built controlled)") as command:
                    with self.assertRaises(ne.SafetyError):
                        ne.verify_prepared_binary(output, prepared, "amd64")
                    if variant != "version":
                        command.assert_not_called()

    def test_network_failure_has_no_pinned_fallback_and_leaves_failed_private_stage(self):
        transport = ReleaseTransport()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "new-instance"
            with patch.object(ne, "release_download", side_effect=ne.SafetyError("controlled offline failure")):
                with self.assertRaises(ne.SafetyError):
                    self.prepare(output, transport)
            self.assertTrue((output / "PREPARATION_FAILED").is_file())
            self.assertFalse((output / "xboard-node").exists())
            self.assertFalse((output / "prepared.json").exists())
            self.assertEqual(transport.calls, [])

    def test_pinned_cli_uses_its_existing_local_lock_without_release_network(self):
        with patch.object(ne, "resolve_release_lock") as remote:
            self.assertEqual(ne.prepared_release_lock(Path("unused"), {}, "amd64"), json.loads(ne.LOCK_PATH.read_text()))
            remote.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
