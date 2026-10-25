# Node-managed Hysteria2 UDP forwarding, 2026-10-25

Exact experimental sources for `docs/HYSTERIA2_AUTO_FORWARDING_REPORT.md`.
These scripts have fixed paths, hashes and ownership markers for the authorized
dedicated VM. They are not production installation commands. Review isolation
and use fresh directories before adapting them to any other environment.

- `vm_hy2_auto_fixture.py`: private CA setup, two new network namespaces,
  candidate binary hash verification and both server kernels. Twenty checks
  require Node itself to create/replace/remove NAT rules. The fixture never
  installs a successful-path redirect. The pre-existing verified client comes
  from `../hy2/client_main.go`; no client business code changes were needed.
- `wire_helpers.py`: raw TCP/UDP echo and private-link IPv4/UDP header metadata
  only. No payload or PCAP storage.
- `panel_fixture.py` + `panel_seed.php`: separate loopback `17006` Docker panel,
  SQLite database, Redis and synthetic machine/user. The immutable local base
  image is overlaid offline with the current backend source, using the fixed
  per-file checksums embedded in the script. Initialization output and random
  credentials go only to the fresh Root-private fixture directory. The PHP seed
  and range changes use real FormRequest preparation and the save controller.
- `managed_panel.py` + `byte_relay.py`: 22 actual machine HTTP/NodeWorker WS
  checks on both kernels. A byte-transparent Unix relay joins namespace
  loopback to the real panel's host loopback; it neither interprets nor stores
  content. It does not simulate the panel API.
- `guards.py`: five actual process/nft checks in a private namespace: occupied
  UDP port, foreign NAT, dropped NET_ADMIN, overlapping Node instances, SIGKILL
  and same-owner stale-table recovery. Only its negative NAT-conflict fixture
  installs one foreign rule, scoped to that namespace.
- `linux_source_verify.py`: archived candidate source with the existing offline
  Go module cache, production tags and `-race`. No source or dependency fetch.
- `final_guard.py`: final owned process/container/namespace/port inventory and
  original-panel HTTP status. No private runtime log reads.

Upload fixture files to `/tmp/codex-hy2-auto-upload-20261025` (0700), along with
the pinned client, candidate Node, manifest and **private** synthetic panel
export. The standalone `prepare` action checks them and creates fresh
`/opt/xboard-hy2-auto-20261025` (0700). Run standalone tests first, then the real
panel managed tests, then guards; their shared namespace names must not be used
concurrently. Each stage cleans its own namespace processes and compares the
host's stateless nft ruleset before/after. Stop/remove only the owned panel
container with its driver before final inventory. Private test data is retained.

The public evidence JSON contains all source snapshot hashes and measured
results. Runtime binaries, private exports, CA keys, database files and logs
must never be added to this directory or Git. Published v1.13-orphan.5 remains
unchanged; these are new-feature candidate tests, not a new formal Release.
