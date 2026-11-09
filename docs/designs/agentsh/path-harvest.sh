#!/bin/bash
# Usage: path-harvest.sh POLICY OUTDIR   From the workspace, runs tools and both agents
# under POLICY and saves each session's file events as OUTDIR/<label>.json, then prints
# each run's exit code and denials. Keep OUTDIR outside /tmp. Used to build and test the
# default-deny policy. The workspace's edits are reverted at the end.
case "$PWD" in "$HOME"/sssf-agentsh-proof) ;; *) echo "run this from ~/sssf-agentsh-proof, not $PWD: it edits and commits in the current repo" >&2; exit 2 ;; esac
POLICY=${1:?policy}; OUT=${2:?outdir}; W=$PWD
mkdir -p "$OUT"
export AGENTSH_CONFIG=/etc/agentsh/sssf.yaml AGENTSH_SERVER=unix:///run/agentsh/agentsh.sock AGENTSH_NO_AUTO=1 NODE_USE_ENV_PROXY=1 JUST_NO_DOTENV=true
TASK='Read hello.py. Add one short comment line at the top of it. Run the unit tests with: just test unit. Run git status and git diff. Then report in one line whether the tests passed. Do nothing else.'
TOOLS='just test unit; uv run pytest -q; .venv/bin/python -m pytest -q; git status --short; git log --oneline -3; git diff --stat; git commit -q --allow-empty -m harvest; git reset -q --soft HEAD~1; rg --version; grep -c "" justfile; ls -a; mkdir -p .h; echo x > .h/x; rm -f .h/x; rmdir .h; echo x > /tmp/h.$$; rm -f /tmp/h.$$; node --version; uv pip list -q'
run() {
  local label=$1; shift
  local log="$OUT/$label.log" start=$(date +%s) rc sid p=$POLICY
  # sssf-dd means the default-deny pair: pi gets its own, and the tools run under Claude's.
  if [ "$POLICY" = sssf-dd ]; then if [ "$label" = pi ]; then p=sssf-dd-pi; else p=sssf-dd-claude; fi; fi
  timeout 300 /usr/bin/agentsh wrap --policy "$p" --root "$W" --report=false -- "$@" < /dev/null > "$log" 2>&1
  rc=$?
  sid=$(grep -oE "session-[0-9a-f-]{36}" "$log" | head -1)
  # The server caps a page at 5000, and a larger limit silently returns 200.
  python3 -I - "$sid" > "$OUT/$label.json" <<'PY'
import json,sys,urllib.request
out=[]; off=0
while True:
    u=f"http://127.0.0.1:18080/api/v1/events/search?session_id={sys.argv[1]}&limit=5000&offset={off}"
    d=json.load(urllib.request.urlopen(u,timeout=30)); ev=d if isinstance(d,list) else d.get("events",[])
    out+=ev; off+=len(ev)
    if len(ev)<5000: break
json.dump(out,sys.stdout)
PY
  python3 -I - "$OUT/$label.json" "$label" "$rc" "$(( $(date +%s)-start ))" <<'PY'
import json,sys,collections
d=json.load(open(sys.argv[1])); ev=d if isinstance(d,list) else d.get("events",[])
deny=collections.Counter()
for e in ev:
    f=e.get("fields") or {}
    if f.get("decision")=="deny" and e.get("type","").startswith("ptrace_file"):
        deny[(f.get("operation"),f.get("path"),f.get("rule"))]+=1
print(f"== {sys.argv[2]} exit {sys.argv[3]} in {sys.argv[4]}s, {len(ev)} events, {sum(deny.values())} file denials")
for k,n in deny.most_common(40): print("  ",n,*k)
PY
}
run tools bash -c "$TOOLS"
run claude claude -p "$TASK" --model claude-haiku-5-5 --allowedTools "Bash Read Edit Write" --max-turns 10
git checkout -q -- hello.py 2>/dev/null
run pi pi -p --no-session --model openai/gpt-5.6-luna --tools bash,read,edit,write "$TASK"
git checkout -q -- hello.py 2>/dev/null
git status --short | head -5
