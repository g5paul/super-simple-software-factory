# SSSF starter recipes. Stamped by install.py, then yours to edit.
#
# One recipe for each chain and each command you run by hand: run something,
# watch it, land it, set up the container or a VM. `install.py` itself has none,
# since it's what writes this file.
#
# Put recipes of your own in local.just, beside this file. It's loaded when it
# exists, and an update never touches it, so this file stays the skill's and
# `just sssf-update` can replace it.

# `.env` reaches every ADW through this, so keys work without exporting them.
set dotenv-load
set positional-arguments

import? 'local.just'

# Every recipe passes this through, so `SSSF_CONFIG=other.yaml just sdlc "..."`
# swaps the whole roster for one run.
config := env_var_or_default("SSSF_CONFIG", "adws/adw_sssf_config/sssf.config.yaml")
db     := "adws/adw_data/sssf.db"
# How to call these recipes: `just` when this is the repo's justfile, `just sssf`
# when a repo with its own justfile loads it with `mod sssf 'sssf.just'`.
just   := if source_file() == justfile() { "just" } else { "just sssf" }

# list every recipe
default:
    @just --list

# ── first run ───────────────────────────────────────────────────────────────

# Proves the whole path works: config validated, session minted, agent ran,
# envelope parsed, gates checked, trace written. Costs a few cents and changes
# nothing in your repo, because both workflows are read-only.
#
# (`just --list` shows only the LAST comment line, so that one is the summary.)

# start here: two cheap read-only runs, end to end
demo:
    @echo "1/2  adw_prompt: one agent, one prompt"
    uv run adws/adw_prompt.py --config {{config}} --agent scout "reply with a one-line summary of this repo"
    @echo "\n2/2  adw_scout: read-only recon"
    uv run adws/adw_scout.py --config {{config}} "list the top-level directories in this repo and what each is for. change nothing."
    @echo "\nboth done. now run:  {{just}} sessions    (or: {{just}} obs)"

# ── run a workflow ──────────────────────────────────────────────────────────
# Args pass straight through: "<prompt or path/to/prompt.md>" [--adw-id X]

# one agent, one prompt: just prompt "summarize this repo"
prompt *ARGS:
    uv run adws/adw_prompt.py --config {{config}} "$@"

# read-only recon: just scout "where is auth handled"
scout *ARGS:
    uv run adws/adw_scout.py --config {{config}} "$@"

# plan only: just plan "add a /health endpoint"
plan *ARGS:
    uv run adws/adw_plan.py --config {{config}} "$@"

# planner, builder, commit: just plan-build "add a /health endpoint"
plan-build *ARGS:
    uv run adws/adw_plan_build.py --config {{config}} "$@"

# plan, build, test, commit: just sdlc "add a /health endpoint"
sdlc *ARGS:
    uv run adws/adw_plan_build_test.py --config {{config}} "$@"

# the full chain, plus review and docs: just simple-sdlc "add a /health endpoint"
simple-sdlc *ARGS:
    uv run adws/adw_simple_sdlc.py --config {{config}} "$@"

# The chains below take the same arguments, and most join an earlier run with
# --adw-id, which is how two runs chain by hand.

# builder only, on a plan that exists: just build "implement the plan" --adw-id <id>
build *ARGS:
    uv run adws/adw_build.py --config {{config}} "$@"

# builder, tests, fix loop: just build-test "implement the plan" --adw-id <id>
build-test *ARGS:
    uv run adws/adw_build_test.py --config {{config}} "$@"

# builder, reviewer, revise loop: just build-review "add a /health endpoint"
build-review *ARGS:
    uv run adws/adw_build_review.py --config {{config}} "$@"

# plan, build, test and every quality.py check, then commit: just sdlc-quality "..."
sdlc-quality *ARGS:
    uv run adws/adw_plan_build_test_quality.py --config {{config}} "$@"

# a write-up of what shipped, from the diff: just document "the /health endpoint"
document *ARGS:
    uv run adws/adw_document.py --config {{config}} "$@"

# quality.py's checks, no agent: just quality "check after the SSSF update"
quality *ARGS:
    uv run adws/adw_quality.py --config {{config}} "$@"

# build an approved feature folder from mattpocock/skills, one ticket at a time:
# just tickets .scratch/summaries/   (see cookbooks/tickets.md)
tickets *ARGS:
    uv run adws/adw_tickets.py --config {{config}} "$@"

# The roster is chosen per run: `--config <path>` on any ADW, or SSSF_CONFIG for
# these recipes. This is how you find the path to pass.
# every roster on disk, and the model each agent runs: just rosters
rosters:
    #!/usr/bin/env -S uv run --no-project -q --with pyyaml python
    import glob, yaml
    for path in sorted(glob.glob("adws/adw_sssf_config/sssf*.yaml")):
        cfg = yaml.safe_load(open(path)) or {}
        default = (cfg.get("defaults") or {}).get("model", "?")
        print(path)
        for agent in cfg.get("agents") or []:
            model = agent.get("model") or f"{default} (inherited)"
            print(f"    {agent['name']:<18} {model}")

# ── land a worktree run ─────────────────────────────────────────────────────
# A chain started with --worktree works on branch sssf/<adw_id>, in its own
# folder, and nothing reaches your branch until you merge it. Neither pushes.

# --ff-only, or `merge: ff_only` in adws/adw_sssf_config/repo.yaml, refuses a merge commit.
# land a successful worktree run on its launch branch: just merge <adw_id>
merge ADW_ID *ARGS:
    uv run adws/adw_worktree.py merge --config {{config}} "$@"

# Only for a repo with `merge: ff_only`, and only when you ask: a conflict or a
# failed check puts the branch back. `just merge` itself never rebases.
# move a run's branch onto its launch branch and recheck it: just rebase <adw_id>
rebase ADW_ID:
    uv run adws/adw_worktree.py rebase {{ADW_ID}} --config {{config}}

# drop a worktree run's branch and folder, uncommitted work included: just discard <adw_id>
discard ADW_ID:
    uv run adws/adw_worktree.py discard {{ADW_ID}} --config {{config}}

# ── send a feature upstream ─────────────────────────────────────────────────
# Builds a local branch export/<name> on upstream/main holding a feature's
# product changes, with no SSSF files, then runs repo.yaml's export_checks on
# it. It never fetches or pushes. See cookbooks/run_adw.md, "Send a feature upstream".
# a feature's changes without SSSF files: just export <branch> | --run <adw_id> | --feature <slug>
export *ARGS:
    uv run adws/adw_export.py --config {{config}} "$@"

# ── race rosters ────────────────────────────────────────────────────────────
# A trial runs one request under several rosters, each as its own worktree
# run of simple-sdlc, then a panel reviews them blind. You pick the winner.
# `main` is sssf.config.yaml; <name> is sssf.config.<name>.yaml. See cookbooks/trial.md.

# race one request across rosters: just trial "add a /health endpoint" main chatgpt
trial *ARGS:
    uv run adws/adw_trial.py start "$@"

# a trial's scorecard, at any point: just trial-show <trial>
trial-show TRIAL:
    uv run adws/adw_trial.py show {{TRIAL}}

# run a trial's panel again, if `just trial` died or its panel failed: just trial-review <trial>
trial-review TRIAL:
    uv run adws/adw_trial.py review {{TRIAL}}

# land your pick and drop the other attempts: just trial-merge <trial> <adw_id>
trial-merge TRIAL ADW_ID *ARGS:
    uv run adws/adw_trial.py merge {{TRIAL}} {{ADW_ID}} {{ARGS}}

# drop every attempt of a trial: just trial-discard <trial>
trial-discard TRIAL:
    uv run adws/adw_trial.py discard {{TRIAL}}

# ── watch it ────────────────────────────────────────────────────────────────
# Reads never block a running workflow, the db is WAL. Poll as hard as you like.

# the last 10 runs
sessions:
    @if [ -f {{db}} ]; then sqlite3 {{db}} "select adw_id, status, substr(request,1,50), total_tokens, round(total_cost,4) from sessions order by started_at desc limit 10;"; else echo "no runs yet: {{db}} does not exist"; fi

# phase status in sequence: just phases <adw_id>
phases ADW_ID:
    @if [ -f {{db}} ]; then sqlite3 {{db}} "select seq, name, kind, owner, status, attempt from phases where adw_id='{{ADW_ID}}' order by seq;"; else echo "no runs yet: {{db}} does not exist"; fi

# the live event tail: just tail <adw_id>
tail ADW_ID:
    @if [ -f {{db}} ]; then sqlite3 {{db}} "select rowid, type, name, started_at from events where adw_id='{{ADW_ID}}' order by rowid desc limit 25;"; else echo "no runs yet: {{db}} does not exist"; fi

# what a run has alive right now, with pids: just procs <adw_id>
procs ADW_ID:
    @if [ -f {{db}} ]; then sqlite3 {{db}} "select kind, name, pid, command, started_at from processes where adw_id='{{ADW_ID}}' and ended_at is null order by id;"; else echo "no runs yet: {{db}} does not exist"; fi

# It refuses while SSSF_ADW_ID is set, so an agent in a run can't start it by name.
# scripts/openshell_image.py refuses the same way, except with --print. Neither stops an
# agent that clears SSSF_ADW_ID, runs the steps by hand, or starts it through something
# that gives it a fresh environment, such as ssh, sudo or systemd-run; see config.md.
# It never runs sudo. It creates TLS keys and gateway.toml only when missing, and
# rewrites its own unit file ~/.config/systemd/user/openshell-gateway.service on every
# run, which is how a rerun picks up a moved openshell-gateway; keep your own unit under
# another name.
# set up the local OpenShell gateway and image for --sandbox openshell
openshell-setup:
    #!/usr/bin/env bash
    set -euo pipefail
    [ -z "${SSSF_ADW_ID:-}" ] || { echo "Refusing: openshell-setup is the engineer's to run, outside an SSSF run (SSSF_ADW_ID is set)." >&2; exit 1; }
    if ! { [ -r /dev/kvm ] && [ -w /dev/kvm ]; }; then
        echo "/dev/kvm isn't readable and writable by you. Pick one fix:" >&2
        echo "  sudo chgrp $USER /dev/kvm      (lasts until WSL restarts)" >&2
        echo "  sudo usermod -aG kvm $USER     (then run 'wsl --shutdown' from Windows, which stops every WSL session)" >&2
        exit 1
    fi
    if ! command -v openshell openshell-gateway podman >/dev/null; then
        echo "openshell, openshell-gateway and podman must all be on PATH. See 'Installing OpenShell' under 'A local sandbox: --sandbox openshell' in .claude/skills/sssf/references/config.md." >&2
        exit 1
    fi
    tls="$HOME/.local/state/openshell/tls"
    if [ ! -f "$tls/ca.crt" ]; then
        openshell-gateway generate-certs --output-dir "$tls"
    fi
    conf="$HOME/.config/openshell/gateway.toml"
    if [ ! -f "$conf" ]; then
        mkdir -p "$(dirname "$conf")"
        printf '[openshell]\nversion = 2\n\n[openshell.gateway]\nbind_address = "127.0.0.1:17670"\ncompute_driver = "vm"\n' > "$conf"
    fi
    unit="$HOME/.config/systemd/user/openshell-gateway.service"
    mkdir -p "$(dirname "$unit")"
    cat > "$unit" <<EOF
    [Unit]
    Description=OpenShell gateway for SSSF

    [Service]
    ExecStart=$(command -v openshell-gateway) --config %h/.config/openshell/gateway.toml
    Environment=OPENSHELL_TELEMETRY_ENABLED=false
    Environment=OPENSHELL_LOCAL_TLS_DIR=%h/.local/state/openshell/tls
    Environment=OPENSHELL_BIND_ADDRESS=127.0.0.1
    Restart=on-failure

    [Install]
    WantedBy=default.target
    EOF
    systemctl --user daemon-reload
    systemctl --user enable --now openshell-gateway
    uv run .claude/skills/sssf/scripts/openshell_image.py --repo .
    openshell status
    echo "Next: adw_simple_sdlc --worktree --sandbox openshell"

# Once per E2B team, from a repo whose .env has E2B_API_KEY. It uses the key, so
# it refuses inside a run, like openshell-setup.
# build SSSF's E2B VM template, which --sandbox e2b makes each VM from
e2b-template *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    [ -z "${SSSF_ADW_ID:-}" ] || { echo "Refusing: e2b-template is the engineer's to run, outside an SSSF run (SSSF_ADW_ID is set)." >&2; exit 1; }
    exec uv run .claude/skills/sssf/scripts/e2b_template.py "$@"

# Name other repos' traces to add theirs: just sandbox-usage ../other/adws/adw_data/sssf.db
# what VM runs used of Vercel's and E2B's free allowances
sandbox-usage *ARGS:
    uv run adws/adw_sandbox_usage.py {{ARGS}} --config {{config}}

# Checks each pid is still the process the trace recorded; pids get reused.
# stop a run, the chain first, then its agents: just kill <adw_id>
kill ADW_ID:
    uv run adws/adw_kill.py {{ADW_ID}} --config {{config}}

# ── SSSF itself ─────────────────────────────────────────────────────────────
# Refreshes .claude/skills/sssf from SSSF_SOURCE in .env, runs install.py
# --update, then runs it again to confirm nothing is left. It refuses to start
# while the tree has uncommitted changes, so the update gets its own commit, or
# while a recorded chain process is still alive. For a file it lists with `!`,
# run install.py --update --take PATH or --keep PATH yourself.

# The operator session in Podman, with --dangerously-skip-permissions; it needs
# container/, which install.py --container stamps. Pass -p '<request>' to run one
# request headless. It refuses inside a run.
# start the operator session in its container: just container
container *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    [ -z "${SSSF_ADW_ID:-}" ] || { echo "Refusing: container is the engineer's to run, outside an SSSF run (SSSF_ADW_ID is set)." >&2; exit 1; }
    [ -x container/run.sh ] || { echo "no container/run.sh here: stamp it with install.py --container (see the install guide)" >&2; exit 1; }
    exec container/run.sh "$@"

# For `local_guard: agentsh`. It runs agentsh_install.py from the SSSF hub, a clean
# checkout at this repo's skill version: HUB if given, else the repo that holds
# SSSF_SOURCE. It prints the sudo lines and never runs them. It refuses inside a run.
# write this repo's agentsh policy and print the sudo lines: just agentsh-install [HUB]
agentsh-install HUB="":
    #!/usr/bin/env bash
    set -euo pipefail
    [ -z "${SSSF_ADW_ID:-}" ] || { echo "Refusing: agentsh-install is the engineer's to run, outside an SSSF run (SSSF_ADW_ID is set)." >&2; exit 1; }
    hub="{{HUB}}"
    if [ -z "$hub" ]; then
        src="${SSSF_SOURCE:-}"
        [ -n "$src" ] || { echo "pass the hub's path, or set SSSF_SOURCE in .env to its skill folder" >&2; exit 1; }
        src="${src/#\~/$HOME}"
        hub="$(cd "${src%/}/../../.." && pwd)"
    fi
    common="$(git rev-parse --path-format=absolute --git-common-dir)"
    exec uv run --directory "${hub/#\~/$HOME}" .claude/skills/sssf/scripts/agentsh_install.py --repo "$(dirname "$common")"

# check that Claude Code agents can sign in, with the agents' own environment
login-check:
    @uv run --no-project -q --with pydantic --with pyyaml --with python-dotenv --with rich \
      python -c "import sys; sys.path.insert(0, 'adws'); from adw_modules import agent_cc; agent_cc.check_login(); print('login ok: Claude Code agents can sign in')"

# update SSSF from the skill source in SSSF_SOURCE
sssf-update:
    #!/usr/bin/env bash
    set -euo pipefail
    src="${SSSF_SOURCE:-}"
    if [ -z "$src" ]; then
        echo "set SSSF_SOURCE in .env to the skill's source dir, e.g. ~/github/super-simple-software-factory/.claude/skills/sssf" >&2
        exit 1
    fi
    src="${src/#\~/$HOME}"
    src="${src%/}"
    dest=.claude/skills/sssf
    if [ ! -f "$src/scripts/install.py" ]; then
        echo "SSSF_SOURCE=$src has no scripts/install.py" >&2
        exit 1
    fi
    if [ "$(cd "$src" && pwd -P)" = "$(cd "$dest" 2>/dev/null && pwd -P)" ]; then
        echo "SSSF_SOURCE points at this repo's own copy; point it at the skill's source" >&2
        exit 1
    fi
    # The source is a working tree, so it ships whatever branch it has checked
    # out. pi-agent-observability once got a hub commit that was not merged yet.
    if git -C "$src" rev-parse --verify -q main >/dev/null \
        && ! git -C "$src" merge-base --is-ancestor HEAD main \
        && [ -z "${SSSF_ALLOW_UNMERGED:-}" ]; then
        echo "the skill source is on $(git -C "$src" branch --show-current || echo a detached HEAD), with commits not on its main;" >&2
        echo "ask the hub to switch it back to main, or set SSSF_ALLOW_UNMERGED=1 to take them anyway" >&2
        exit 1
    fi
    if [ -n "$(git status --porcelain)" ]; then
        echo "commit or stash your changes first, so the update gets its own commit:" >&2
        git status --short >&2
        exit 1
    fi
    if [ -f {{db}} ]; then
        for pid in $(sqlite3 {{db}} "select pid from processes where ended_at is null and pid is not null" 2>/dev/null); do
            if kill -0 "$pid" 2>/dev/null; then
                echo "a chain is still running (pid $pid); update when it ends" >&2
                exit 1
            fi
        done
    fi
    # The excludes keep this repo's visualizer packages and built page, so a
    # running `just obs` keeps working.
    rsync -a --delete --exclude apps/visualizer/node_modules --exclude apps/visualizer/dist "$src/" "$dest/"
    find "$dest" -name __pycache__ -prune -exec rm -rf {} +
    rev=$(git -C "$src" log -1 --format=%h -- . 2>/dev/null || echo unknown)
    if [ -n "$(git -C "$src" status --porcelain -- . 2>/dev/null)" ]; then rev="$rev+uncommitted"; fi
    echo "skill copy refreshed from $src (skill $rev)"
    uv run "$dest/scripts/install.py" --update
    echo
    echo "second pass (expect updated: 0, added: 0):"
    uv run "$dest/scripts/install.py" --update | grep -E "updated:|added:|to review:"
    echo
    if [ -z "$(git status --porcelain)" ]; then
        echo "nothing to commit: this repo already matches skill $rev"
    else
        echo "next: read git status and git diff, then: git add -A && git commit -m \"Update SSSF to skill $rev\""
    fi

# After an update flags a file you changed that the skill changed too.
# take the skill's version of a flagged file: just take <path>
take PATH:
    uv run .claude/skills/sssf/scripts/install.py --update --take "$1"

# keep your version of a flagged file, recorded as reviewed: just keep <path>
keep PATH:
    uv run .claude/skills/sssf/scripts/install.py --update --keep "$1"

# ── observability UI ────────────────────────────────────────────────────────

# Needs bun. `obs` builds the UI, then serves UI and API from ONE process on
# the first free port from 4600 up, so several repos can each run their own at
# once. Set SSSF_OBS_PORT in .env to pin a port instead. The db path is passed
# explicitly because the server runs from the app dir and would otherwise look
# for a trace db sitting next to itself. Ctrl+C stops it.

# --backend=copyfile: bun hardlinks from ~/.bun/install/cache by default, which agentsh can't see through
# boot the trace UI; prints its address
obs:
    cd .claude/skills/sssf/apps/visualizer && bun install --backend=copyfile --silent && bunx vite build --logLevel warn && SSSF_DB={{justfile_directory()}}/{{db}} bun run server/index.ts

# install.py and --update record each repo in ~/.config/sssf/projects.json.
# one dashboard for every repo SSSF is installed in; prints its address
obs-all:
    cd .claude/skills/sssf/apps/visualizer && bun install --backend=copyfile --silent && bunx vite build --logLevel warn && bun run server/index.ts --all

# only for working on the visualizer itself: Vite dev server, fixed ports 4601 (ui) + 4600 (api)
obs-dev:
    cd .claude/skills/sssf/apps/visualizer && bun install --backend=copyfile && (SSSF_DB={{justfile_directory()}}/{{db}} SSSF_OBS_PORT=4600 bun run server/index.ts &) && bunx vite
