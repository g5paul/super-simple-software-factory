# SSSF in the hub

The hub runs SSSF on itself, so guard changes can go through a chain
(`docs/designs/hub-guard-chain.md`). The install sits on `main`: `adws/`,
`justfile`, `.env.sample` and the config under `adws/adw_sssf_config/` were
stamped from the skill in `.claude/skills/sssf/`.

## Ground rules

- `adws/` is main's guard. A run changes the skill in `.claude/skills/sssf/`,
  never `adws/`, and `adws/` is a protected path. The two meet only when an
  update lands on `main`.
- Runs cut their own branch from `main`, and commit locally. The engineer
  runs every push. A `forbid` rule with `on_match: refuse` refuses `git push`
  and the `gh` commands that write to GitHub, and the pre-push hook refuses
  a push while `SSSF_ADW_ID` is set.
- Agents bill to the Claude subscription login. `.env` holds no API key.

## Install

```bash
uv run .claude/skills/sssf/scripts/install.py --coding-agent claude_code
```

The Claude Code roster as shipped: planner, reviewer, standards_reviewer and
spec_reviewer on Opus; builder and ticket_builder on the default, Sonnet;
scout and documenter on Haiku.

`protected_files` adds the hub's own rules and records to the shipped list:
`.claude/agents/`, `CLAUDE.md`, `CLAUDE.local.md` and `docs/projects.md`.

`repo.yaml` sets `max_worktree_runs: 1`, since the suite's process-kill tests
are timing-sensitive, and `worktree_setup: []`, since the suite needs only
`uv`.

## Quality

One block. The hub has no lint, format, typecheck or build step.

| Block | argv |
|---|---|
| test | `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider` |

It takes about 75 seconds. The hub has no CI, so this is the whole check.

## Check the billing

Every agent's init event must show `apiKeySource` as `none`:

```bash
grep -h '"subtype":"init"' adws/adw_data/sessions/*/*/raw_output.jsonl \
  | python3 -c "import json,sys; print({json.loads(l)['apiKeySource'] for l in sys.stdin})"
```

## Run and watch

`just simple-sdlc path/to/task.md` runs the usual chain. `just sessions`,
`just phases <adw_id>` and `just obs` read the trace. The guard-change chain
isn't built yet.

## Update SSSF

`just sssf-update` refuses here, because the skill's source is this repo's
own copy. After a skill change merges to `main`, update on a short branch:

```bash
uv run .claude/skills/sssf/scripts/install.py --update
```

Then `--take` or `--keep` each flagged file, as in the install cookbook, and
fast-forward `main`. The stamp records no `skill` commit here, for the same
reason. `git log -1 -- adws/` names the update that stamped `adws/`.
