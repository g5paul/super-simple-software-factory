#!/usr/bin/env bash
# Start an SSSF operator session in the container. Extra arguments go to claude.
#
# The repo and ~/.config/sssf are mounted at the paths they have on the host,
# so the trace, the dashboard registry, and the rate-limit reading are the same
# files the host's `just obs-all` reads. Everything else the session sees is
# the image and its own home volume.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(git -C "$here" rev-parse --show-toplevel)
image="localhost/sssf-operator-$(basename "$repo"):latest"
token=${SSSF_CONTAINER_TOKEN:-$HOME/.config/sssf-container/token.env}

command -v podman >/dev/null || { echo "podman is not installed; see cookbooks/install.md" >&2; exit 1; }
if [ ! -f "$token" ]; then
    echo "no token file at $token" >&2
    echo "run 'claude setup-token' on the host, then save CLAUDE_CODE_OAUTH_TOKEN=<token> there (see cookbooks/install.md)" >&2
    exit 1
fi
# An image built before the label, or before the Containerfile changed, is
# rebuilt (decision 15).
want=$(sha256sum "$here/Containerfile" | cut -d' ' -f1)
have=$(podman image inspect --format '{{ index .Labels "sssf.containerfile.sha256" }}' "$image" 2>/dev/null || true)
[ "$have" = "$want" ] || "$here/build.sh"

# The host's pi sign-in stays on the host: the broker hands each pi agent a
# one-hour token through a socket. A Claude Code agent never uses it, and it
# starts whether or not the host is signed in to pi (decision 11).
runtime=${XDG_RUNTIME_DIR:?XDG_RUNTIME_DIR is not set; rootless podman needs it}
sock="$runtime/sssf-pi-$(basename "$repo").sock"
python3 "$here/pi_token_broker.py" "$sock" &
broker=$!
trap 'kill "$broker" 2>/dev/null' EXIT
for _ in $(seq 50); do
    [ -S "$sock" ] && break
    kill -0 "$broker" 2>/dev/null || break
    sleep 0.1
done
if [ ! -S "$sock" ] || ! kill -0 "$broker" 2>/dev/null; then
    echo "the pi token broker didn't start; see the message above" >&2
    exit 1
fi
mkdir -p "$HOME/.config/sssf"
quiet() { grep -v "is not a shared mount" >&2 || true; }

# With a token set, Claude is already signed in, but its first-run setup
# still opens on the login menu. Mark that setup done in the home volume.
podman run --rm --userns=keep-id -v sssf-operator-home:"$HOME":U "$image" python3 -c '
import json, pathlib
p = pathlib.Path.home() / ".claude.json"
d = json.loads(p.read_text()) if p.exists() else {}
if not d.get("hasCompletedOnboarding"):
    d["hasCompletedOnboarding"] = True
    p.write_text(json.dumps(d, indent=2))
' 2> >(quiet)

# The hostname is fixed so the trace can tell this container's processes from
# the host's: their pids live in different namespaces (see live_runs, kill).
# No exec: the EXIT trap has to run to stop the broker.
# --network pasta:-T,43190: the container's 127.0.0.1:43190 reaches the host's
# obs server (decision 13).
podman run --rm -it --userns=keep-id --network pasta:-T,43190 --name "sssf-operator-$(basename "$repo")" \
    --hostname "sssf-operator-$(basename "$repo")" \
    --env-file "$token" \
    -e DISABLE_AUTOUPDATER=1 \
    -e UV_PROJECT_ENVIRONMENT="$HOME/.cache/sssf-container-venv/$(basename "$repo")" \
    -e GIT_AUTHOR_NAME="$(git -C "$repo" config user.name)" -e GIT_COMMITTER_NAME="$(git -C "$repo" config user.name)" \
    -e GIT_AUTHOR_EMAIL="$(git -C "$repo" config user.email)" -e GIT_COMMITTER_EMAIL="$(git -C "$repo" config user.email)" \
    -v sssf-operator-home:"$HOME":U \
    -v "$repo:$repo" \
    -v "$HOME/.config/sssf:$HOME/.config/sssf" \
    -v "$sock:/run/sssf/pi.sock" \
    -e SSSF_PI_TOKEN_SOCKET=/run/sssf/pi.sock \
    -w "$repo" \
    "$image" \
    bash -c '
        # The session signs in from the token, but the shells it runs do not
        # get it, so the agents an ADW starts would be "Not logged in". Claude
        # Code runs CLAUDE_ENV_FILE before each shell command; this one
        # exports the token. It lives in the container'"'"'s /tmp, gone on exit.
        umask 077
        printf "export CLAUDE_CODE_OAUTH_TOKEN=%q\n" "$CLAUDE_CODE_OAUTH_TOKEN" > /tmp/sssf-auth.sh
        CLAUDE_ENV_FILE=/tmp/sssf-auth.sh exec claude --dangerously-skip-permissions "$@"
    ' run "$@"
