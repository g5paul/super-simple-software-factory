#!/bin/bash
# Usage: pi-loop.sh RUNS [HANG_SECONDS]   From the workspace, runs a cheap wrapped pi
# prompt, or $PI_PROMPT with bash and read tools, and counts hangs. On a hang it prints every thread's state before killing it.
RUNS=${1:-5}; LIMIT=${2:-40}; PROMPT=${PI_PROMPT:-Say OK.}
export AGENTSH_CONFIG=/etc/agentsh/sssf.yaml AGENTSH_SERVER=unix:///run/agentsh/agentsh.sock AGENTSH_NO_AUTO=1 NODE_USE_ENV_PROXY=1
ok=0; hung=0; failed=0
LOG=$(mktemp -p "$PWD" .pilog.XXXXXX)
threads() {
  for t in /proc/$1/task/*; do
    printf '  tid %s %-14s state=%s wchan=%s\n' "${t##*/}" "$(cat "$t/comm")" "$(cut -d')' -f2 "$t/stat" | cut -d' ' -f2)" "$(cat "$t/wchan" 2>/dev/null)"
  done
}
for i in $(seq 1 "$RUNS"); do
  start=$(date +%s)
  /usr/bin/agentsh wrap --policy sssf-net --root "$PWD" --report=false -- \
    pi -p --no-session --model openai/gpt-5.6-luna --tools bash,read "$PROMPT" > "$LOG" 2>&1 &
  W=$!
  timeout "$LIMIT" tail --pid="$W" -f /dev/null
  if kill -0 "$W" 2>/dev/null; then
    hung=$((hung+1)); echo "run $i HUNG after ${LIMIT}s"
    P=$(pgrep -P "$W" | head -1)
    echo " pi pid ${P:-none}, tracer $(awk '/TracerPid/{print $2}' "/proc/$P/status" 2>/dev/null)"
    [ -n "$P" ] && threads "$P"
    grep -oE "session-[0-9a-f-]{36}" "$LOG" | head -1
    [ -n "$P" ] && kill -9 "$P"
    kill "$W" 2>/dev/null; wait "$W" 2>/dev/null
  else
    wait "$W"; rc=$?
    if [ "$rc" = 0 ]; then ok=$((ok+1)); else failed=$((failed+1)); fi
    echo "run $i rc=$rc $(( $(date +%s)-start ))s"
  fi
done
rm -f "$LOG"
echo "ok=$ok hung=$hung failed=$failed"
