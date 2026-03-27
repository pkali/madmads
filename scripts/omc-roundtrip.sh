#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/omc-roundtrip.sh

Runs the full lowered asmout verification stack:
1. MADS Scorch binary roundtrip for 800 and 5200
2. Checked-in OMC regression probes
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Running MADS Scorch roundtrip"
"$script_dir/mads-roundtrip.sh" all

echo "==> Running OMC regression probes"
"$script_dir/omc-regression.sh"

echo "==> OMC roundtrip verification passed"