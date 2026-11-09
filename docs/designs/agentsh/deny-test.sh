#!/bin/bash
# Usage: deny-test.sh OUTDIR [probe-only]   From the workspace. Runs the probe under each default-deny
# policy, reports which fake files it changed, then runs the tools and both agents.
# Keep OUTDIR outside /tmp.
OUT=${1:?outdir}; HERE=$(cd "$(dirname "$0")" && pwd); W=$PWD
D=$HOME/.local/share/agentsh-harvest/secret-probe
mkdir -p "$D" "$OUT"
export AGENTSH_CONFIG=/etc/agentsh/sssf.yaml AGENTSH_SERVER=unix:///run/agentsh/agentsh.sock AGENTSH_NO_AUTO=1
for agent in claude pi; do
  find "$D" -mindepth 1 -delete
  printf 'FAKE=probe-value\n' > "$D/.env"; printf 'FAKE=probe-value\n' > "$D/plain.txt"
  for f in del-direct del-root del-fd del-cwd mv-fd mv-out chmod-fd; do : > "$D/$f"; done
  chmod 644 "$D/chmod-fd"
  echo "====== probe under sssf-dd-$agent"
  # The policy denies opening the hub's copy, so the probe runs from the workspace.
  cp "$HERE/deny-probe.sh" "$W/.deny-probe.sh"
  /usr/bin/agentsh wrap --policy "sssf-dd-$agent" --root "$W" --report=false -- bash "$W/.deny-probe.sh" < /dev/null 2>&1 | grep -v "^agentsh:" | cut -c1-100
  rm -f "$W/.deny-probe.sh"
  echo "--- fake files after the probe, anything but the seven set-up names and .env, plain.txt is a change"
  ls -A "$D" | tr '\n' ' '; echo; stat -c 'chmod-fd mode %a' "$D/chmod-fd" 2>/dev/null
  rm -f "$W/mv-out-done"
done
[ "${2:-}" = probe-only ] && exit 0
echo "====== real work"
bash "$HERE/path-harvest.sh" sssf-dd "$OUT"
