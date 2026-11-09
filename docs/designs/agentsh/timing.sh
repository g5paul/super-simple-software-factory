#!/bin/bash
# Times one kind of syscall at a time, with bash's built-in clock so that reading
# the clock starts no process. Run it bare and wrapped, from the workspace.
t() {
  local name=$1 n=$2 body=$3 a b
  a=${EPOCHREALTIME/./}
  eval "for ((i=0;i<n;i++)); do $body; done" 2>/dev/null
  b=${EPOCHREALTIME/./}
  printf '%-22s %6d us each  (%d runs)\n' "$name" $(( (b-a)/n )) "$n"
}
t "stat, builtin test"   200 '[ -e justfile ]'
t "open, redirect"       200 ': < justfile'
t "open missing"         200 ': < no-such-file'
t "no syscall, shows the eval's own open" 200 ':'
t "fork, subshell"        20 '( : )'
t "fork and exec true"    20 '/bin/true'
t "exec missing path"     20 '/no/such/program'
t "sleep 1"                3 'sleep 1'
t "python startup"         3 '.venv/bin/python -c pass'
t "python -S startup"      3 '.venv/bin/python -S -c pass'
