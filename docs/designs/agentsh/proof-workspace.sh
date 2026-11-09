#!/bin/bash
# Builds the proof workspace: a detached worktree of sssf-sandbox with a fake
# .env, so a guard failure leaks nothing. It lives outside /tmp, which empties at boot.
set -eu
SRC=$HOME/github/sssf-sandbox; W=$HOME/sssf-agentsh-proof
git -C "$SRC" worktree add --detach "$W" HEAD
cd "$W"
printf 'FAKE=not-a-secret\n' > .env
printf 'FAKE=\n' > .env.example
printf '\ntest kind:\n    @echo ran test {{kind}}\n' >> justfile
uv sync -q
echo "built $W"
