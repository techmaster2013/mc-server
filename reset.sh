#!/usr/bin/env bash
# Deletes only this repository's minecraft-server directory, including worlds.
set -euo pipefail
[[ $# -eq 0 ]] || { echo 'usage: bash reset.sh (deletes minecraft-server/)' >&2; exit 1; }
[[ "$(uname -s)" == Linux ]] || { echo 'run this inside your linux codespace' >&2; exit 1; }
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$repo_root/modloaders/_manager.py" reset
