#!/usr/bin/env bash
# Shared entry point; run one of the named loader scripts instead.
set -euo pipefail
kind=$1
shift
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --help ]]; then
  echo "usage: bash modloaders/$kind.sh [minecraft-version] [loader-version-or-paper-build]"
  echo 'defaults to the installed minecraft version; run install.sh first for a fresh server.'
  exit 0
fi
[[ $# -le 2 ]] || { echo 'too many arguments' >&2; exit 1; }
[[ "$(uname -s)" == Linux ]] || { echo 'run this inside your linux codespace' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'run bash install.sh first to install prerequisites' >&2; exit 1; }
exec python3 "$repo_root/modloaders/_manager.py" "$kind" "$@"
