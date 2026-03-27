#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/ca65-probe.sh [800|5200]

Generates current MadMads asmout for Scorch, rewrites it for the ca65 backend,
then runs ca65 and stores the resulting object or error log under tmp/ca65-probe.
EOF
}

target="${1:-800}"

case "$target" in
  800|5200)
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
probe_root="$repo_root/tmp/ca65-probe/$target"
mkdir -p "$probe_root"

if [[ "$target" == "800" ]]; then
  asmout_file="$probe_root/scorch800.a65"
  ca65_file="$probe_root/scorch800.ca65.asm"
  object_file="$probe_root/scorch800.ca65.o"
else
  asmout_file="$probe_root/scorch5200.a65"
  ca65_file="$probe_root/scorch5200.ca65.asm"
  object_file="$probe_root/scorch5200.ca65.o"
fi

log_file="$probe_root/ca65.log"

echo "==> Generating asmout target=$target"
(
  cd "$scorch_dir"
  "$mads_dir/mads" scorch.asm -d:TARGET="$target" -A:"$asmout_file" -o:/dev/null
)

echo "==> Rewriting asmout for ca65"
"$repo_root/.venv/bin/python" "$repo_root/scripts/asmout_postprocess.py" \
  --dialect ca65 \
  "$asmout_file" \
  "$ca65_file"

echo "==> Running ca65"
set +e
"$repo_root/scripts/cc65-tool.sh" ca65 "$ca65_file" -o "$object_file" >"$log_file" 2>&1
status=$?
set -e

if [[ $status -eq 0 ]]; then
  echo "OK  ca65 assembled $ca65_file"
  echo "OBJ $object_file"
  exit 0
fi

echo "FAIL ca65 rejected $ca65_file"
echo "LOG  $log_file"
sed -n '1,40p' "$log_file"
exit $status