#!/usr/bin/env bash
set -euo pipefail
umask 077
tool_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Python 3.10+ is required. No system packages will be installed automatically.' >&2
  exit 1
fi
if ! python3 -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  printf '%s\n' 'Python 3.10+ is required; the available interpreter is older.' >&2
  exit 1
fi
exec python3 -I -B "$tool_dir/manager.py" "$@"
