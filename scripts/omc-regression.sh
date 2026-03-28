#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/omc-regression.sh

Runs a small checked-in OMC regression probe set. Each probe is assembled twice:
first raw to confirm the known OMC failure still reproduces, then through
scripts/asmout_postprocess.py --dialect omc to verify the rewritten output
assembles successfully.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
output_root="$repo_root/tmp/omc-regression"
probe_dir="$output_root/probes"
python_bin="$repo_root/.venv/bin/python"
omc_bin="$repo_root/omc"

mkdir -p "$output_root"
mkdir -p "$probe_dir"

if [[ ! -x "$omc_bin" ]]; then
  echo "missing OMC binary: $omc_bin" >&2
  exit 1
fi

if [[ ! -x "$python_bin" ]]; then
  echo "missing Python environment: $python_bin" >&2
  exit 1
fi

run_probe() {
  local name="$1"
  local expected_pattern="$2"
  local source_file="$probe_dir/$name.a65"
  local source_output="$probe_dir/$name.COM"
  local rewritten_file="$output_root/$name.omc.a65"
  local raw_log="$output_root/$name.raw.log"
  local rewritten_log="$output_root/$name.omc.log"
  local source_arg="${source_file#"$repo_root"/}"
  local rewritten_arg="${rewritten_file#"$repo_root"/}"

  echo "==> Probe $name"

  trap 'rm -f "$source_output"' RETURN
  rm -f "$source_output"

  set +e
  (
    cd "$repo_root"
    "$omc_bin" "$source_arg"
  ) >"$raw_log" 2>&1
  local raw_status=$?
  set -e

  if [[ $raw_status -eq 0 ]]; then
    echo "FAIL raw OMC unexpectedly accepted $source_file" >&2
    sed -n '1,40p' "$raw_log" >&2
    exit 1
  fi

  "$python_bin" "$repo_root/scripts/asmout_postprocess.py" \
    --dialect omc \
    "$source_file" \
    "$rewritten_file" >/dev/null

  if ! grep -Eq "$expected_pattern" "$rewritten_file"; then
    echo "FAIL rewritten output for $name did not contain expected pattern" >&2
    echo "pattern: $expected_pattern" >&2
    sed -n '1,80p' "$rewritten_file" >&2
    exit 1
  fi

  (
    cd "$repo_root"
    "$omc_bin" "$rewritten_arg"
  ) >"$rewritten_log" 2>&1
  echo "OK  $name"
}

write_probe() {
  local name="$1"
  local source_file="$probe_dir/$name.a65"

  case "$name" in
    pagecross-label)
      cat >"$source_file" <<'EOF'
    *= $7FFE
    .BYTE $EA
    .BYTE $EA
TARGET
    RTS
EOF
      ;;
    symbolic-lowpage-absy)
      cat >"$source_file" <<'EOF'
BASE = $E8

    *= $AC00
    STA BASE-8,Y
NEXT
    RTS
EOF
      ;;
    symbolic-zp-indexedx)
      cat >"$source_file" <<'EOF'
ZPVAR = $80

    *= $96C1
    STY ZPVAR,X
NEXT
    RTS
EOF
      ;;
    spaced-indexed)
      cat >"$source_file" <<'EOF'
POKEY = $D200

    *= $96B5
    STA POKEY, X
EOF
      ;;
    *)
      echo "unknown probe: $name" >&2
      exit 1
      ;;
  esac
}

write_probe pagecross-label
write_probe symbolic-lowpage-absy
write_probe symbolic-zp-indexedx
write_probe spaced-indexed

run_probe pagecross-label '^TARGET = \*$'
run_probe symbolic-lowpage-absy '^\s*\.BYTE \$99,\$E0,\$00$'
run_probe symbolic-zp-indexedx '^\s*\.BYTE \$94,\$80$'
run_probe spaced-indexed '^\s*STA POKEY,[Xx]$'

echo "==> OMC regression probes passed"