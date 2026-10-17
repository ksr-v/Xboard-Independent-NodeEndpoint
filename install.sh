#!/usr/bin/env bash
# Root SSH bootstrap for the already enabled NodeEndpoint panel plugin.
# Keep the invocation last: a truncated download must not start installation.
set -Eeuo pipefail

nodeendpoint_bootstrap() (
    export PATH=/usr/sbin:/usr/bin:/sbin:/bin
    export LC_ALL=C
    umask 077

    fail() { printf 'NodeEndpoint: %s\n' "$*" >&2; exit 1; }
    [[ ${EUID} -eq 0 ]] || fail 'Run this command as root on the Xboard panel server.'
    [[ $(uname -s) == Linux ]] || fail 'Linux is required.'
    for tool in curl python3 sha256sum mktemp stat chmod rm rmdir; do
        command -v "$tool" >/dev/null || fail "Missing dependency: $tool"
    done
    python3 -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || fail 'Python 3.10+ is required.'

    # Do not repair an existing directory blindly or follow a foreign symlink.
    for directory in / /root; do
        [[ -d $directory && ! -L $directory ]] || fail "Unsafe directory: $directory"
        [[ $(stat -c %u -- "$directory") == 0 ]] || fail "Directory is not owned by root: $directory"
        permissions=$(stat -c %a -- "$directory")
        (( (8#${permissions} & 0022) == 0 )) || fail "Directory permits group/other writes: $directory"
    done

    noninteractive=0
    for argument in "$@"; do
        case $argument in --detect-only|--sync-ca|--configure-firewall|--help|-h) noninteractive=1 ;; esac
    done
    if (( ! noninteractive )); then
        # curl | bash consumes stdin; the Python wizard must read the SSH TTY.
        { true </dev/tty; } 2>/dev/null || fail 'An interactive SSH terminal is required (ssh -t).'
    fi

    work=$(mktemp -d /root/nodeendpoint-bootstrap.XXXXXXXX)
    cleanup() {
        rm -f -- "$work/quick_install.py"
        rmdir -- "$work"
    }
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM HUP
    chmod 700 "$work"
    printf '%s\n' 'Downloading the pinned installer; private root files use 0700/0600.'
    curl --fail --show-error --silent --location --proto '=https' --proto-redir '=https' \
        --tlsv1.2 --connect-timeout 15 --max-time 180 \
        'https://github.com/ksr-v/Xboard-Independent-NodeEndpoint/releases/download/root-installer-v1.0.4/quick_install.py' \
        --output "$work/quick_install.py"
    printf '%s  %s\n' '058e041eac8eacf31ff24926fd3d5154dce2045df0f7b0c3850b4e0e989caf40' "$work/quick_install.py" | sha256sum --check --status \
        || fail 'Installer SHA-256 mismatch; nothing was executed.'
    chmod 600 "$work/quick_install.py"
    cd /
    if (( noninteractive )); then
        python3 -I "$work/quick_install.py" "$@"
    else
        python3 -I "$work/quick_install.py" "$@" </dev/tty
    fi
)

nodeendpoint_bootstrap "$@"
