#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/mads-roundtrip.sh [800|5200|all]

Builds Mad-Assembler/mads with the system FPC, generates asmout from scorch_src,
reassembles it, and verifies that the binary roundtrip matches.
EOF
}

target="${1:-all}"

case "$target" in
  800|5200|all)
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
mads_dir="$repo_root/Mad-Assembler"
scorch_dir="$repo_root/scorch_src"
artifact_root="$scorch_dir/.roundtrip"

mkdir -p "$artifact_root"

build_mads() {
  echo "==> Building MADS"
  (
    cd "$mads_dir"
    fpc -Mdelphi -vh -O3 mads.pas
  )
}

roundtrip_target() {
  local scorch_target="$1"
  local out_dir="$artifact_root/$scorch_target"
  local asmout_file orig_bin orig_lab round_bin round_lab

  mkdir -p "$out_dir"

  if [[ "$scorch_target" == "800" ]]; then
    asmout_file="$out_dir/scorch800.a65"
    orig_bin="$out_dir/orig.xex"
    orig_lab="$out_dir/orig.lab"
    round_bin="$out_dir/roundtrip.xex"
    round_lab="$out_dir/roundtrip.lab"
  else
    asmout_file="$out_dir/scorch5200.a65"
    orig_bin="$out_dir/orig.bin"
    orig_lab="$out_dir/orig.lab"
    round_bin="$out_dir/roundtrip.bin"
    round_lab="$out_dir/roundtrip.lab"
  fi

  echo "==> Roundtrip target=$scorch_target"

  (
    cd "$scorch_dir"
    "$mads_dir/mads" scorch.asm \
      -d:TARGET="$scorch_target" \
      -A:"$asmout_file" \
      -o:"$orig_bin" \
      -t:"$orig_lab"

    "$mads_dir/mads" "$asmout_file" \
      -o:"$round_bin" \
      -t:"$round_lab"
  )

  if cmp -s "$orig_bin" "$round_bin"; then
    echo "OK  target=$scorch_target binary roundtrip matched"
  else
    echo "FAIL target=$scorch_target binary roundtrip mismatch" >&2
    exit 1
  fi
}

build_mads

if [[ "$target" == "all" ]]; then
  roundtrip_target 800
  roundtrip_target 5200
else
  roundtrip_target "$target"
fi

echo "==> Done"