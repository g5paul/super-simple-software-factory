# Fix what the first real runs under agentsh found

Rollout step 2 of `docs/designs/agentsh-local-sandbox.md`. Source task: `specs/tasks/guard-agentsh-wrap-4.md`. The earlier tasks (`specs/tasks/guard-agentsh-wrap.md` to `-3.md`) still stand except where this changes them. The engineer's decisions in the design doc's "The first real runs" section are settled. Don't reopen them.

## Threat model

Each local Claude Code or pi agent runs as the engineer's own user under `agentsh wrap`, with a default-deny policy that SSSF generates. The agent can run any command, write any file in its workspace, and make links. It can also make SSSF on the host read files it wrote, such as `plan.md` in the handoff folder. SSSF reads those unwrapped, so a link there gets followed outside the policy. The guard only sees what the ptrace tracer reports. That's execve argv joined by spaces with `argv[0]` included, file operations on resolved paths, and connects. A `symlinkat` is different: the engine checks the link's target as the raw string the agent passed, with no resolving (`internal/ptrace/handle_file.go`, `SYS_SYMLINKAT`, fork at 41960715). At this level the guard can't stop:
- a process that leaves the wrap,
- a copied binary under a new name,
- what host-side tools do with files the agent left behind,
- quality checks, which run outside the wrap.

The text guard (`rules.py`, `sssf_refuse.py`) stays on and is unchanged.

## Baseline

`main` at `6530141`. Every finding is judged against that commit. The wrap itself merged as b5d0b82.

## Bar

Must do, against the baseline:
1. The five real Claude Code git argv lists in `docs/designs/agentsh/first-real-runs/claude-code-git-argv.json` are not denied by `sssf-no-push`. Every case the baseline denies stays denied, plus `git -c 'alias.p=!git push' p`. `git -C /x/push-tools status` runs.
2. On a `--worktree` run, a Claude Code agent can read the refuse script, `<main>/adws/adw_modules/sssf_refuse.py`.
3. A `null` events page means no rows. An HTTP error, a refused connection, invalid JSON, or a body that is not `null`, a list, or an object still counts as a failed poll.
4. A counted exec denial names its command from `fields.argv` when the event has no `command`, shortened. This shows in the RuleBreach message and in the trace.
5. On every run, agents can read and write `<main>/<data_dir>/sessions` and everything under it. The rule comes after every deny rule, so the earlier denies still win inside it: `.env` and other secret files, credentials, `/proc` secrets, the other agent's sign-in, `.git/hooks` and `.git/config`.
6. A relative symlink target with no `..` and no denied name, made in the workspace, is allowed. A link whose target names a denied file stays denied, whether the target is relative or absolute.

Must never do, against the baseline:
- keep an agent file,
- run agent code,
- delete or overwrite an engineer file,
- allow a path the baseline denies, beyond items 2, 5 and 6 above,
- let a run stop because the API answered normally.

A real Claude Code call that the policy denies, with no agent action behind it, is a false positive.

Accepted, not findings: the item-5 allow on every run's handoff folder, the relative-symlink limits named below, and every limit `config.md` already lists.

## Out of scope

These holes stay open. File them as old holes:
- Moving the handoff folder into the worktree, which is the stronger fix for item 5.
- pi under the wrap. That includes pi's `sssf_rules.ts` extension, which a `--worktree` pi run loads from the main checkout. The same failure as item 2 is likely there, but pi needs its own policy and a real run first.
- Running a real agent under the service, sudo, patching the agentsh fork, and turning this on outside sssf-sandbox.
- A link target with `..`, which the engine can't place because it doesn't resolve the target against the link's folder. It stays denied: see item 6.
- A link target that passes through a symlink already in the tree that leads out. The baseline has the same gap for absolute targets.
- Glob metacharacters in the main checkout's path, which `rule()` doesn't escape. The git dir already has this gap.
- The install script refusing a template that differs from the committed version. That's the chain run after step 2 (design doc, Next 4).
- Agents reading other runs' handoff files. The engineer accepted this.

## Findings that shaped the plan

- **`REFUSE_*` files.** No file by that name exists in the templates. `grep -rn "REFUSE_" .claude/skills/sssf/templates` finds only `SSSF_REFUSE_RULES`, an environment variable, and `REFUSE_SHELL`/`REFUSE_EXTENSION`, which are the script paths. So the only main-checkout paths a worktree Claude run needs are these two:
  - `<main>/adws/adw_modules/sssf_refuse.py`, which `agent_cc.REFUSE_SHELL` sets as `CLAUDE_CODE_SHELL_PREFIX`.
  - `<data_dir>/sessions/**`, which holds `context_handoff/`, `quality/` and so on.
  The main checkout's `.claude/` is already readable through `sssf-main-config`. Run ac34222b reached the plan gates with only those two missing, which confirms the list.
- **Relative symlinks (item 6).** In the fork at 41960715, `extractFileArgs` returns `(linkpath, target)` for `SYS_SYMLINKAT`, and the target is the raw string. `internal/api/ptrace_handlers.go` then runs `CheckFile(Path2, "symlink")` and merges the two decisions. `CheckFile` (`internal/policy/engine.go`) compiles every glob with separator `/` (gobwas). When no rule matches a write-class operation, it returns `default-deny-files`. A relative target like `testing` matches none of SSSF's rules, because they're all absolute, and the last one is `/**`. That explains the denial: the target is checked as a path of its own, as raw text. The policy can fix this for targets with no `..`. It can't for targets with `..`, because their meaning depends on the link's folder, which the engine doesn't use.
- **An old hole the same rule closes.** An absolute target holding `..`, like `${PROJECT_ROOT}/../../.ssh/id_rsa`, is matched as raw text. It hits `sssf-workspace` and is allowed on the baseline. The new `..` deny closes that.

## Files and changes

### `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

1. **Push rule (item 1).** In `COMMAND_HEAD`, change `sssf-no-push`'s `args_patterns` to exactly this text, as `docs/designs/agentsh/sssf-net.yaml` holds it:
   `['^\S*\s+push(\s|$)', '(?s)^\S*\s+-.*\spush(\s|$)', '(?s)^\S*\s+-.*\salias\.[^=\s]*=\S*push']`
   Remember the Python string escapes: the file writes `\\S` inside a normal string. Update the comment above it. `push` must now be a word of its own after a leading option, or appear in an alias's value. A hook name like `hook.pre-push.enabled` or a path like `/x/push-tools` no longer matches.

2. **Null page (item 3).** In `AgentshProcess._get`:
   - `data is None` returns `[]`.
   - A dict returns `data.get("events")`, and a `None` there also returns `[]`.
   - A list is used as is.
   - Anything else (str, int, float, bool) raises `ValueError("unexpected events body")`, so the poll counts as failed. Don't let a string iterate into an empty list silently.
   - If the rows value inside a dict isn't a list, also raise `ValueError`.
   - Keep the `isinstance(r, dict)` filter.

3. **Command from argv (item 4).** Add a module function `command_text(event: dict) -> str`:
   - Use `event["command"]` or `fields["command"]` when it is a non-empty string.
   - Otherwise take `fields["argv"]` (or `event["argv"]`) if it's a list of strings. Join it as `basename(argv[0])` followed by `argv[1:]`, separated by spaces.
   - Write `\n`, `\r` and `\t` inside the text as the two-character escapes.
   - If the result is longer than `COMMAND_CHARS = 160`, return the first word, then ` … `, then the last 140 characters cut forward to the next space. For the fixture event that gives `git … -c hook.post-index-change.enabled=false config user.name` or similar, which names the command.
   - Return `""` when there's nothing to use.

   In `_take`, use `command_text(event)` for `self.last` and for the `path` fallback in `other_denials`. The docstring on `last` says it holds the shortened command.

4. **File rules (items 2, 5, 6).** Change the signature to `file_rules(git_dir, agent, secret_files=(), *, sessions: str, refuse_script: str)`. Pass both through `policy_text(settings, git_common_dir, harness, name, *, sessions: str, refuse_script: str)`. They're required keyword arguments, so no caller gets a silent default. Add these rules:
   - `sssf-link-targets`, decision `deny`, operations `['symlink']`. Place it right after `sssf-git-config`, so it's the last deny before any allow. Its paths, in order:
     - `**..**` (any target holding `..`, absolute or relative),
     - the bare forms of every secret name: `.env`, `.env.*`, and for each repo `secret_files` glob the bare form (see below),
     - `.git`, `.git/**`, `**/.git`, `**/.git/**`.

     For each glob in `SECRETS` and each `secret_glob(p)` for the repo's `secret_files`, add a bare relative form. For `**/X`, add `X`; `**/X` itself is already denied for all ops by its own rule, and that covers `sub/X`. For a rooted `${PROJECT_ROOT}/.../last`, add the last segment `last` and `**/last`. A rooted secret is then denied as a link target by its name from any folder. That fails closed.

     Put the helper in a function `relative_secret_forms(globs) -> list[str]` and drop duplicates in order. Credentials, `/proc` secrets and sign-ins are absolute under `${HOME}` or `/proc`. A relative target reaches them only through `..` (denied) or an existing link (out of scope), and the raw absolute forms already hit their own deny rules.
   - `sssf-sessions`, decision `allow`, operations `ALL`, paths `[sessions, f"{sessions}/**"]`. Place it right after `sssf-git-data`, so it comes after every deny rule. Comment it: "A `--worktree` run's handoff folder sits in the main checkout. Every run's, decided 2026-11-09."
   - `sssf-refuse-script`, decision `allow`, operations `READ`, paths `[refuse_script]`. Place it right after `sssf-sessions`. Claude only: append it when `agent == "claude"`. pi's policy doesn't get it (out of scope).
   - `sssf-relative-links`, decision `allow`, operations `['symlink']`, paths `['[!/]**']`. A relative target is checked as raw text, so this matches any target that doesn't start with `/`. The `sssf-link-targets` denies above it decide which of those are refused. Place it right before `sssf-default-deny`.

5. **Paths in `read(cfg)`.**
   - `sessions`: `d = Path(cfg.defaults.data_dir)`. If `d` is absolute, `sessions = str((d / "sessions").resolve())`. Otherwise `sessions = str((root / d / "sessions").resolve())`, where `root` is the main checkout `read` already computes. Read `data_dir`, not `data_root`: `worktree.anchor` pins `data_root`, and `read` runs before it in `session.ensure`. `adw_agentsh_policy.py` runs after it, so `data_dir` gives the same text both times. Check that `data_dir` is the field `anchor` pins from (`worktree.py:87`).
   - `refuse_script`: `str(Path(__file__).resolve().with_name("sssf_refuse.py"))`. That's the same file as `agent_cc.REFUSE_SHELL`, resolved because the tracer reports resolved paths. Don't import `agent_cc`.

   Pass both to `policy_text`. The installed policy and `check_machine`'s comparison both come from `read`, so they agree.

### `.claude/skills/sssf/templates/adws/adw_agentsh_policy.py`

It calls `agentsh_guard.read(cfg)`, so no change should be needed. Confirm that nothing else calls `policy_text` or `file_rules` (grep the templates and tests). Update any caller.

### `docs/designs/agentsh/deny-policy.py`

`test_file_rules_equal_the_designs_deny_policy_output` compares its `file_rules:` section with ours. Add the same four rules in the same places. It takes `<git_dir> <out_dir>`. Derive `main = parent of git_dir`, `sessions = f"{main}/adws/adw_data/sessions"` and `refuse = f"{main}/adws/adw_modules/sssf_refuse.py"`. Emit the refuse rule for the claude policy only. The test passes the same two values to `policy_text`.

Regenerate `docs/designs/agentsh/sssf-dd-claude.yaml` and `sssf-dd-pi.yaml` only if they're the script's committed output for the `/m/proj/.git` example. Check how they were made before touching them, and leave them alone if they came from a real path.

### `.claude/skills/sssf/tests/fixtures/agentsh-first-real-runs/`

Copy the three files from `docs/designs/agentsh/first-real-runs/` byte for byte with `cp`, so the tests run where `docs/` isn't installed:
- `claude-code-git-argv.json`
- `events-page-past-end.json`
- `execve-denial-event.json`

### `.claude/skills/sssf/references/config.md`

In "Running local agents under agentsh":
- Replace the limit "A repo whose path holds `push` has every `git -C <path>` denied." with: "A `git -C` path, or any option value, holding `push` as a separate word after a space is denied. It fails closed."
- Keep the multi-line message limit, but say that the denied case is `push` as its own word.
- Add a paragraph on `--worktree` runs. Under agentsh, agents may read and write the main checkout's `<data_dir>/sessions/`, which holds every run's handoff folder. Claude Code agents may read `adws/adw_modules/sssf_refuse.py`. Secret files and git config stay denied inside the sessions folder.
- Add these limits:
  - "A symlink whose target holds `..` is denied, even inside the workspace, because agentsh checks a link's target as written and doesn't resolve it from the link's folder. A link whose target names a secret file by its name, such as `.env`, is denied from any folder. Both fail closed, and each counts as a file denial, not a refusal."
  - "A link target that passes through an existing symlink is judged by its text."
- Mention that a denied exec now names its command from argv, shortened.

### `docs/designs/agentsh-local-sandbox.md`

Under "The first real runs", add a short paragraph that records:
- the item-6 finding (the target is checked raw; relative targets without `..` are now allowed; `..` stays denied and why),
- the absolute-`..` hole the same rule closes,
- that no `REFUSE_*` files exist, so the main-checkout allows are the sessions folder and `sssf_refuse.py`.

In "What got built", replace the old push-case list for the real-service check with the new one, adding `git -C /x/push-tools status` (runs) and `git -c 'alias.p=!git push' p` (denied).

### Other docs

- The module docstring and comments in `agentsh_guard.py`, as above.
- No CHANGELOG exists for the skill templates. Check `ls .claude/skills/sssf/` and add an entry only if one is there.
- `app_docs/` is not edited. The documenter should note that any earlier write-up describing `sssf-no-push` as `-.*push`, or the agentsh file rules without the sessions allow, now describes old behaviour.

## Tests

All tests go in `.claude/skills/sssf/tests/test_agentsh_guard.py`. Build the config from the shipped `sssf.config.yaml` as the file already does, with no test-only config. Load the fixtures from `tests/fixtures/agentsh-first-real-runs/` with a `FIXTURES` path constant.

1. **The real argv lists.** Parametrize over the five lists in `claude-code-git-argv.json`. Assert `not push_denied(argv)`, using the existing `push_denied`, which joins with spaces and reads the patterns from the generated policy text. Add a second assertion that one of the lists contains `hook.pre-push.enabled=false`, so the fixture keeps the case that broke the baseline.
2. **Push cases.**
   - Keep every existing case in both lists. `git -c a=b commit -m push` stays denied, which fails closed.
   - Add to denied: `["git", "-c", "alias.p=!git push", "p"]`.
   - Add to runs: `["git", "-C", "/x/push-tools", "status"]` and `["git", "-c", "hook.pre-push.enabled=false", "status"]`.
   - Assert that the generated pattern list equals the three strings from `docs/designs/agentsh/sssf-net.yaml`'s `sssf-no-push`. Skip that one assertion when `docs/` isn't installed, as `DENY_POLICY` does.
3. **Null page.**
   - The `Service` fake returns the raw bytes of `events-page-past-end.json` (`null`) for a page past the end. Run a session for more than three poll intervals with `POLL_SECONDS` monkeypatched small, and assert `process.failure == ""` with no stop.
   - Also call `_get` directly against bodies `null`, `{"events": null}` and `[]` and assert `[]`.
   - Against bodies `"x"`, `42`, invalid JSON and an HTTP 500, assert it raises.
   - Three such failures in a row still set "stopped answering event polls". Extend the existing test.
4. **Exec event with argv only.** Feed `execve-denial-event.json`, which has `fields.argv`, `fields.rule`, `fields.decision` and no `command`, through `_take` three times with distinct ids. Assert:
   - `process.last[1]` starts with `git`, ends with `config user.name`, and is at most 160 characters plus the ellipsis,
   - the `RuleBreach` message from `breach()` contains `config user.name` and doesn't contain ``last `` `` (an empty name).
   Also unit-test `command_text` on a short argv (unchanged), on a `command` field (preferred), on an argv holding `\n` (escaped), and on nothing (`""`).
5. **File rules.** Write a small helper `first_match(text, path, op)` that parses the generated YAML and walks `file_rules` in order, using a gobwas-style glob translated to a Python regex:
   - `**` → `.*`, `*` → `[^/]*`, `?` → `[^/]`,
   - `[!x]` → `[^x]`, `{a,b}` → `(?:a|b)`, `\c` → literal `c`.

   It returns the rule name, or `default-deny-files` when nothing matches and the op isn't a read. Use it to assert, for the claude and pi policies built through `read(cfg)` on a temp git repo with `secret_files: ["*.pem", "./cfg/*.key"]`:
   - **Sessions.**
     - `<S>/x/context_handoff/plan.md` gets `sssf-sessions` for `write`, `create` and `read`.
     - `<S>/x/.env` and `<S>/x/.env.local` get `sssf-secret-files`.
     - `<S>/x/a.pem` gets `sssf-repo-secret-files`.
     - `${HOME}/.ssh/id_rsa` stays `sssf-credentials`.
     - The git dir's `hooks/pre-commit` and `config` get `sssf-git-config` for `write`.
     - `sssf-sessions` comes after every rule whose decision is `deny`, except `sssf-default-deny`.
   - **Refuse script.**
     - In the claude policy, `read` on the refuse script path gets `sssf-refuse-script`, and `write` on it doesn't (it falls to `sssf-default-deny` on a worktree layout).
     - The pi policy has no `sssf-refuse-script`.
   - **Symlinks (op `symlink`).**
     - Allowed: `testing`, `sub/dir/file`, `./x`.
     - `sssf-link-targets`: `../x`, `a/../../b`, `.env`, `sub/.env`, `.env.local`, `a.pem`, `x/a.pem`, `a.key`, `.git/hooks/pre-commit`, `/abs/proj/../../home/u/.ssh/id_rsa`.
     - Unchanged from the baseline: `/etc/passwd` → `sssf-default-deny`, and `${HOME}/.ssh/id_rsa` → `sssf-credentials`.
   - **Order.** Extend `test_rule_order_in_the_generated_policy`:
     - `sssf-link-targets` comes after `sssf-git-config` and before `sssf-workspace`.
     - `sssf-relative-links` is second to last.
     - `sssf-sessions` comes after `sssf-git-data`.
6. **Paths.**
   - `read(cfg)` on a temp repo gives a `sessions` equal to `<repo>/adws/adw_data/sessions` (resolved) for the shipped `data_dir`.
   - The same text comes back after `worktree.anchor(cfg, root)`, which proves the install script and `session.ensure` agree.
   - From inside a linked worktree of that repo, the path still points at the main checkout.
7. **Fixture copies.** When `docs/designs/agentsh/first-real-runs/` exists, each fixture copy is byte-equal to its source.

Update the callers of `policy_text` inside existing tests (`test_rule_order…`, `test_a_quote…`, `test_file_rules_equal…`, `push_patterns`) to pass `sessions=` and `refuse_script=`.

## Verification

- The repo's own test command passes. `adws/adw_modules/quality.py` runs `pytest tests` from `.claude/skills/sssf`, which collects `test_agentsh_guard.py`. Run it from that directory exactly that way, and run `uv run pytest tests/test_agentsh_guard.py -q` there for the focused run. Both exit 0 on a machine without the service: `test_live_service_is_active` skips as before.
- `grep -n "push(" .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` shows the three-pattern rule. `diff` its YAML-parsed patterns against `docs/designs/agentsh/sssf-net.yaml`'s `sssf-no-push`, as test 2 does.
- `python3 docs/designs/agentsh/deny-policy.py /m/proj/.git "$(mktemp -d)"` exits 0, and its output matches `policy_text` (test `test_file_rules_equal_the_designs_deny_policy_output`).
- No file under `adws/adw_modules/`, `adws/tests/`, `specs/` or `app_docs/` changed (`git status --porcelain`, read by the builder, not run as a check).

## For the engineer

These need you, outside the builder's reach:

1. Before the sudo install, check the gobwas semantics of the new globs on the fork's engine. The builder has no Go toolchain, so its test uses a Python emulation. Use `LoadFromFile` and `CheckFile` at 41960715 with separator `/`:
   - `[!/]**` matches `testing` and not `/etc/passwd`,
   - `**..**` matches `../x` and `/a/../b`,
   - `.env.*` matches `.env.local`.

   If `[!/]` doesn't parse as a negated class there, the allow fails closed (relative links stay denied). Record it in `config.md` as a limit rather than widening.
2. After `uv run adws/adw_agentsh_policy.py` and the sudo steps, run a `--worktree` chain and a plain chain in sssf-sandbox. Check the trace for these:
   - no `sssf-no-push` denial on Claude Code's own git calls,
   - no denial on `sssf_refuse.py`, its folder `adws/adw_modules`, or `adws/adw_data/sessions/<id>/`,
   - no "stopped answering event polls" on an idle agent,
   - the push cases in the design doc's "What got built" list.
3. If Python's start-up stats on `<main>/adws` or `<main>/adws/adw_modules` show up as denials that break the refuse prefix, widen `sssf-refuse-script` to `stat` on those two folders, through a chain run.
4. Still open from earlier specs: add `agentsh_guard.py` to `GUARD_MODULES` in `adws/adw_guard_change.py` and to `CLAUDE.md`'s guard code list, if that hasn't been done.
