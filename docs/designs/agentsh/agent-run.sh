#!/bin/bash
# Usage: agent-run.sh claude|pi   Runs one wrapped agent and lists its network events.
W=/tmp/sssf-agentsh-proof; cd $W || exit 1
export AGENTSH_CONFIG=/etc/agentsh/sssf.yaml AGENTSH_SERVER=unix:///run/agentsh/agentsh.sock AGENTSH_NO_AUTO=1 NODE_USE_ENV_PROXY=1
PROMPT='Say OK. Then use your shell tool once to run exactly: curl -sS -m 8 -o /dev/null -w "%{http_code}" https://example.com ; and report its output verbatim. Do nothing else.'
case $1 in
  claude) CMD=(claude -p "$PROMPT" --model claude-haiku-5-5 --allowedTools "Bash(curl *)" --max-turns 4) ;;
  pi) CMD=(pi -p --no-session --model openai/gpt-5.6-luna --tools bash "$PROMPT") ;;
  *) echo "usage: $0 claude|pi"; exit 2 ;;
esac
LOG=$W/agent-$1.log
start=$(date +%s)
timeout 240 /usr/bin/agentsh wrap --policy sssf-net --root $W --report=false -- "${CMD[@]}" > $LOG 2>&1
echo "== $1 exit $? after $(( $(date +%s) - start ))s"
grep -v "^agentsh: [sF]" $LOG | tail -15 | cut -c1-200
SID=$(grep -oE "session-[0-9a-f-]{36}" $LOG | head -1)
echo "== network events for $SID"
curl -s -m 10 "http://127.0.0.1:18080/api/v1/events/search?session_id=$SID&limit=2000" | python3 -I -c '
import json,sys,collections
d=json.load(sys.stdin); ev=d if isinstance(d,list) else d.get("events",[])
c=collections.Counter()
for e in ev:
    t=e.get("type","")
    if not (t.startswith("net") or t.startswith("ptrace_network") or t.startswith("dns")): continue
    f=e.get("fields") or {}; p=e.get("policy") or {}
    host=e.get("domain") or f.get("domain") or e.get("remote") or f.get("address") or ""
    c[(t, host, str(f.get("port") or e.get("port") or ""), p.get("effective_decision") or f.get("decision") or "", p.get("rule") or f.get("rule") or "")]+=1
for k,n in sorted(c.items()): print(n, *k)
print("total events", len(ev))'
