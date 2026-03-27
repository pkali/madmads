#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/cc65-tool.sh <tool> [args...]

Runs a cc65 tool from either:
  1. a system installation available on PATH, or
  2. the repo-local extracted toolchain under tmp/cc65-local.

Examples:
  scripts/cc65-tool.sh ca65 --version
  scripts/cc65-tool.sh ld65 --version
EOF
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 2
fi

tool="$1"
shift

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
local_root="$repo_root/tmp/cc65-local/root"
local_bin="$local_root/usr/bin/$tool"

if command -v "$tool" >/dev/null 2>&1; then
  exec "$tool" "$@"
fi

if [[ -x "$local_bin" ]]; then
  export CC65_HOME="$local_root/usr/share/cc65"
  exec "$local_bin" "$@"
fi

cat >&2 <<EOF
cc65 tool '$tool' was not found.

Tried:
  - system PATH
  - $local_bin

To set up a repo-local copy without sudo, download and extract the Ubuntu package:
  mkdir -p tmp/cc65-local
  cd tmp/cc65-local
  apt-get download cc65
  dpkg-deb -x cc65_*.deb root
EOF
exit 1