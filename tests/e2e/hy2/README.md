# Hysteria2 port-hopping experiment, 2026-10-25

These are the exact experimental source snapshots for the isolated acceptance
test documented in `docs/HYSTERIA2_PORT_HOPPING_TEST_REPORT.md`. They are not a
production installer. Review the fixed paths and isolation assumptions before
running them on another machine.

Copy these five files into `.runtime/hy2-20261025/` before local execution. The
PHP fixture expects the pinned backend at `.runtime/xboard-bundled` and this
workstation's existing Composer autoloader at
`D:/Xboard-Independent/source/Xboard/vendor/autoload.php`. It uses real Laravel
classes with an in-memory SQLite database and array cache, not a live panel.
`backend_subscriptions.php` writes a synthetic public boundary-test summary;
`panel_runtime_export.php` writes a separate **private** runtime config containing
a newly generated synthetic user identity. Never commit that private JSON.

The Go client is built with the existing offline Node build environment and
fixed dependencies; it makes no upstream fetches. See the report for hashes and
the build command. It consumes only the selected real Hy2 outbound and requires
TLS verification. It performs exactly one TCP dial and one UDP session and
checks every echoed byte for the complete requested duration. It does not log
credentials, packet payloads or raw errors.

`vm_hy2_fixture.py` expects the formal Node binary, the compiled client, both
Python scripts and the private exported config in the fresh codex-owned 0700
directory `/tmp/codex-hy2-upload-20261025` on the authorized Linux test VM. Its
`prepare` action verifies binary hashes, creates the fresh root-owned 0700
`/opt/xboard-hy2-hop-20261025`, and generates an isolated two-day test CA and
server certificate. Its `run` action creates two dedicated network namespaces
and a private veth pair, tests both embedded server kernels, and removes its
processes/namespaces in `finally`. nft UDP redirects exist only in the server
namespace. It must not be used in an existing directory or on a production
machine without a new isolation review.

`wire_helpers.py` supplies private TCP/UDP echo endpoints and records only
IPv4/UDP header metadata on the private veth. It stores neither payload nor
PCAP. Multiple observed endpoint transitions are not an exact timer-hop count:
old sockets and retransmissions can interleave. Use distinct destination ports,
source sockets, duration and same-session byte checks together.

The first two harness attempts stopped on empty `ip -j netns list` output and
single-line nft parsing. The committed fixture contains the corrected parsing
and multi-line rule; its final run writes under `attempt-2`, preserving prior
attempts. These were fixture errors, not protocol failures. No private runtime
logs were read. Public result JSON is exported separately from private inputs,
certificates and logs.
